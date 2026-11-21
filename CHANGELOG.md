# Changelog

All notable changes to Hidee. Dates are when the change reached `main`.

## Unreleased

- Fixed: after a shorter version, the What left page showed every highlighted detail as stopped, including the
  details the shorter version kept. The "Reached" side of a step, the map's labels, and Who knows what now follow
  what actually ran: details the user's request names and destination fields show as reached and count as received,
  removed details are struck, and a rejection or a draft reached no one.

- Fixed: a sentence that restates the sensitive files (`known_fact`) was removed whole even when the user's request
  named a value inside it. It is now kept when the request names a value inside it or asks to include that value's
  kind, like any other detail the request names.

- Withheld memory remembers only what a rejection withheld: the categories its reason names as not needed (category
  names in the person's reason, or the decision's `withhold` list), never the needed details, destinations, or details the
  user's request names; a rejection that names no detail remembers the destination; a rejection without a reason
  remembers what the shorter version would have removed. A corrected resend that keeps the needed details now goes
  through; a resend that still carries a named detail, in any form, is stopped. The decision record lists the
  remembered categories (`withheld`).

- `hidee app --no-freetext` turns free-text detection off (default on); patterns and known data stay on.

- Sensitive facts written as free text are highlighted (`hidee/freetext.py`, local, no model): special categories of
  personal data (health and mental health, sexual orientation, religion, political opinions, union membership,
  ethnic origin, genetic and biometric data) and other sensitive facts (finances, legal trouble, immigration status,
  family matters) when the sentence is about a person, and sentences that restate the user's designated sensitive
  files (`known_fact`). The card shows the sentence with its category; the shorter version removes the whole
  sentence unless the user's request names it; the ledger records categories only.

- A rejection can carry a short reason for the agent: a "Tell the agent why" field on the confirmation page of a
  rejection. The agent is told "Rejected by the user: <reason>", and the ledger's
  `decision` record logs it as `note` (as written, one line, at most 300 characters).

- The shorter version is request-aware: it keeps the highlighted details the user's own request names (by value, or
  by asking to include their kind, such as "with the patient's name and date of birth") as well as destination
  fields, and removes the rest. Only the person's own prompt counts, never tool results or fetched pages. The card
  marks each detail as removed, kept because the request names it, or kept because it is the destination; the
  `executed` record lists `kept_requested` next to `removed` and `kept_destination` (categories only); withheld
  memory remembers only removed details.

- Messages to people (chat, email, posts, mail or chat API calls addressed to someone) are held by default and the
  card asks how each goes out: Send it now, Save as a draft for me (nothing is sent; the agent is told to save a draft
  or give the user the text), Send shorter version, or Reject. The card shows the full text in the user's name and
  notes any wording the user did not write (such as an AI attribution line) neutrally, without removing it.
  `--messages draft|off` sets a default. The ledger records `draft` decisions and a `drafted` record.

- The Mac app: `Hidee-<version>-macos-arm64.dmg`, built with PyInstaller (`scripts/build_dmg.sh`), brings its own
  Python and opens the workspace in its own window with a double click. Hook commands reach the app itself
  (`hidee/frozen.py`), so every agent call is still checked. Offered on the download page, with how to open an app
  that Apple has not notarized yet.

- Known data: values from files listed in a project's `.hidee/sensitive` (names, dates, ids, account numbers) are
  detected in outbound calls, held, and removed by the shorter version (`hidee/known.py`). Off without the file.
- Gemini sessions use a credential-free gate port, so the MCP servers Gemini starts (which lose credentialed proxy
  URLs) also pass the gate. Codex safe mode also skips the user's host skills (`~/.agents/skills`).

- The shorter version keeps the address a call is sent to (`to`, `cc`, `bcc`, a chat channel, a billing API's
  `email`, and the same keys in a command's body) and removes sensitive details only from the content, so a
  reminder still reaches its recipient. The checkpoint card marks only the details that are removed; the `executed`
  record lists the removed categories and the kept destination categories; withheld memory remembers only removed
  details. Before this fix, the shorter version also removed the recipient, so messages reached no one.
- Gemini CLI MCP calls are checked as MCP calls (`mcp__<server>__<tool>`) instead of local work.

- Hidee opens in its own window, with its icon in the Dock and its name in the menu bar, instead of a browser tab
  (pywebview; macOS by default). Behind the agent and step pictures open as more Hidee windows, other sites open in
  the browser, and closing the window quits Hidee. `hidee app --browser` keeps the browser; without a window system
  Hidee falls back to it. A busy port is explained instead of failing silently.
- `hidee` alone shows a welcome, the commands, and an offer to open the workspace; `hidee app` prints where the
  workspace and the data are. The help lists only the product commands.

## 0.3.0 (2026-11-15)

- Codex CLI safe mode runs the whole CLI in a sandbox written by Hidee, with every connection through Hidee, and
  your own Codex hooks, MCP servers, plugins, and apps turned off for the session.
- One story in the app: see what leaves (live flow), decide (checkpoint), keep a record (Evidence). Dashboard and
  Who knows what are one page, What left.
- Drag to resize the workspace columns, and the live flow against the checkpoint; the decision buttons always stay
  in view, and the right column no longer jumps.
- Updates with your consent: Hidee asks once whether to check every two weeks, and installs only releases signed
  with Hidee's key (`hidee update`, or Settings → Updates).
- Gemini CLI's MCP tool calls are now checked as MCP calls (they were treated as local work and never held).
- Fixed a database race that could lose a timeout and leave "waiting for you" showing after nothing waited.
- Codex CLI: an approval no longer shows up in Codex as a failed hook; the shorter version is verified end to end.
- Linux: sessions start through the account's login shell when `$SHELL` is unset; Hidee's folders made after
  startup are private to you, like the rest of its home folder.
- The install script checks the download's SHA-256 and, without uv or pipx, installs into its own environment
  instead of `pip install --user`, which Homebrew Python refuses.

- Open-source groundwork: Apache-2.0 license, contributing guide, security policy, CI on macOS and Linux.

## 0.2.0 (2026-11-06)

- The desktop app (`hidee app`): real terminals for Claude Code, Gemini CLI, and Codex CLI, a start panel, and
  Settings with keys in the macOS Keychain.
- Safe mode, on by default: a sandbox for the agent and its traffic sent through a per-session gate; the user's
  own hooks, plugins, skills, and MCP servers are skipped (Claude Code and Gemini CLI).
- Evidence log: a hash-chained record of every session kept outside the agent's context, with
  `hidee evidence verify|export|reconcile|timeline` and an Evidence page.
- Learn mode: the agent loop, where each piece of data went, real token usage, timeline export, a glossary, and a
  practice lesson that needs no sign-in.
- Withheld memory: disguised retries of details the person held back are denied.
- Trust layer: Hidee's data is readable only by the user, and Hidee sends nothing to its makers.

## 0.1.0 (2026-11-03)

- First demoable version: rules in plain language, a checkpoint for outbound tool calls, one-use grants bound to
  the approved content, and key-free demo scenarios.
