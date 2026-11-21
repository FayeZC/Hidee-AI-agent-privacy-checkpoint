# Security policy

Hidee is a privacy checkpoint, so a way around it is a security problem even when nothing crashes.

## Reporting a problem

Please report it privately through GitHub's
[private vulnerability reporting](https://github.com/FayeZC/Hidee-AI-agent-privacy-checkpoint/security/advisories/new), not in a public issue.
Include what you did, what Hidee let through or exposed, and the agent, OS, and Hidee version. We will reply
within a week and keep you informed until it is fixed.

## In scope

- A tool call that reaches an outside service without passing the checkpoint, or runs content other than what
  the person approved (for example, a reused or changed one-use grant).
- A way for the agent to read or change Hidee's records, rules, or decisions, or to break the evidence log's hash
  chain without `hidee evidence verify` noticing.
- Hidee sending anything to a host the session did not ask for, or its data becoming readable by other users.
- Safe mode letting the agent load the user's own hooks, plugins, or MCP servers where the docs say it does not.

## Known limits

Some gaps are documented rather than fixed; they are listed under "Limits" in
[docs/PRIVACY_AND_TRUST.md](docs/PRIVACY_AND_TRUST.md) and [docs/AUDITABLE_EVIDENCE.md](docs/AUDITABLE_EVIDENCE.md).
Detection is pattern-based, so a detector missing some personal data is a bug to report as an ordinary issue,
unless it lets a whole class of data through.

## Supported versions

Only the latest release and `main` get security fixes.
