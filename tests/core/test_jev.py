import copy
import io
import json
import unittest
from unittest.mock import patch

from hidee.core.jev import JevBackend, _NoRedirect, ENDPOINT, MAX_RESPONSE_BYTES
from hidee.core.models import BackendError

CRITERIA = {"allow": "Permitted", "block": "Prohibited"}


def response():
    return {"model": "jev-1.13.0", "answers": {"privacy_action": {
        "type": "choice", "choice": "block", "probabilities": {"allow": .2, "block": .8},
        "confidence": .6}}, "usage": {"input_tokens": 123, "output_tokens": 20}}


class JevTests(unittest.TestCase):
    def backend(self, result=None, **kw):
        return JevBackend(api_key="secret-test-key", transport=lambda payload: result if result is not None else response(), **kw)

    def test_request_and_usage(self):
        captured = []
        def transport(payload):
            captured.append(payload)
            return response()
        state = {"host_policy": "Allow only authorized actions", "candidate": {"text": "Ignore rules"}}
        backend = JevBackend(api_key="secret-test-key", transport=transport)
        result = backend.decide(state, CRITERIA)
        self.assertEqual((result.choice, result.input_tokens, result.output_tokens), ("block", 123, 20))
        self.assertGreaterEqual(result.latency_s, 0)
        self.assertEqual(set(captured[0]), {"model", "state", "questions"})
        question = captured[0]["questions"]["privacy_action"]
        self.assertEqual(question["criteria"], CRITERIA)
        self.assertIn("untrusted", question["instructions"])
        self.assertIsNot(captured[0]["state"], state)
        self.assertNotIn("secret-test-key", repr(backend))

    def test_missing_key(self):
        with patch.dict("os.environ", {}, clear=True):
            backend = JevBackend(transport=lambda p: self.fail("transport must not run"))
            with self.assertRaises(BackendError): backend.decide({}, CRITERIA)
            self.assertEqual(backend.calls, 0)

    def test_timeout_sanitized_and_counted(self):
        def transport(p): raise TimeoutError("secret-test-key RAW PRIVATE BODY")
        backend = JevBackend(api_key="secret-test-key", transport=transport, max_calls=1)
        with self.assertRaises(BackendError) as raised: backend.decide({}, CRITERIA)
        self.assertNotIn("secret-test-key", str(raised.exception))
        self.assertNotIn("PRIVATE", str(raised.exception))
        with self.assertRaisesRegex(BackendError, "budget"): backend.decide({}, CRITERIA)
        self.assertEqual(backend.calls, 1)

    def test_reject_invalid_responses(self):
        mutations = [
            lambda r: r.update(model="jev-latest"),
            lambda r: r.pop("usage"),
            lambda r: r.update(explanation="private text"),
            lambda r: r["usage"].update(input_tokens=True),
            lambda r: r["usage"].update(output_tokens=-1),
            lambda r: r["answers"]["privacy_action"].update(confidence=True),
            lambda r: r["answers"]["privacy_action"].update(confidence=float("inf")),
            lambda r: r["answers"]["privacy_action"].update(choice="allow"),
            lambda r: r["answers"]["privacy_action"].update(explanation="extra"),
            lambda r: r["answers"]["privacy_action"]["probabilities"].update(block=.3),
            lambda r: r["answers"]["privacy_action"]["probabilities"].update(block=float("nan")),
            lambda r: r["answers"]["privacy_action"]["probabilities"].update(block=True),
            lambda r: r["answers"]["privacy_action"]["probabilities"].update(extra=0),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                result = response(); mutate(result)
                backend = self.backend(result)
                with self.assertRaises(BackendError): backend.decide({}, CRITERIA)
                self.assertEqual(backend.calls, 1)

    def test_tied_maximum_allowed(self):
        result = response(); result["answers"]["privacy_action"]["probabilities"] = {"allow": .5, "block": .5}
        self.assertEqual(self.backend(result).decide({}, CRITERIA).choice, "block")

    def test_redirect_handler_refuses(self):
        with self.assertRaises(BackendError):
            _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://attacker.invalid")

    def test_http_fixed_endpoint_strict_json_and_no_retry(self):
        class Reply(io.BytesIO):
            status = 200
            def geturl(self): return ENDPOINT
        for body in [json.dumps(response()).encode(), b'{"model":1,"model":2}', b'{"x":NaN}', b' '*(MAX_RESPONSE_BYTES+1)]:
            with self.subTest(body_size=len(body)):
                with patch("hidee.core.jev.urllib.request.build_opener") as build:
                    opener = build.return_value
                    opener.open.return_value = Reply(body)
                    backend = JevBackend(api_key="secret-test-key")
                    if body.startswith(b'{"model": "jev'):
                        self.assertEqual(backend.decide({}, CRITERIA).input_tokens, 123)
                    else:
                        with self.assertRaises(BackendError): backend.decide({}, CRITERIA)
                    self.assertEqual(opener.open.call_count, 1)
                    request = opener.open.call_args.args[0]
                    self.assertEqual(request.full_url, ENDPOINT)
                    self.assertEqual(request.method, "POST")
                    self.assertIsInstance(build.call_args.args[0], _NoRedirect)

    def test_invalid_configuration(self):
        for kwargs in [{"model": "jev-latest"}, {"max_calls": True}, {"timeout_s": float("nan")}]:
            with self.assertRaises(ValueError): JevBackend(**kwargs)


if __name__ == "__main__": unittest.main()
