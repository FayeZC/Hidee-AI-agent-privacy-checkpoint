"""Integration tests of the host boundary; scripted decisions are not model evals."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from hidee.core.gateway import PrivacyGateway
from hidee.core.models import Action, BackendDecision, BackendError, HostContext, HostPolicy


class RecordingBackend:
    def __init__(self, choices=None, failure=None):
        self.choices = iter(choices or ["allow"] * 20)
        self.failure = failure
        self.calls = []

    def decide(self, state, criteria):
        self.calls.append(copy.deepcopy(state))
        if self.failure is not None:
            raise self.failure
        choice = next(self.choices)
        labels = list(criteria)
        if choice not in labels:
            labels.append(choice)  # Deliberately invalid result for the unavailable-alternative test.
        return BackendDecision(choice=choice,
                               probabilities={d: 0.97 if d == choice else 0.03 / (len(labels) - 1) for d in labels},
                               confidence=0.97, model="scripted-test-only")


def context(**overrides):
    policy = HostPolicy(
        policy_id="synthetic-policy", version="1", instructions="Allow relevant professional queries only.",
        allowed_kinds=("search", "send_message", "store_memory"),
        recipient_fields={"search-service": ("query", "private_profile"),
                          "delivery-service": ("message",), "assistant-memory": ("memory",)},
        provider_fields=("query", "message", "memory"), max_checks=16, max_releases=8)
    fields = dict(task_id="task-1", version="1", task="Find a fictional professional resource.",
                  purpose="Professional learning", actor_id="actor-1", subject_id="subject-1",
                  policy=policy, provider_authorized=True, expires_at=1000.0)
    fields.update(overrides)
    return HostContext(**fields)


def action(action_id="action-1", **overrides):
    fields = dict(action_id=action_id, kind="search", recipient="search-service",
                  payload={"query": "Fictional public lecture information"})
    fields.update(overrides)
    return Action(**fields)


class GatewayIntegrationTests(unittest.TestCase):
    def gateway(self, backend=None, ctx=None, clock=None):
        return PrivacyGateway(backend or RecordingBackend(), ctx or context(), clock=clock or (lambda: 100.0))

    def test_executes_exact_authorized_action_once(self):
        gateway = self.gateway()
        candidate = action()
        review = gateway.evaluate(candidate)
        self.assertEqual(review.decision, "allow")
        executed = []
        self.assertEqual(gateway.execute(review.review_id, executed.append).status, "executed")
        self.assertEqual(executed, [candidate])
        self.assertEqual(gateway.execute(review.review_id, executed.append).status, "not_executed")
        self.assertEqual(len(executed), 1)

    def test_mutating_original_payload_cannot_change_grant(self):
        gateway = self.gateway()
        payload = {"query": "Permitted public professional topic"}
        review = gateway.evaluate(action(payload=payload))
        payload["query"] = "MUTATED_PRIVATE_HABIT"
        executed = []
        gateway.execute(review.review_id, executed.append)
        self.assertEqual(executed[0].payload, {"query": "Permitted public professional topic"})
        self.assertNotIn("MUTATED_PRIVATE_HABIT", json.dumps(gateway.history()))

    def test_minimal_alternative_is_exact_and_snapshotted(self):
        gateway = self.gateway(RecordingBackend(["use_minimal_alternative"]))
        candidate = action("broad", kind="send_message", recipient="delivery-service",
                           payload={"message": "Delivery instructions and an irrelevant daily routine"})
        replacement = action("minimal", kind="send_message", recipient="delivery-service",
                             payload={"message": "Leave the parcel with reception"})
        review = gateway.evaluate(candidate, minimal_alternative=replacement)
        self.assertEqual(review.decision, "use_minimal_alternative")
        replacement.payload["message"] = "MUTATED_AFTER_REVIEW"
        executed = []
        self.assertEqual(gateway.execute(review.review_id, executed.append).status, "executed")
        self.assertEqual(executed[0].action_id, "minimal")
        self.assertEqual(executed[0].payload["message"], "Leave the parcel with reception")

    def test_context_change_invalidates_pending_grant(self):
        initial = context()
        gateway = self.gateway(ctx=initial)
        review = gateway.evaluate(action())
        gateway.update_context(replace(initial, version="2"))
        executed = []
        self.assertEqual(gateway.execute(review.review_id, executed.append).status, "not_executed")
        self.assertEqual(executed, [])

    def test_a_later_release_invalidates_an_earlier_pending_grant(self):
        gateway = self.gateway()
        first = gateway.evaluate(action("first"))
        stale = gateway.evaluate(action("stale"))
        executed = []
        self.assertEqual(gateway.execute(first.review_id, executed.append).status, "executed")
        self.assertEqual(gateway.execute(stale.review_id, executed.append).status, "not_executed")
        self.assertEqual([a.action_id for a in executed], ["first"])

    def test_unavailable_alternative_cannot_be_executed(self):
        gateway = self.gateway(RecordingBackend(["use_minimal_alternative"]))
        review = gateway.evaluate(action())
        self.assertNotIn(review.decision, ("allow", "use_minimal_alternative"))
        self.assertIsNone(review.review_id)
        self.assertEqual(gateway.history(), [])

    def test_hard_recipient_denial_precedes_a_fake_allow(self):
        backend = RecordingBackend(["allow"])
        gateway = self.gateway(backend)
        review = gateway.evaluate(action(recipient="unapproved-recipient"))
        self.assertEqual(review.decision, "block")
        self.assertIsNone(review.review_id)
        self.assertEqual(backend.calls, [])

    def test_provider_field_denial_happens_before_backend_call(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend)
        # The recipient permits this key, but the decision provider does not.
        review = gateway.evaluate(action(payload={"private_profile": "PRIVATE_PROVIDER_DENIED"}))
        self.assertEqual(review.decision, "block")
        self.assertEqual(backend.calls, [])
        self.assertIsNone(review.review_id)

    def test_lone_surrogate_candidate_is_rejected_before_backend(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend)
        review = gateway.evaluate(action(payload={"query": "Invalid Unicode " + chr(0xD800)}))
        self.assertEqual(review.decision, "block")
        self.assertIsNone(review.review_id)
        self.assertEqual(backend.calls, [])
        self.assertEqual(gateway.history(), [])

    def test_nested_nonstring_key_is_rejected_without_coercion(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend)
        review = gateway.evaluate(action(payload={"query": {1: "Must not silently become a string key"}}))
        self.assertEqual(review.decision, "block")
        self.assertIsNone(review.review_id)
        self.assertEqual(backend.calls, [])
        self.assertEqual(gateway.history(), [])

    def test_provider_field_denial_covers_the_supplied_alternative(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend)
        review = gateway.evaluate(action("original"),
                                  minimal_alternative=action("alternative", payload={"private_profile": "DENIED"}))
        self.assertEqual(review.decision, "block")
        self.assertEqual(backend.calls, [])

    def test_backend_failure_cannot_create_an_execution_grant(self):
        backend = RecordingBackend(failure=BackendError("sanitized unavailable backend"))
        gateway = self.gateway(backend)
        review = gateway.evaluate(action())
        self.assertEqual(review.decision, "block")
        self.assertIsNone(review.review_id)
        self.assertEqual(gateway.history(), [])
        self.assertEqual(len(backend.calls), 1)

    def test_high_confidence_does_not_override_low_selected_probability(self):
        class LowSupportBackend(RecordingBackend):
            def decide(self, state, criteria):
                result = super().decide(state, criteria)
                probabilities = {label: 0.51 if label == "allow" else 0.49 / (len(criteria) - 1)
                                 for label in criteria}
                return replace(result, probabilities=probabilities, confidence=0.999)

        gateway = self.gateway(LowSupportBackend())
        review = gateway.evaluate(action())
        self.assertEqual(review.decision, "request_verified_context")
        self.assertIsNone(review.review_id)
        self.assertEqual(gateway.history(), [])

    def test_selected_probability_not_confidence_controls_operational_floor(self):
        class LowConfidenceBackend(RecordingBackend):
            def decide(self, state, criteria):
                return replace(super().decide(state, criteria), confidence=0.01)

        gateway = self.gateway(LowConfidenceBackend())
        review = gateway.evaluate(action())
        self.assertEqual(review.decision, "allow")
        self.assertIsNotNone(review.review_id)

    def test_released_history_is_passed_forward_but_pending_reviews_are_not(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend)
        first = gateway.evaluate(action("first", payload={"query": "First released topic"}))
        self.assertEqual(backend.calls[0]["prior_releases"], [])
        gateway.evaluate(action("pending"))
        self.assertEqual(backend.calls[1]["prior_releases"], [])
        gateway.execute(first.review_id, lambda _: None)
        gateway.evaluate(action("second"))
        self.assertEqual(len(backend.calls[2]["prior_releases"]), 1)
        self.assertEqual(backend.calls[2]["prior_releases"][0]["payload"], {"query": "First released topic"})
        history = gateway.history()
        history[0]["payload"]["query"] = "CALLER_MUTATION"
        self.assertNotIn("CALLER_MUTATION", json.dumps(gateway.history()))

    def test_prior_releases_are_recipient_scoped(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend)
        first = gateway.evaluate(action("first"))
        gateway.execute(first.review_id, lambda _: None)
        gateway.evaluate(action("delivery", kind="send_message", recipient="delivery-service",
                                payload={"message": "A permitted delivery note"}))
        self.assertEqual(backend.calls[-1]["prior_releases"], [])
        self.assertEqual(len(gateway.history()), 1)

    def test_executor_failure_is_conservatively_recorded_and_not_retried(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend)
        review = gateway.evaluate(action())
        attempts = []

        def uncertain_executor(candidate):
            attempts.append(candidate)
            raise RuntimeError("May have sent before losing acknowledgement")

        self.assertEqual(gateway.execute(review.review_id, uncertain_executor).status, "execution_uncertain")
        self.assertEqual(gateway.execute(review.review_id, uncertain_executor).status, "not_executed")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(gateway.history()), 1)
        self.assertEqual(gateway.history()[0]["status"], "possibly_released")
        gateway.evaluate(action("next"))
        self.assertEqual(backend.calls[-1]["prior_releases"][0]["status"], "possibly_released")

    def test_expired_context_is_blocked_before_backend(self):
        backend = RecordingBackend()
        gateway = self.gateway(backend, context(expires_at=99.0))
        self.assertEqual(gateway.evaluate(action()).decision, "block")
        self.assertEqual(backend.calls, [])

    def test_authorization_expiring_after_review_prevents_execution(self):
        current_time = [100.0]
        gateway = self.gateway(ctx=context(expires_at=101.0), clock=lambda: current_time[0])
        review = gateway.evaluate(action())
        current_time[0] = 102.0
        executed = []
        self.assertEqual(gateway.execute(review.review_id, executed.append).status, "not_executed")
        self.assertEqual(executed, [])

    def test_check_budget_prevents_additional_backend_calls(self):
        backend = RecordingBackend()
        initial = context()
        gateway = self.gateway(backend, replace(initial, policy=replace(initial.policy, max_checks=1)))
        self.assertEqual(gateway.evaluate(action("first")).decision, "allow")
        self.assertEqual(gateway.evaluate(action("second")).decision, "block")
        self.assertEqual(len(backend.calls), 1)

    def test_release_budget_prevents_additional_backend_calls(self):
        backend = RecordingBackend()
        initial = context()
        gateway = self.gateway(backend, replace(initial, policy=replace(initial.policy, max_releases=1)))
        review = gateway.evaluate(action("first"))
        gateway.execute(review.review_id, lambda _: None)
        self.assertEqual(gateway.evaluate(action("second")).decision, "block")
        self.assertEqual(len(backend.calls), 1)

    def test_context_size_and_provider_denial_precede_backend_calls(self):
        for initial in (context(provider_authorized=False),
                        replace(context(), policy=replace(context().policy, max_context_bytes=1))):
            with self.subTest(provider_authorized=initial.provider_authorized):
                backend = RecordingBackend()
                gateway = self.gateway(backend, initial)
                self.assertEqual(gateway.evaluate(action()).decision, "block")
                self.assertEqual(backend.calls, [])

    def test_public_review_and_audit_do_not_contain_payload_or_probabilities(self):
        gateway = self.gateway()
        review = gateway.evaluate(action(payload={"query": "SYNTHETIC_PRIVATE_AUDIT_CANARY"}))
        gateway.execute(review.review_id, lambda _: None)
        public = json.dumps(review.public())
        audit = json.dumps(gateway.audit())
        for text in (public, audit):
            self.assertNotIn("SYNTHETIC_PRIVATE_AUDIT_CANARY", text)
            self.assertNotIn("probabilities", text)
            self.assertNotIn("confidence", text)


if __name__ == "__main__":
    unittest.main()
