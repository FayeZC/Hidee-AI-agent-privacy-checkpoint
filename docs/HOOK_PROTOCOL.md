# Hidee hook protocol

Hidee connects to each agent through the agent's own hook system. Two small scripts run as hooks:

- `python -m hidee.hook` (**checking hook**): asks Hidee about a tool call and prints the reply. It can block.
- `python -m hidee.learn_hook` (**watching hook**): reports prompts, results, failures, and answers. It never
  blocks and prints nothing (except `{}` for Codex).

Both use only the Python standard library, run with the same interpreter as Hidee (`sys.executable`), and talk to
Hidee directly, never through the session's proxy settings.

Hidee's checks speak Claude Code's dialect: Claude Code tool names (`Bash`, `WebSearch`, `WebFetch`, `Read`, …)
and replies with `permissionDecision`. Codex CLI uses the same format. Gemini CLI is translated in and out by
`dialects.py`.

## 1. Session environment and HTTP endpoints

Each session's environment carries:

| Variable | Value |
| :- | :- |
| `HIDEE_URL` | `http://127.0.0.1:<port>` of the app |
| `HIDEE_TOKEN` | the per-run hook token (random, 32 bytes, URL-safe) |
| `HIDEE_SESSION` | the session id |
| `HIDEE_AGENT` | `gemini` or `codex`; unset for Claude Code |

The scripts send them as headers `X-Hidee-Token`, `X-Hidee-Session`, and `X-Hidee-Agent`, with the hook's stdin as
the JSON body:

| Endpoint | Called by | Answer |
| :- | :- | :- |
| `POST /hook/pretooluse` | checking hook (Claude Code `PreToolUse`, Gemini `BeforeTool`, Codex `PreToolUse`) | the decision, after the rules or a person decide |
| `POST /hook/aftertool` | checking hook with `--after` (Gemini `AfterTool`) | a note for the model after a shorter version, at most once |
| `POST /hook/learn` | watching hook | always `{}` |

A wrong token gets `401 Unknown hook token`. A missing session header is recorded as session `unknown`.

## 2. Failure behavior

| Script | Without `HIDEE_URL` | Hidee unreachable, wrong token, bad reply, or timeout |
| :- | :- | :- |
| `hidee.hook` (checking) | exit 0, no output: Hidee is not managing this session | **fail closed**: exit 2 with a message on stderr ("Hidee could not check this tool call … so it was blocked. Is the Hidee app still running?"). The agents treat exit code 2 from a pre-tool hook as a block. |
| `hidee.hook --after` | exit 0 | exit 0, no output: a missing note blocks nothing |
| `hidee.learn_hook` | exit 0 | **fail open**: exit 0; the step is missing from the timeline and the evidence log |

The checking hook waits up to 650 seconds for Hidee (5 seconds with `--after`); the watching hook waits 2 seconds.
The reply must be a JSON object; anything else counts as a bad reply. An empty object prints nothing.

## 3. Claude Code

**How settings are supplied.** Hidee writes `~/.hidee/app/sessions/<id>/settings.json` and starts Claude Code with
`claude --settings <that file>` (plus the safe-mode flags in safe sessions). The user's own settings files are not
changed.

**Events registered** (matcher `*` for all):

| Event | Command | Timeout |
| :- | :- | :- |
| `PreToolUse` | `<python> -m hidee.hook` | 660 s |
| `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop` | `<python> -m hidee.learn_hook` | 5 s |

The matcher `*` sends every tool call, local ones included, so the live map shows all work. Only outbound calls
and local overreach can wait.

**Input fields used.**

- `/hook/pretooluse`: `tool_name`, `tool_input`, `cwd` (used when the session is not known, for example in the
  online demo).
- `/hook/learn`: `hook_event_name`, `prompt` (UserPromptSubmit), `tool_name`, `tool_input`, `tool_response`
  (PostToolUse), `error` (PostToolUseFailure), `last_assistant_message` (Stop), and `transcript_path` (token usage
  in learn mode, and the `model_request` evidence records of every Claude Code session; only a `.jsonl` file under
  `~/.claude/projects` is read).

**Replies** from `/hook/pretooluse`:

| Outcome | Reply |
| :- | :- |
| Local work, or allowed by the rules | `{}` (no opinion; Claude Code's own permission flow still applies) |
| Approved by a person | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "Approved in Hidee."}}` |
| Shorter version | `permissionDecision: "allow"`, reason "Approved in Hidee as a shorter version; sensitive parts were removed.", `updatedInput` (the original tool input with each checked string replaced by its shorter value), and `additionalContext` (tells the model which kinds of details were removed, shows the input the tool received, and asks it not to send the removed details another way) |
| Saved as a draft for the user (a message to a person) | `permissionDecision: "deny"`, reason starting "Hidee: the user chose to save this message as a draft and send it themselves. It was not sent." with the instruction to save it with a draft tool or give the user the text |
| Saved as a draft for the user (a message to a person) | `permissionDecision: "deny"`, reason starting "Hidee: the user chose to save this message as a draft and send it themselves. It was not sent.", with the instruction to save it with a draft tool or give the user the text |
| Blocked by rules, rejected, or timed out | `permissionDecision: "deny"`, reason `"Hidee: <reason>"`, followed by `" It contained: <labels>."` when the detector found anything |
| The engine refused the action as malformed | `permissionDecision: "deny"`, reason `"Hidee could not check this call: <error>"` |
| Withheld detail in disguise | `permissionDecision: "deny"`, reason "Hidee: this contains the <label> the user held back earlier in this session, written another way. It was not sent. Do not try to send it again; ask the user instead." |

## 4. Gemini CLI

**How settings are supplied.** Gemini reads system settings from the file named in
`GEMINI_CLI_SYSTEM_SETTINGS_PATH`; system settings take precedence over the user's own. Hidee writes three variants
per session and the launch command picks one after the user's shell profile has run:

```sh
case "$HIDEE_GEMINI_AUTH" in key) f=key;; *) if [ "$GOOGLE_GENAI_USE_VERTEXAI" = true ];
  then f=vertex; else f=default; fi;; esac;
export GEMINI_CLI_SYSTEM_SETTINGS_PATH=<session folder>/gemini-$f.json; exec gemini
```

`gemini-vertex.json` and `gemini-key.json` add `security.auth.selectedType` (`vertex-ai` or `gemini-api-key`),
because a saved Google-account choice would otherwise win. Safe sessions add the settings in
[ARCHITECTURE.md](ARCHITECTURE.md#6-safe-mode-layers).

**Events registered** (Gemini timeouts are in milliseconds):

| Event | Matcher | Hook name | Command | Timeout |
| :- | :- | :- | :- | :- |
| `BeforeTool` | `.*` | `hidee-checkpoint` | `<python> -m hidee.hook` | 660000 |
| `AfterTool` | `.*` | `hidee-note` | `<python> -m hidee.hook --after` | 5000 |
| `AfterTool` | `.*` | `hidee-learn` | `<python> -m hidee.learn_hook` | 5000 |
| `BeforeAgent`, `AfterAgent` | `*` | `hidee-learn` | `<python> -m hidee.learn_hook` | 5000 |

**Input fields used.** `/hook/pretooluse`: `tool_name`, `tool_input`, `cwd`. `/hook/learn`: `hook_event_name`,
`prompt` (BeforeAgent), `prompt_response` (AfterAgent), `tool_name`, `tool_input`, `tool_response` (AfterTool).

**Translation of tool calls** (`dialects.gemini_call`):

| Gemini tool | Checked as | Input built |
| :- | :- | :- |
| `run_shell_command` | `Bash` | `{"command": command}` |
| `google_web_search` | `WebSearch` | `{"query": query}` |
| `web_fetch` | `WebFetch` | `{"url": <first http(s) URL in prompt>, "prompt": prompt}` |
| `read_file`, `read_many_files` | `Read` | other arguments kept; `file_path` from `file_path`, `absolute_path`, `path`, or the joined `paths`/`include` list |
| `write_file` | `Write` | as above |
| `replace` | `Edit` | as above |
| `glob`, `list_directory` | `Glob` | `{"pattern": pattern or dir_path or path}` |
| `search_file_content` | `Grep` | `{"pattern": pattern, "path": dir_path}` |
| an MCP tool (bare name, server in `mcp_context`) | `mcp__<server>__<tool>` | arguments unchanged |
| anything else | its own name | arguments unchanged |

**Translation of replies** (`dialects.gemini_reply`):

| Claude Code reply | Gemini reply |
| :- | :- |
| `{}` | `{}` |
| `deny` | `{"decision": "deny", "reason": <permissionDecisionReason>}` |
| `allow` | `{"decision": "allow"}` |
| `allow` with `updatedInput` | `{"decision": "allow", "hookSpecificOutput": {"tool_input": <Gemini arguments>}, "systemMessage": "Hidee sent a shorter version: sensitive parts were removed."}` |

The shorter input goes back into Gemini arguments: `{"command": …}` for shell, `{"query": …}` for search, and for
`web_fetch` the old URL in the prompt is replaced by the new one (or the new URL is put in front of the prompt if
the old one is not found). Other tools get the updated input as is.

**The note after a shorter version.** Gemini's `BeforeTool` cannot add context for the model, so Hidee keeps the
`additionalContext` note per session and returns it from `/hook/aftertool` as
`{"hookSpecificOutput": {"additionalContext": <note>}}` on the next `AfterTool`, once.

**Translation of lifecycle events** (`dialects.gemini_event`):

| Gemini event | Recorded as |
| :- | :- |
| `BeforeAgent` (`prompt`) | `UserPromptSubmit` with `prompt` |
| `AfterAgent` (`prompt_response`) | `Stop` with `last_assistant_message` |
| `AfterTool` whose `tool_response.error` is set | `PostToolUseFailure` with `error` |
| `AfterTool` otherwise | `PostToolUse`; `tool_response` is `llmContent`, else `returnDisplay`, else the raw response |
| other events | ignored |

## 5. Codex CLI

**How settings are supplied.** Codex's hook format is Claude Code's. Hidee passes the hooks as inline config on the
command line and skips Codex's persisted trust review, because Hidee writes the hooks itself:

```
codex --dangerously-bypass-hook-trust \
  -c hooks.PreToolUse=[{hooks=[{type='command',command="<python> -m hidee.hook",timeout=660}]}] \
  -c hooks.UserPromptSubmit=[{hooks=[{type='command',command="<python> -m hidee.learn_hook",timeout=5}]}] \
  -c hooks.PostToolUse=[…learn_hook…] -c hooks.Stop=[…learn_hook…]
```

Hidee shell-quotes each argument; the command inside is a TOML basic string, so quotes, backslashes, and spaces in
the Python path are escaped.

Safe sessions run the whole CLI under `sandbox-exec -f <session>/codex.sb` and add `--sandbox danger-full-access
-c web_search="disabled" --disable plugins --disable apps`, plus `-c` overrides that turn off the user's own hooks
(`hooks.state`) and MCP servers (`mcp_servers.<name>.enabled=false`); see ARCHITECTURE.md, safe mode.

**Events registered.** `PreToolUse` (checking, 660 s); `UserPromptSubmit`, `PostToolUse`, `Stop` (watching, 5 s).
There is no failure event for Codex.

**Input fields used.** The same as Claude Code. When `tool_name` is `Bash` and `tool_input.command` is a list, Hidee
checks the script itself: for `["bash", "-lc", "<script>"]` (or `-c`) it uses `<script>`; for any other list it
joins the arguments with shell quoting.

**Replies.** The same JSON as Claude Code, with one difference: Codex accepts `permissionDecision: "allow"` only
together with `updatedInput`, so after a plain approval Hidee replies `{}` (no objection) instead of a bare
`allow`. A shorter version is still `allow` with `updatedInput`, and a rejection is `deny`. The watching hook prints `{}` for Codex, because Codex expects JSON from
a `Stop` hook. Codex hosted web search runs on OpenAI's side and never reaches a local hook.

## 6. Reading the evidence of a hook

Every call to `/hook/pretooluse` writes a `tool_call` record, and every outbound or overreaching call writes a
`decision` record (and an `executed` record for a shorter version). Every call to `/hook/learn` writes `prompt`,
`tool_result`, `tool_failure`, or `answer`. See [DATA_AND_EVIDENCE.md](DATA_AND_EVIDENCE.md).
