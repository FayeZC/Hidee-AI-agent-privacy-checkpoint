# Contributing to Hidee

Thank you for helping. Hidee sits between an AI agent and the outside world, so a change here can decide
whether someone's data leaves their computer. Please read this page before opening a pull request.

## Set up and run the tests

```bash
git clone https://github.com/FayeZC/Hidee-AI-agent-privacy-checkpoint.git && cd Hidee-AI-agent-privacy-checkpoint
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q tests
```

The suite needs no network, no API key, and no installed agent. It runs on macOS and Linux with Python 3.11 or
newer, and CI runs it on both. To try the app without touching your own data, use a separate home folder:

```bash
.venv/bin/hidee app --home /tmp/hidee-dev --port 8771 --no-browser
```

[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) explains the project layout, the other entry points, and how a release
is built. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) follows one tool call from the agent to the evidence log.

## What a good change looks like

- **It comes with a test.** New behavior gets a test that fails without it; a bug fix gets a test that shows the
  bug.
- **It keeps Hidee's promises.** Hidee sends nothing to its makers, adds no new network destination, and keeps
  its own data readable only by the user. `tests/test_privacy.py` checks part of this; please do not weaken it.
  See [docs/PRIVACY_AND_TRUST.md](docs/PRIVACY_AND_TRUST.md).
- **It fails closed where Hidee decides.** The checking hook blocks on any error. The watching hook and the learn
  timeline never block a session.
- **Every agent goes through one check.** Real hooks and scripted sessions both use `hook_api.check_call`; a new
  agent is translated to Claude Code's hook dialect first (see [docs/HOOK_PROTOCOL.md](docs/HOOK_PROTOCOL.md)).
- **Hook scripts stay standard-library only**, because they run before every tool call.
- **Product text is plain English** a non-expert can read. Code comments explain why, not what.

## Pull requests

Keep each pull request to one change, describe how you tested it, and update the docs when users or reviewers
would notice the difference. For a larger change, such as a new agent, a new detector category, or a change to
the evidence record format, please open an issue first so we can agree on the design.

## Reporting problems

Use the issue templates for bugs and ideas. Do not paste API keys, customer data, or evidence logs that hold
personal data. Report security problems privately as described in [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contribution is licensed under the Apache License 2.0, the license of this
project.
