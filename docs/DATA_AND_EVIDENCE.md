# Hidee data and evidence

This document lists everything Hidee stores, the SQLite schema, the evidence log record format, the hash chain as
implemented in `hidee/evidence.py`, the export bundle, the reconciliation report and forensic timeline, and the
privacy choices behind them.

## 1. Where data lives

All data stays on the user's computer. `hidee app` uses `~/.hidee/app/` (change it with `--home`); `hidee serve`
uses `~/.hidee/`.

| Path | Written by | Contents |
| :- | :- | :- |
| `~/.hidee/app/rules.yaml` | `hidee app` on first start, then the user | Rules ([RULES.md](RULES.md)) |
| `~/.hidee/app/hidee.db` | `store.py` | SQLite database (section 2) |
| `~/.hidee/app/evidence/YYYY-MM-DD.jsonl` | `evidence.py` | Evidence log, one file per UTC day (section 3) |
| `~/.hidee/app/sessions/<id>/settings.json` | `sessions.py` | Claude Code hook settings for the session (and the safe-mode sandbox block) |
| `~/.hidee/app/sessions/<id>/meta.json` | `sessions.py` | Session name, folder, agent, learn, safe, start time; used to keep readable names after a restart |
| `~/.hidee/app/sessions/<id>/gemini-{default,vertex,key}.json` | `sessions.py` | Gemini CLI system settings (Gemini sessions) |
| `~/.hidee/app/sessions/<id>/gemini.sb` | `sessions.py` | Seatbelt profile (safe Gemini sessions) |
| `~/.hidee/app/keys.json` | `keys.py` | Saved API keys, mode 0600, only on systems without the macOS `security` tool |
| macOS login keychain, service `hidee`, account `anthropic`/`gemini`/`openai` | `keys.py` | Saved API keys on macOS |
| `~/Hidee Projects/<name>/` | `workspace.py` | Folders created by "New project" |
| `~/.hidee/memory.jsonl` | `executors.LocalMemoryExecutor` | Approved entries sent to recipient `agent-memory` (`hidee serve` only) |

Kept in memory only, and gone when Hidee stops: the terminal scrollback (256 KB per session), the live flow feed
(300 events), the learn-mode timeline (500 events per session), withheld memory, the per-run CSRF token and hook
token, and the Gemini notes waiting for `AfterTool`.

## 2. SQLite tables

`hidee.db` has five tables (`store.SCHEMA`). Timestamps are Unix seconds (`REAL`). JSON columns hold JSON text.

**`agents`**: tokens for the agent HTTP API.

| Column | Type | Meaning |
| :- | :- | :- |
| `id` | TEXT PK | agent id used in rules |
| `name` | TEXT | readable name |
| `token_hash` | TEXT UNIQUE | SHA-256 hex of the token (`hd_` + 32 random URL-safe bytes); the token itself is never stored |
| `created_at` | REAL | |
| `revoked` | INTEGER | 1 if revoked |

**`actions`**: every checked action (outbound tool calls, local overreach, API submissions).

| Column | Type | Meaning |
| :- | :- | :- |
| `id` | TEXT PK | random hex id |
| `agent_id` | TEXT | `session:<id>` in the app, the agent id in the API |
| `kind`, `recipient` | TEXT | as in the rules |
| `fields` | JSON | `{name: {"value": …, "category": …}}`, the full checked content |
| `minimal` | JSON or NULL | the shorter version, same shape |
| `status` | TEXT | `received`, `waiting`, `allowed`, `minimized`, `blocked`, `timed_out`, `execution_failed` |
| `reason` | TEXT | one plain sentence |
| `marks` | JSON | `{category: "needed" \| "check" \| "forbidden"}` |
| `decided_by` | TEXT or NULL | `rules`, `owner`, or `timeout` |
| `executed` | JSON or NULL | `{name: value}` of what was approved to run |
| `created_at`, `decided_at`, `expires_at` | REAL | `expires_at` is the review deadline |

**`grants`**: one-use approvals.

| Column | Type | Meaning |
| :- | :- | :- |
| `id` | TEXT PK | |
| `action_id` | TEXT | |
| `version` | TEXT | `original` or `minimal` |
| `payload_digest` | TEXT | SHA-256 of the canonical JSON of the approved fields (`core.digest`) |
| `expires_at` | REAL | 60 seconds after creation |
| `used` | INTEGER | set to 1 atomically on first use; a second use is refused |

**`disclosures`**: what each recipient has received, written just before an approved action runs. Feeds Who knows
what.

| Column | Type |
| :- | :- |
| `id` | INTEGER PK |
| `action_id`, `recipient`, `category` | TEXT |
| `at` | REAL |

**`audit`**: events of the decision process, metadata only.

| Column | Type | Meaning |
| :- | :- | :- |
| `id` | INTEGER PK | |
| `at` | REAL | |
| `event` | TEXT | `submitted`, `decided`, `executed`, `timed_out`, `grant_refused`, `execution_failed`, `auth_failed` |
| `action_id` | TEXT or NULL | |
| `detail` | JSON | for example `{"agent", "kind", "recipient", "verdict"}` or `{"choice", "by"}`, never field values |

The `audit` table never holds content, but the `actions` table does: `fields` stores the full text of each checked
call, including the sensitive values the detector found, so that the reviewer can see them. Protect `hidee.db` like
any file with personal data.

## 3. Evidence log records

The evidence log is the customer-side record of every session started in Hidee. Hooks and the network gate write
it, outside the model's context, so it does not depend on what the agent remembers or reports.

Each line of `evidence/YYYY-MM-DD.jsonl` is one JSON object:

```json
{"seq": 812, "at": "2026-11-04T22:41:07.512Z", "session": "43c26332", "event": "decision",
 "data": {"action": "9f2c…", "kind": "search", "recipient": "web-search", "found": ["email"],
          "by": "person", "choice": "minimal", "reason": "Owner approved the shorter version.",
          "seconds": 12.408, "user": "maria"},
 "prev": "5d1e…", "hash": "a07b…"}
```

| Field | Meaning |
| :- | :- |
| `seq` | 1, 2, 3, … across all files, continued after a restart |
| `at` | UTC time with milliseconds, ending in `Z`; also chooses the file name |
| `session` | session id (`unknown` if a hook sent none) |
| `event` | record type (below) |
| `data` | event data (below) |
| `prev` | `hash` of the previous record; 64 zeros for the first record |
| `hash` | SHA-256 of this record (section 4) |

**Digest.** Long text is stored as a digest (`evidence.digest`): `{"excerpt", "length", "sha256", "found"}`. The
text is the value itself if it is a string, otherwise its JSON (`json.dumps(value, ensure_ascii=False, indent=1)`).
`excerpt` is the text with API keys, card numbers, and government IDs replaced by `[api_key]`, `[card_number]`, or
`[government_id]`, then cut to 600 characters (plus `…`). `length` is the number of characters of the full text,
`sha256` the hex SHA-256 of its UTF-8 bytes, and `found` the sorted list of detector categories in the full text.

**Events and their data:**

| Event | Written when | `data` |
| :- | :- | :- |
| `session_start` | a session starts | `name`, `agent` (`claude`, `gemini`, `codex`, `shell`), `folder`, `learn`, `safe`, `sign_in` (`"key saved in Hidee"` or `"the user's own sign-in"`, never a key), `user` (OS user) |
| `session_end` | the session's process exits | `exit_code` |
| `prompt` | the user submits a prompt | digest of the prompt |
| `tool_call` | the checking hook reports a call | `tool`, `input` (digest of the tool input), `where` (`outside` if outbound, else `local`) |
| `decision` | an outbound or overreaching call is decided | `action`, `kind`, `recipient`, `found` (categories other than `plain`), `by`, `choice`, `reason`, `seconds` (from submission to decision), and `user` (OS user) when a person decided |
| `executed` | a shorter version replaced the model's request | `action`, `input` (the tool input that actually ran), `note`, `removed` (categories the shorter version removed), `kept_destination` (categories kept because they are in destination fields) |
| `drafted` | a message to a person was saved as a draft for the user | `action`, `input` (the message the user will send), `note` ("nothing was sent") |
| `tool_result` | a tool returned | `tool` plus a digest of the response |
| `tool_failure` | a tool failed (Claude Code, Gemini CLI) | `tool` plus a digest of the error |
| `answer` | the model finished a turn | digest of the final answer |
| `connection` | the network gate saw a connection | `host`, `port`, `url` (the excerpt of the URL digest), `allowed`, `model` |
| `model_request` | a Claude Code turn ended (Stop), shortly after, and when the session ended; one record per assistant message id not recorded before in the session | `request_id` (message id, `msg_…`, the key the provider also holds), `provider_request_id` (API request id, `req_…`, when the transcript has it), `model`, `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `at` (the transcript's time of the request, UTC), and `subagent` (the sub-agent transcript's name, for a sub-agent's request) |

`model_request` records come from Claude Code's transcript (`learn.model_requests`), read only if the hook's
`transcript_path` is a `.jsonl` file under `~/.claude/projects`, together with the sub-agent transcripts in
`<session>/subagents/*.jsonl`. Claude Code writes one line per content block; lines with the same message id count
as one request, with the last line's usage and the first line's time. `evidence.RequestLedger` remembers which ids
each session has recorded, and reads them back from the log after a restart, so a turn reported twice records each
request once. Gemini CLI and Codex CLI sessions have no `model_request` records: the hook events Hidee receives
from them carry no token usage.

`by` is `rules` when the rules decided, `timeout` when no one decided in time, `hidee` when withheld memory stopped a
disguised retry (that record has no `action`), `default` when the user's default (`--messages draft`) saved a message as a draft, and `person` when someone chose in the checkpoint (only then is `user` recorded). `choice` is `approve` (allowed by rules or approved by a person; for a message to a person, "send it now"), `draft` (saved as a draft for the user; nothing left), `minimal` (shorter version),
`reject` (rejected by a person, or a withheld match), `blocked` (blocked by the rules), or `timed_out`.

## 4. Hash chain

The hash of a record is computed exactly as follows (`evidence._canonical` and `evidence._hash`):

1. Take the record without its `hash` key: `seq`, `at`, `session`, `event`, `data`, `prev`.
2. Serialize it as canonical JSON: `json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
   Keys are sorted at every level, there are no spaces, and non-ASCII characters are kept as is.
3. Encode as UTF-8 and take the SHA-256; `hash` is the lowercase hex digest.

```python
import hashlib, json

def record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

The line on disk is written with `json.dumps(record, ensure_ascii=False)` (default separators, insertion order);
the verifier recomputes the canonical form, so the layout of the line does not matter.

**Writing.** `EvidenceLog.append` holds a lock, builds the record with `seq = last seq + 1` and `prev = last hash`,
computes `hash`, appends the line to the file for the record's UTC date, and remembers the new head. At start, it
reads all files in name order to continue the chain.

**Verifying the log** (`evidence.verify`, used by `hidee evidence verify` and the Evidence page). It reads all
`*.jsonl` files in name order and checks, for each record, that `prev` equals the previous record's hash (64 zeros
for the first), that `seq` equals the expected number (starting at 1), and that `hash` equals the recomputed hash.
The first record that fails is reported. An edit, a deletion, an insertion, or a reordering inside the log breaks
the chain from that record on.

Verification alone cannot detect records removed from the end, or a chain rewritten as a whole. Comparing the head
hash with a copy kept elsewhere (written down, sent to someone, or in an earlier export) detects both.

## 5. Export bundle and manifest

`hidee evidence export` (and **Export** on the Evidence page, which exports all days) writes a zip file with the
chosen day files, unchanged, and `manifest.json`:

```json
{
  "format": "hidee-evidence/1",
  "hidee": "0.2.0",
  "exported_at": "2026-11-05T14:02:11Z",
  "days": ["2026-11-04", "2026-11-05"],
  "records": 812,
  "first_seq": 1,
  "first_prev": "0000000000000000000000000000000000000000000000000000000000000000",
  "head_seq": 812,
  "head_hash": "a07b…",
  "how_to_verify": "Each record's hash is sha256 of its canonical JSON without 'hash' (sorted keys, no spaces); 'prev' is the previous record's hash. Run: hidee evidence verify <this zip>."
}
```

`first_seq` and `first_prev` let a bundle that starts in the middle of the log be checked on its own.

**Verifying a bundle** (`evidence.verify_bundle`). It reads the `.jsonl` members in name order, checks the chain
starting from `first_prev` and `first_seq`, then checks that the number of records equals `records` and that the
last hash equals `head_hash`. It prints one of: "Chain intact: N records, head …", "The chain breaks at record N.",
"The records do not end at the manifest's head hash.", or "The bundle has no records."

## 6. Commands

```bash
hidee evidence verify [BUNDLE.zip] [--home ~/.hidee/app]
hidee evidence export [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--out hidee-evidence.zip] [--home ~/.hidee/app]
```

| Command | Output | Exit code |
| :- | :- | :- |
| `verify` (no bundle) | "Chain intact: N records." or "The chain breaks at record N." | 0 if intact, 1 if broken |
| `verify BUNDLE` | one of the bundle messages above | 0 if intact, 1 otherwise |
| `export` | "Wrote <path>" | 0 |
| `reconcile` | the report (Markdown or CSV), led by the chain's status | 0 |
| `timeline` | "Wrote <csv> and <manifest>", then the chain's status | 0 |

`--from` and `--to` are inclusive and compare against the file names (UTC days).

```bash
hidee evidence reconcile [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--format md|csv] [--home ~/.hidee/app]
hidee evidence timeline [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--out hidee-timeline.csv] [--home ~/.hidee/app]
```

**`reconcile`** (`reconcile.reconcile`) prints totals per UTC day: model requests per model (requests, input
tokens, cache read, cache creation, and output tokens), outbound actions per destination (approve, shorter version,
reject, blocked, timed out, and who decided), and connections per host (allowed, refused, model provider). A model
request counts on the day of its `at`, other records on the day of their own time. The Markdown report starts with
the chain's status and head hash. The CSV has the columns `section, day, subject, status, by, count, utc_time,
session, request_id, provider_request_id, subagent, input_tokens, cache_read_input_tokens,
cache_creation_input_tokens, output_tokens`: one `model_request` row per request, and one `action` or `connection`
row per day, destination or host, and outcome. With `--format csv` the chain's status goes to standard error, so the
CSV on standard output stays clean.

**`timeline`** (`reconcile.timeline_csv`) writes the forensic timeline: one row per record, sorted by `seq`, plain
RFC 4180 CSV (comma separated, double-quote quoting, CRLF line ends, UTF-8), with ISO 8601 UTC times. Columns, in
order:

| Column | Value |
| :- | :- |
| `utc_time` | the record's `at` |
| `seq`, `session`, `event` | as in the record |
| `actor` | `rules`, `person:<OS user>`, `timeout`, or `hidee` for decisions; `gate` for connections; `person:<OS user>` for prompts; `agent` for tool calls, results, failures, answers, and model requests; `hidee` for session start and end and executed inputs |
| `agent` | `claude`, `gemini`, `codex`, or `shell`, from the session's `session_start` (read even if it falls before `--from`) |
| `tool` | the tool name, if any |
| `destination` | the recipient of a decision, or `host:port` of a connection |
| `decision` | the decision's `choice`, or `allowed` / `refused` for a connection |
| `request_id` | the message id of a model request |
| `sha256` | the sha256 of the full content (prompts, results, failures, answers, and a tool call's input) |
| `record_hash`, `prev_hash` | the record's `hash` and `prev` |
| `detail` | a short summary, at most 160 characters, secrets masked, whitespace collapsed; a leading `=`, `+`, `-`, or `@` gets a `'` in front so a spreadsheet does not run it as a formula |

The chain's status is kept out of the CSV so that timeline tools read it without a header to skip. It goes in a
manifest beside the CSV, `<out>.manifest.json`: `format` (`hidee-reconcile/1`), `generated_at`, `from`, `to`,
`chain_intact`, `chain_records`, `broken_at`, `head_seq`, `head_hash`, `rows`, and `files` (the sha256 of the CSV).
The Evidence page's **Reconcile** section shows the three tables for a chosen range of days and offers two zip
downloads: the reconciliation (`reconciliation.md`, `reconciliation.csv`, `manifest.json`) and the timeline
(`timeline.csv`, `manifest.json`).

## 7. Privacy choices

- **Excerpts, not full text, in the evidence log.** Prompts, tool inputs, results, and answers are kept as a
  600-character excerpt with the full length and SHA-256. The log stays small and holds less personal data, yet a
  full copy produced later can be checked against the hash.
- **Masking.** API keys, card numbers, and government IDs are masked in excerpts, in the learn timeline, and in
  connection URLs. Other categories (for example email addresses and phone numbers) stay readable in excerpts, so
  the record shows what the agent handled; the `found` list names them.
- **Exceptions.** The `executed` record stores the full tool input that actually ran, since that is the evidence of
  what left; it is the shorter version, so the detected details are already replaced. The `actions` table in
  `hidee.db` stores full field values for review (section 2).
- **No secrets in records.** `session_start` records only a label for the sign-in method. Saved keys live in the
  keychain (or a 0600 file), appear only as their last four characters, and reach a session only through its
  environment.
- **Out of the agent's reach.** In Claude Code safe sessions the sandbox denies writes to `~/.hidee`; in Gemini safe
  sessions the Seatbelt profile allows writes only to the project, temp folders, and Gemini's caches.
- **Local only.** Nothing is uploaded. Key checks in Settings contact the provider only when the user clicks
  **Test it**.
