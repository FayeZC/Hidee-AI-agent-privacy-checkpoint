# Hidee architecture

This document describes how Hidee is built: its components, how one tool call travels from the agent through
Hidee and back, the network gate, safe mode, the learn-mode timeline, and the evidence log. File names refer to the
`hidee/` package.

## 1. Overview

Hidee is one Python process (FastAPI on uvicorn) that runs on the user's computer and listens on `127.0.0.1`. In
app mode (`hidee app`) it does four jobs:

1. It starts agent sessions in pseudo-terminals and shows them in the browser (`sessions.py`, `workspace.py`).
2. It answers the agents' hooks: every tool call is mapped to an action, checked against the rules, and, if the
   rules cannot decide, held for a person (`hook_api.py`, `toolmap.py`, `detect.py`, `rules.py`, `engine.py`).
3. It runs a local HTTP proxy, the network gate, that every session's connections go through (`gate.py`).
4. It writes a hash-chained evidence log of every session (`evidence.py`).

The same engine also serves the older agent HTTP API (`hidee serve`, `api.py`), the key-free demo (`hidee demo`,
`demo.py`), and the online demo (`hidee web`, `online.py`, `website.py`).

```
 Browser (127.0.0.1:8770)                                    Outside world
 ┌──────────────────────────────┐                     ┌────────────────────────┐
 │ Workspace  What left         │                     │ web search, sites,     │
 │ Evidence   Settings          │                     │ MCP servers, mail      │
 └──────┬──────────────▲────────┘                     │ model provider         │
   WebSocket /term     │ decide (POST, CSRF)          └───────────▲────────────┘
        │              │                                          │
 ┌──────▼──────────────┴─────────────────────────────────────┐    │
 │ Hidee process                                             │    │
 │                                                           │    │
 │  SessionManager ──PTY──► agent (claude | gemini | codex)  │    │
 │                            │ hook (stdin JSON)            │    │
 │                            ▼                              │    │
 │  /hook/pretooluse ─► toolmap + detect ─► Engine ─► rules  │    │
 │        ▲                                   │   (allow,    │    │
 │        │ reply: allow / deny /             │   review,    │    │
 │        │ shorter version                   │   block)     │    │
 │        └───────────────────────────────────┤              │    │
 │                                  waiting ──► Checkpoint   │    │
 │                                                           │    │
 │  Gate (HTTP proxy) ◄── HTTP(S)_PROXY of every session ────┼────┘
 │       │ check host by rules, record connection            │
 │       ▼                                                   │
 │  EvidenceLog ──► ~/.hidee/app/evidence/YYYY-MM-DD.jsonl   │
 │  Store (SQLite) ─► ~/.hidee/app/hidee.db                  │
 └───────────────────────────────────────────────────────────┘
```

## 2. Components

| Module | Role |
| :- | :- |
| `cli.py` | Command line: `app`, `serve`, `demo`, `web`, `cloud-demo`, `evidence verify|export`, `agent add`. Writes the starter rules on first run. |
| `app.py` | `create_app`: builds the store, rules loader (reloads `rules.yaml` when its mtime changes), engine, executors, and routers. In app mode it adds the session manager, network gate, activity feed, learn timeline, withheld memory, evidence log, key store, and `TrustedHostMiddleware`. |
| `sessions.py` | `SessionManager`: starts each session on its own PTY, writes the per-session hook settings, chooses the launch command per agent, applies safe mode, sets the proxy environment, keeps 256 KB of scrollback, and reports session start and end to the evidence log. |
| `hook.py` | The checking hook script (`python -m hidee.hook`). Standard library only. Posts the call to Hidee and prints the reply; fails closed. With `--after` it is Gemini's AfterTool note hook. |
| `learn_hook.py` | The watching hook script (`python -m hidee.learn_hook`). Posts prompts, results, failures, and answers; always exits 0. |
| `hook_api.py` | Hook endpoints, `check_call` (the full check of one tool call), the reply builder, evidence for turns and decisions, the learn routes, and the default app rules `APP_RULES`. |
| `dialects.py` | Translates Gemini CLI tool calls, replies, and lifecycle events to and from Claude Code's format. |
| `toolmap.py` | Maps a tool call to an action (kind, recipient, fields, shorter version), reads scripts a command runs, and finds local overreach. |
| `detect.py` | Pattern detectors for sensitive information, redaction, category labels, and risk groups. |
| `rules.py` | Rules file parsing, validation, wildcard matching, and evaluation (allow, review, block). |
| `engine.py` | Submits actions, applies the rule outcome, holds actions for review, expires them, and executes approvals through one-use grants bound to a digest of the approved content. |
| `store.py` | SQLite tables for agents, actions, grants, disclosures, and the audit log. |
| `executors.py` | What runs after approval. In app mode `PassThrough`: approval is the effect, because the agent runs the tool. |
| `gate.py` | The network gate: an asyncio HTTP proxy with per-session credentials and per-session ports. |
| `withheld.py` | Per-session memory of held-back details; matches disguised retries. |
| `learn.py` | The learn-mode timeline per session, plain explanations, and token usage from Claude Code transcripts. |
| `evidence.py` | Evidence writer, hash chain, verify, export, bundle verify, session summary. |
| `keys.py` | API keys in the macOS keychain or a 0600 file, key checks, and the launch prefix that hands a key to one session. |
| `live.py` | In-memory activity feed for the live flow map (300 events). |
| `workspace.py` | Workspace page, session create and close, terminal WebSocket, Settings, folder picker, Evidence page and export. |
| `web.py` | Review cards, confirmation, decisions, What left (map and Who knows what), step pop-ups. |
| `flow.py` | SVG drawings for the dashboard map and step pictures. |
| `api.py`, `agents.py` | Agent HTTP API with per-agent bearer tokens (`hidee serve`). |
| `demo.py`, `online.py`, `website.py` | Key-free demo, online demo with scripted sessions and per-visitor sandboxes, and the product site. |
| `core/` | Privacy gateway: policy checks, one-use grants, and disclosure history; the engine uses its canonical `digest`. |

## 3. Starting a session

`POST /sessions` (with the CSRF token) calls `SessionManager.create`:

1. A session id (8 hex characters) and a folder `~/.hidee/app/sessions/<id>/`.
2. `settings.json`: Claude Code hook settings (and the safe-mode sandbox block, if safe).
3. For Gemini, three system settings files: `gemini-default.json`, `gemini-vertex.json` (selects `vertex-ai`
   sign-in), and `gemini-key.json` (selects `gemini-api-key`). The launch command picks one after the user's shell
   profile has run. For a safe Gemini session also `gemini.sb`, the Seatbelt profile.
4. The launch command. Agents start through the user's login shell (`$SHELL -l -i -c "... exec <agent> ..."`) so
   the user's own sign-in applies. A saved key is passed as `HIDEE_KEY` in the environment and moved into the
   provider's variable by a shell prefix just before `exec`.
5. The environment: the parent environment without Claude Code's own session markers (`CLAUDECODE`,
   `CLAUDE_CODE_SESSION_ID`, and similar), plus `HIDEE_URL`, `HIDEE_TOKEN` (the per-run hook token),
   `HIDEE_SESSION`, `HIDEE_AGENT` (`gemini` or `codex`), and the proxy variables `HTTP_PROXY`, `HTTPS_PROXY`
   (both cases), `NO_PROXY=127.0.0.1,localhost,::1`, and `NODE_USE_ENV_PROXY=1`.
6. `pty.openpty` plus `subprocess.Popen` with a new process group. A reader thread copies output into the
   scrollback and to connected browsers. `meta.json` records name, folder, agent, learn, safe, and start time.
7. An evidence record `session_start`. When the process exits, `session_end` with the exit code.

The browser connects to `/term/{id}` over a WebSocket. Hidee accepts it only with the per-run key and an `Origin`
that matches the `Host`, because a WebSocket to a local shell would otherwise be a remote shell for any web page.

## 4. One tool call, end to end

1. **Agent.** The model chooses a tool. Before it runs, the agent runs Hidee's checking hook
   (`PreToolUse` for Claude Code and Codex CLI, `BeforeTool` for Gemini CLI) with the call as JSON on stdin.
2. **Hook script.** `hidee.hook` posts the JSON to `POST /hook/pretooluse` on `HIDEE_URL`, with the headers
   `X-Hidee-Token`, `X-Hidee-Session`, and `X-Hidee-Agent`. It bypasses proxy settings so it always reaches Hidee
   directly. It waits up to 650 seconds.
3. **Endpoint.** Hidee checks the token (401 if wrong). For Codex, a `Bash` command given as an argv list is
   turned into a script string. For Gemini, `dialects.gemini_call` translates the tool name and input. If the
   session is in learn mode, the call is added to the timeline.
4. **Evidence.** A `tool_call` record: the tool, a digest of the input, and whether it goes outside.
5. **Mapping.** `toolmap.map_call` decides whether the call is outbound:

   | Tool call | Kind | Recipient |
   | :- | :- | :- |
   | `WebSearch` | `search` | `web-search` |
   | `WebFetch` | `fetch` | the URL's host |
   | `mcp__<server>__<tool>` | `use_tool` | `mcp:<server>` |
   | `Bash` with a mail program (`mail`, `sendmail`, `msmtp`, `mutt`, …) or `osascript` driving Mail, Messages, or Outlook | `send_message` | the address in the command, or `mail-app` |
   | `Bash` with a network program (`curl`, `wget`, `ssh`, `scp`, `rsync`, `nc`, `gh`, `aws`, `gcloud`, …), a network `git` subcommand, `open <url>`, or an interpreter whose inline code uses a network library | `network` | the first host in the command |
   | `Bash` that runs a script file (`python3 x.py`, `bash x.sh`, `./x.sh`) whose text goes out | `network` | the first host in the script |

   Calls to `localhost`, `127.0.0.1`, `::1`, or `0.0.0.0` stay local. Each string in the outbound part of the
   input becomes a field with category `plain`; each detector finding becomes its own field with its category
   (the destination itself, such as the address a message goes to, is not a finding). The shorter version keeps
   the plain fields with findings replaced (`[email]` for searches and MCP calls; `REDACTED_EMAIL` for fetches,
   network commands, and messages), except findings in destination fields, which stay so the call still reaches its
   recipient: a structured input's `to`, `cc`, `bcc`, `recipient(s)`, `email`, `address`, `channel` (and similar
   keys), or the same keys written just before the finding in a text input such as a curl body (`toolmap.RECIPIENT_KEYS`,
   `toolmap.RECIPIENT_CONTEXT`). If every finding is a destination, all are replaced. Script text is added as fields too, and a call that runs a script gets no
   shorter version.
6. **Withheld memory.** For an outbound call, Hidee compares the input with the details held back earlier in the
   same session. A match is denied at once, recorded as a `decision` by `hidee`, and the agent is told to ask the
   user.
7. **Local overreach.** If the call is not outbound, `toolmap.map_access` checks file tools and shell commands for
   credential files, privileged commands (`sudo`, `chmod`, `launchctl`, `security`, …), and paths outside the
   session folder. A match becomes kind `access` to `this-computer`. Anything else is ordinary local work: it is
   drawn on the map and gets an empty reply.
8. **Engine.** `Engine.submit("session:<id>", kind, recipient, fields, minimal)` evaluates the rules
   ([RULES.md](RULES.md)). `block` sets status `blocked`. `allow` executes at once (`allowed`, decided by `rules`).
   `review` sets status `waiting` with a deadline of 600 seconds, and the card appears in the checkpoint.
9. **Person.** The checkpoint pane embeds the review page (`/?embed=1`). A choice goes through the confirmation
   page to `POST /decide/{id}`, which calls `Engine.decide(..., decided_by="owner")`. Approve and shorter version
   create a one-use grant bound to the SHA-256 digest of the approved fields, valid for 60 seconds, and execute it.
   Executing records the disclosure (recipient and categories) before the effect, which feeds Who knows what.
10. **Wait.** `check_call` polls the action every 0.25 seconds until it leaves `waiting` or the review timeout
    plus 5 seconds passes. Expired actions become `timed_out`.
11. **Evidence and memory.** A `decision` record (rules or person, choice, reason, seconds), and for a shorter
    version an `executed` record with the input that actually ran. Held-back values of a shorter, rejected, or
    blocked outbound call go into withheld memory.
12. **Reply.** `hook_api.reply` builds a Claude Code reply; for Gemini, `dialects.gemini_reply` translates it
    ([HOOK_PROTOCOL.md](HOOK_PROTOCOL.md)):
    - allowed by rules: `{}`, so the agent's own permission flow still applies;
    - approved by a person: `allow`;
    - shorter version: `allow` with `updatedInput` and a note for the model;
    - blocked, rejected, timed out, or withheld: `deny` with the reason.
13. **Tool.** The agent runs the tool with the original or the shorter input, or reports the refusal to the model.
    The watching hook then reports the result (`PostToolUse`), which becomes a `tool_result` record.

Timeouts are ordered so the outcome always arrives: review 600 s < hook script 650 s < agent hook timeout 660 s.
A hook that times out does not block in Claude Code, so the hook timeout must be the longest.

## 5. The network gate

The hook sees what a tool call says; the gate sees where a session actually connects, including scripts, package
managers, and MCP servers the session starts.

- **Where it runs.** An asyncio server on `127.0.0.1` on a random port, in its own thread, started with the first
  session. Each session's proxy URL is `http://<session-id>:<hook-token>@127.0.0.1:<port>`, so every connection is
  attributed to a session. Requests without valid credentials get `407`.
- **Per-session ports.** A safe Claude Code session also gets a port of its own, because Claude Code's sandbox
  proxy setting cannot carry credentials. Connections to that port count as that session's.
- **What it sees.** For `CONNECT` (HTTPS), the host and port only. For plain HTTP, the full URL. A plain-HTTP
  connection carries exactly one request: the gate forwards it with `Connection: close` and its body by
  `Content-Length`, and reads nothing more from the client, so a reused connection cannot reach a host unchecked
  or pass on the proxy credentials. Chunked plain-HTTP requests are refused with `411`.
- **Checks.** Model hosts (`anthropic.com`, `claude.ai`, `oauth2.googleapis.com`, `*aiplatform.googleapis.com`,
  `generativelanguage.googleapis.com`, `cloudcode-pa.googleapis.com`, `openai.com`, `chatgpt.com`,
  `bedrock*.amazonaws.com`) always pass and show as "Model provider". In safe sessions, names and addresses that
  reach this computer (loopback, link-local such as `169.254.169.254`, unspecified, `*.localhost`,
  `metadata.google.internal`) are refused with `403`. Everything else is evaluated with the rules as kind
  `network` to the host, with category `plain` plus any categories the detector finds in the URL. Only a `block`
  verdict refuses the connection (`403` with the reason); the gate cannot hold a connection for review.
- **Records.** Every connection becomes a `connection` evidence record (host, port, masked URL, allowed, model)
  and, unless the same session reached the same recipient in the last 20 seconds, a step on the live map.

## 6. Safe mode layers

Safe mode adds a sandbox to each agent session so that the hooks and the gate cannot be bypassed easily and the
agent cannot reach credentials or Hidee's own records. The user-facing summary is in
[USER_GUIDE.md](USER_GUIDE.md#31-what-safe-mode-does-for-each-agent); this section gives the mechanism.

**Claude Code.** `sessions.safe_settings` adds to the session's `settings.json`:

```json
{"permissions": {"defaultMode": "default"},
 "sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false,
             "filesystem": {"denyRead": ["~/.ssh", "~/.aws", "..."], "denyWrite": ["~/.hidee"]},
             "network": {"allowedDomains": ["*"], "deniedDomains": ["localhost", "*.localhost", "127.0.0.1",
                         "[::1]", "0.0.0.0", "169.254.169.254", "metadata.google.internal"],
                         "httpProxyPort": "<the session's gate port>"}}}
```

The launch adds `--setting-sources project,local --disable-slash-commands --strict-mcp-config`, which skips the
user's own settings, plugins, skills, slash commands, and MCP servers. The `env` block of the user's
`~/.claude/settings.json` is copied into the session settings so the sign-in still works.

**Gemini CLI.** The whole CLI runs as `sandbox-exec -f gemini.sb -D TARGET_DIR="$PWD" -D HOME_DIR="$HOME"
-D TMP_DIR="${TMPDIR:-/tmp}" gemini -e none`. The profile (`sessions.gemini_profile`) allows everything by default,
then denies all file writes except the project, temp folders, `~/.gemini`, `~/.cache`, `~/.npm`, and `/dev`;
denies reads of the credential folders except the Vertex AI credentials file; and denies all outbound network
except `localhost:<app port>` (hooks) and `localhost:<gate port>`. Hidee runs Gemini inside its own profile
because Gemini's built-in proxied profiles allow only port 8877, which would cut off the hooks. The system
settings also set `skills.enabled=false`, `hooksConfig.disabled` (the names of the user's own hooks), and
`mcp.excluded` (the user's MCP servers).

**Gemini MCP servers.** Gemini CLI removes proxy URLs that carry credentials from the environment of the MCP
servers it starts. Gemini sessions therefore get a credential-free gate port of their own (`Gate.session_port`, local
services refused only in safe mode), so their MCP servers' connections also pass the gate; the safe profile allows
that port.

**Codex CLI.** Like Gemini, the whole CLI runs inside a Seatbelt profile written by Hidee
(`sessions.codex_profile`, saved as `codex.sb` in the session folder): writes only to the project, temp,
`~/.codex`, and `~/.cache`; no reads of the credential folders; no writes to `~/.hidee`; network only to Hidee's
port and the gate's, so model traffic and every command's connections pass the gate. Codex's own sandbox is off
(`--sandbox danger-full-access`), because macOS cannot nest `sandbox-exec`, and because its network-less sandbox
made each outbound command fail and ask to rerun outside any sandbox. The launch also adds `web_search="disabled"`
(hosted web search runs on OpenAI's side where no local hook can see it), `--disable plugins --disable apps`, and
turns off the user's own Codex setup without copying `CODEX_HOME` (which would rotate the ChatGPT refresh token):
each hook in `hooks.json` gets `hooks.state."<file>:<event>:<group>:<index>".enabled=false`, inline `config.toml`
hooks for other events become empty lists, and each `mcp_servers.<name>` gets `enabled=false`.

## 7. Learn timeline

Every session registers watching hooks, because the evidence log needs prompts, results, and answers. Learn mode
decides only whether those events are also shown.

- Claude Code: `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop`.
- Gemini CLI: `BeforeAgent`, `AfterTool`, `AfterAgent`, translated by `dialects.gemini_event`.
- Codex CLI: `UserPromptSubmit`, `PostToolUse`, `Stop`.

`POST /hook/learn` writes the evidence record for every session and, for learn sessions, adds an event to
`learn.Timeline`: kind (`prompt`, `call`, `result`, `failure`, `answer`), time, tool, a short summary, an excerpt
of at most 600 characters with API keys, card numbers, and government IDs masked, the full size in characters, the
categories found, a `where` label (`local`, `model`, `outside`), and a plain explanation. Tool calls come from the
checking endpoint; a call is `outside` when `map_call` treats it as outbound. Results, failures, prompts, and
answers are `model`, because they join the conversation that goes to the provider.

The **Behind the agent** page (`/behind/{id}`) polls `GET /learn/{id}?since=N` every 1.5 seconds. For Claude Code,
`Timeline.usage` reads the transcript named in `transcript_path` (accepted only if it is a `.jsonl` file under
`~/.claude/projects`), counts assistant messages with usage once per message id, and returns the number of
requests, the input tokens of the first and latest request (input plus cache read plus cache creation), the total
output tokens, and the model. The timeline is in memory, 500 events per session.

## 8. Evidence log

`evidence.EvidenceLog` appends one JSON object per line to `~/.hidee/app/evidence/<UTC date>.jsonl`. Each record
carries a sequence number and the SHA-256 of the previous record, so a deleted, reordered, or edited record breaks
the chain from that point on. Writers are the session manager (start, end), the hook endpoints (tool calls,
decisions, executed input, prompts, results, failures, answers), and the gate (connections). All writes go through
one lock in the Hidee process. The record format and the hash computation are in
[DATA_AND_EVIDENCE.md](DATA_AND_EVIDENCE.md).

## 9. Security boundary

- The app listens on `127.0.0.1` only, and `TrustedHostMiddleware` refuses any `Host` other than `127.0.0.1` or
  `localhost`, which defeats DNS rebinding.
- Forms that change state (create or close a session, decide, save keys, export evidence, pick a folder) require
  the per-run CSRF token. The terminal WebSocket requires the same per-run key and a matching `Origin`.
- Hook endpoints require the per-run hook token, which only Hidee's sessions receive.
- Terminal and hook routes exist only in app mode. `serve` and `cloud-demo` have none. The online demo runs in
  app mode, but its sessions are scripted (`online.ScriptedSessionManager`), so no shell runs and no network gate
  starts; each visitor gets a private app over a temporary folder that is removed after an hour.
- Agents cannot change rules or decide their own actions. In safe mode, Claude Code's sandbox denies writes to
  `~/.hidee`.

## 10. Outside Hidee's view

- Agents started outside Hidee, and anything a session does that neither a hook nor the gate sees.
- Processes that ignore `HTTP_PROXY` and `HTTPS_PROXY` or open raw sockets, outside safe mode.
- The content of HTTPS connections; the gate sees only host and port.
- Codex hosted web search, which runs on OpenAI's servers (turned off in safe mode).
- Tools a hook does not name in a way `toolmap.py` recognizes, and network code that the patterns miss: those
  steps count as local.
- What the model provider does with the conversation. Hidee marks that data as "goes to the model provider" but
  does not filter it.
