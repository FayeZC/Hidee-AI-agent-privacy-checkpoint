# Hidee user guide

Hidee is a local privacy checkpoint and evidence log for AI coding agents. It runs Claude Code, Gemini CLI, or
Codex CLI in terminals inside a browser workspace on your own computer. Before the agent searches the web, fetches
a page, calls an MCP tool, runs a network command, or sends a message, the agent's hook asks Hidee. Your rules
settle the clear cases. Anything the rules do not cover waits for you in the checkpoint, and you choose what
actually leaves. Hidee also keeps a hash-chained record of every session, outside the agent's context.

This guide covers the desktop workspace, `hidee app`. For the agent HTTP API (`hidee serve`) and the key-free
demo (`hidee demo`), see the [README](../README.md).

## 1. Install

You need:

- macOS (recommended) or Linux, and Python 3.11 or newer. Safe mode for Gemini CLI uses macOS `sandbox-exec`, so
  it works on macOS only.
- At least one agent installed and working in your own terminal: `claude` (Claude Code), `gemini` (Gemini CLI),
  or `codex` (Codex CLI). Hidee does not install agents.

**The Mac app** (Apple silicon, macOS 12 or newer): download `Hidee-<version>-macos-arm64.dmg` from
<https://hidee.net/download>, open it, and drag Hidee into Applications. It brings its own Python, so nothing else is
needed besides your agent. The app is not yet notarized by Apple: the first time, open System Settings → Privacy &
Security and click **Open Anyway** next to Hidee (on older macOS, right-click Hidee and choose Open). A double click
opens the workspace in its own window; opening it again while it runs opens another window onto the same Hidee.
A newer version is a new app to download (Settings → Updates says when there is one).

**From a terminal:** `curl -fsSL https://hidee.net/install.sh | sh` installs the `hidee` command (it checks the
download's SHA-256 first). Or install from the source folder:

```bash
git clone https://github.com/FayeZC/Hidee-AI-agent-privacy-checkpoint.git && cd Hidee-AI-agent-privacy-checkpoint
uv venv && uv pip install -e .        # or: python3 -m venv .venv && .venv/bin/pip install -e .
```

Or install a built wheel (see [DEVELOPMENT.md](DEVELOPMENT.md) for `scripts/build_release.sh`):

```bash
uv tool install ./release/hidee-*-py3-none-any.whl     # or: pipx install <wheel>
```

## 2. Start the workspace

```bash
hidee app                 # or .venv/bin/hidee app from the source folder
```

Hidee prints a short welcome in the terminal and opens the workspace in its own window, with Hidee's icon in the
Dock (on macOS; elsewhere it uses pywebview if installed, or your browser). Pages the workspace opens beside itself,
such as Behind the agent, open as more Hidee windows. Closing the window quits Hidee. The workspace also answers at
<http://127.0.0.1:8770/workspace>. Typing `hidee` alone shows the commands and offers to open the workspace.
Options:

| Option | Default | Meaning |
| :- | :- | :- |
| `--port` | `8770` | Port of the workspace (it listens on 127.0.0.1 only) |
| `--home` | `~/.hidee/app` | Folder for rules, the database, the evidence log, and session files |
| `--browser` | off | Open the workspace in your browser instead of its own window |
| `--no-browser` | off | Open nothing; just serve the workspace |

On first start Hidee writes the default app rules to `~/.hidee/app/rules.yaml` (see [RULES.md](RULES.md)). Keep
the `hidee app` process running while you work: if it stops, every checked tool call is blocked (see section 9).
Stopping it with Ctrl-C closes all sessions.

The workspace has four numbered panes: **1 Sessions** (left), **2** the terminal or the start panel (middle),
**3 Live flow** (see what leaves: a map of every step), and **4 Checkpoint** (decide: where steps wait for you).
The top bar follows the same story: **Workspace** (see and decide), **What left** (where your agents' data went),
**Evidence** (the record), and **Settings**.

## 3. Start a session

Click **+ New session**. The start panel asks three questions and offers two switches.

**1. Where do you want to work?**

- **New project**: type a name. Hidee creates `~/Hidee Projects/<name>` (letters, digits, spaces, `-`, and `_`
  are kept) and names the session after it.
- **Open a folder**: click **Choose…** to open your system's folder chooser (macOS, or Linux with `zenity`), or
  type a path. The session is named after the folder.

**2. Which agent?** Claude Code, Gemini CLI, Codex CLI, or Shell. A shell session is your own login shell: programs
in it that honor proxy settings still go through Hidee's network gate, but it has no hooks, so nothing waits in the
checkpoint.

**3. How should it sign in?**

- **Sign in the way my terminal does** (default). Hidee starts the agent through your login shell
  (`$SHELL -l -i -c`), so whatever works in your terminal works here: a Claude or ChatGPT subscription, a Google
  account, API keys exported in your shell profile, or Vertex AI variables. For Gemini CLI, Hidee selects Vertex AI
  sign-in when `GOOGLE_GENAI_USE_VERTEXAI=true` is set in your profile.
- **My … key**: use a key saved in **Settings** (section 8). Hidee picks the saved key that matches the agent:
  Anthropic for Claude Code, Gemini for Gemini CLI, OpenAI for Codex CLI. If no key is saved for that provider,
  the session does not start and Hidee tells you to add one.

**Safe mode** (on by default, agents only). The agent can change only the project folder, cannot read your
credential folders, and every connection goes through Hidee's network gate. What this means for each agent is
described in section 3.1.

**Show what happens behind the agent (learn mode)** (off by default). Adds the **Behind the agent ↗** view to the
session (section 6). Learn mode only shows; it never changes what the checkpoint allows or blocks.

Click **Start**. The terminal shows the agent; use it as you normally would. If the agent program is not found on
Hidee's PATH or on your shell's PATH, Hidee says so and suggests a shell session instead. Click **Close** in the
terminal head to end a session.

### 3.1 What safe mode does for each agent

| | Claude Code | Gemini CLI | Codex CLI |
| :- | :- | :- | :- |
| Sandbox | Claude Code's own sandbox for every command; unsandboxed commands are not allowed; the session fails to start if the sandbox is unavailable | The whole CLI runs inside a Seatbelt profile written by Hidee (macOS) | The whole CLI runs inside a Seatbelt profile written by Hidee (macOS); Codex's own sandbox is off |
| Writes | project and temp only; `~/.hidee` is denied | project, temp, `~/.gemini`, `~/.cache`, `~/.npm` | project, temp, `~/.codex`, `~/.cache` |
| Credential folders | reads denied: `~/.ssh`, `~/.aws`, `~/.config/gcloud`, `~/.gnupg`, `~/.kube`, `~/.netrc`, `~/.docker/config.json`, `~/.git-credentials`, `~/.npmrc`, `~/.pypirc`, `~/Library/Keychains` | same list, except the one Vertex AI credentials file `~/.config/gcloud/application_default_credentials.json` | same list as Claude Code |
| Network | HTTP(S) from commands goes to the session's own gate port; services on this computer and cloud metadata addresses are refused | only Hidee's port (for hooks) and the gate's port; everything else, including model traffic, passes the gate | only Hidee's port (for hooks) and the gate's port, as for Gemini CLI; hosted web search is turned off |
| Your own extras | your user settings, hooks, plugins, skills, slash commands, and MCP servers are skipped; the `env` block of `~/.claude/settings.json` is kept so your sign-in still works | your own hooks are disabled, skills are off, your MCP servers are excluded, extensions are off (`-e none`) | your own hooks are disabled, your MCP servers are turned off, plugins and apps are off |
| Permission mode | pinned to `default`, so your auto mode does not apply | unchanged | unchanged |

## 4. The checkpoint

When a step needs you, it appears in the **Checkpoint** pane and the agent waits. The card shows the session,
the kind of step (Search, Fetch page, Connect, Send message, Use tool, Local access), and the recipient. Below,
each piece of the call is listed with its category and a mark: **✓ allowed by your rules**, **⚠ not covered by
your rules**, or **⛔ never allowed here**. If several steps wait, the card says "1 of N waiting" and shows the
oldest first.

You have three choices:

- **Send shorter version.** The call runs with each sensitive part replaced by a placeholder, such as `[email]`
  in a search query or `REDACTED_EMAIL` in a URL or shell command (brackets would break those). The agent is told
  that details were removed with your approval and that it should not try to send them another way. The address
  the call is sent to stays: a recipient's email in `to`, `cc`, or `bcc`, a chat channel, a billing API's `email`,
  and the same keys inside a command's JSON or form body (`"to": "..."`, `to=...`). Only the content loses its
  sensitive parts, so a reminder still reaches the patient without the chart note. Details your own request names
  stay too: if you asked "send the referral with the patient's name and date of birth" or wrote the phone number in
  your request, the shorter version keeps the name, the date of birth, or that number and removes the rest. Only
  what you typed to the agent counts, never text from a web page, a file, or a tool result, so a page cannot ask for
  a detail to be kept. The card marks each highlighted detail: removed in the shorter version, kept because your
  request names it, or kept because it is where the call is sent. If every detail is an address, the shorter version
  removes them all; if your request names every detail, there is no shorter version. This button
  appears only when Hidee can build a shorter version: there must be something to remove, the shorter version must
  not be forbidden by your rules, and the command must not run a script file (a script cannot be shortened safely
  from outside, so you approve or reject it).
- **Approve as written.** The call runs unchanged.
- **Reject.** The call does not run. The agent sees Hidee's reason and the kinds of data the call contained. On
  the confirmation page you can add a short reason for the agent ("Tell the agent why", optional, one line), such
  as "please resend without the phone number"; the agent is told "Rejected by the user: ..." and can try again
  without that detail. The reason is kept in the evidence log as you wrote it.

Every choice opens a confirmation page first. It shows exactly what **Will be sent** and what **Will be left
out**, or, for a reject, that nothing will be sent. Click the yes button to decide, or **Go back**.

A step that nobody decides within 10 minutes is rejected (`timed_out`) and the agent is told so.

**Sensitive facts in plain sentences.** Hidee also highlights facts written as ordinary sentences, such as "Jane is
being treated for depression" or "he was arrested last year": health and mental health, sexual orientation,
religion, political opinions, union membership, ethnic origin, genetic or biometric data, finances or debt, legal
trouble, immigration status, and family matters. A word alone is not enough: the sentence has to be about a person
(a name, "she", "my brother", "the patient"), so a general article about depression is not flagged. With sensitive
files listed (below), a sentence that restates something from those files is highlighted too, as "restates your
sensitive files", even when it is worded differently. The card shows the whole sentence with its category, and the
shorter version removes that sentence unless your request asks for it. All of this runs on your computer, with no
model and no connection; the evidence log records only the category. It works by wording, so it can miss a fact
phrased in an unusual way and can flag a sentence that is harmless; the card always shows what it found before you
choose. To turn this layer off (patterns and known data stay on), start the workspace with
`hidee app --no-freetext`.

**Known data from your sensitive files.** Pattern detectors find email addresses, phone numbers, and keys, but not
a customer's name or an account number. List the files that hold such records in `.hidee/sensitive` inside the
project folder (one path or glob per line, for example `customers.csv` or `records/*.md`). Hidee then treats values
from those files (names, dates, ids, account numbers, record references) as sensitive in every outbound call of
sessions in that folder: they are highlighted on the card as "known data from your sensitive files", the rules
hold them for you, and the shorter version removes them (the address the call goes to is still kept). Matching
ignores case and separators. The values stay in Hidee's memory; the evidence log records only the category
`known_data`. Without a `.hidee/sensitive` file nothing changes.

**Messages to people.** When an agent is about to send a message to a person (a chat message, an email, a post,
or a mail or chat API call addressed to someone), Hidee holds it and shows the full text that would go out in your
name, including any wording you did not write, such as an "AI-generated" line, which is noted neutrally and never
removed. You choose: **Send it now** (as written), **Save as a draft for me** (nothing is sent; the agent is told to
save the message as a draft, for example with a draft tool, or to give you the text, and that you will send it
yourself), **Send shorter version**, or **Reject**. Draft tools themselves (save_draft and similar) count as local
work, because a draft in your own account reaches no one. `hidee app --messages draft` saves every message to a person
as a draft without asking; `--messages off` lets the rules decide as for any call. The default is to ask.

**Withheld memory.** After you send a shorter version or reject a step, Hidee remembers the details that were held
back in that session: only what was actually held back. For a shorter version, the removed details (the kept
address and the details your request named are not remembered). For a rejection with a reason, the kinds of detail
the reason names ("please resend without the phone number" remembers the phone number, not the patient's name the
message needs), so a corrected resend goes through; a rejection without a reason remembers what the shorter version
would have removed; a rejection of the destination remembers the destination, not the message's content. If the agent later tries to send the same detail in disguise (an address split up, "at"
for "@", spaced letters, or a phone number with different punctuation), Hidee blocks the call without asking
again and tells the agent to ask you instead. This memory lasts until Hidee restarts.

## 5. Live flow and What left

**Live flow** (pane 3) draws every tool call as it happens: local work goes to "This computer", outbound calls go
to their recipient, and connections seen by the network gate appear under their session. Model traffic shows as
its own "Model provider" flow. Above the map, four counters show kinds of risk (hover one for its meaning):

| Risk | Counts |
| :- | :- |
| Privacy | email, phone, government ID, health, money or pay |
| Security | API keys, card numbers, credential files, privileged commands |
| Internal | confidentiality markers, private network addresses and host names, local file paths |
| Overreach | steps outside the session folder, credential files, privileged commands |

Click a step to open a picture of it: what it carried, what was sent, and its timeline. The flow map lives in
memory and holds the most recent 300 steps.

**What left** shows the most recent 200 checked steps from the database: the whole map from sessions through
Hidee to each service (a service no rule names is marked "Not in your rules", so each step to it waited for you),
then **Who knows what**, then a close-up of each sensitive step (blocked, waiting, or sent shorter).

**Who knows what** is a table of services (rows) by kinds of information (columns). Each cell counts how often
that kind of information was sent to that service. Click a cell to open the last step that sent it. A step counts
here once it is allowed, approved, or sent shorter. (The old `/knows` address opens this section.)

## 6. Behind the agent (learn mode)

In a learn session, the terminal head has a **Behind the agent ↗** link. It opens a page that lists every step of
the agent loop as a card, grouped by turn:

- **You asked**: your message.
- **The model chose a tool**: the tool and a short summary of its input.
- **The tool returned** or **The tool failed**: an excerpt of the output or error.
- **The model answered**: the final answer of the turn.

Each card has one plain sentence about the step and a badge for where its data went: **Stays on this computer**,
**Goes to the model provider** (everything added to the conversation is sent with every later request), or
**Leaves this computer** (a step Hidee checks as outbound). Cards list the kinds of sensitive information they
contain. API keys, card numbers, and government IDs are masked even here; text is cut to 600 characters.

The meter at the top shows how much the conversation holds. For Claude Code it reads real token counts from Claude
Code's own transcript: the number of model requests, the size of the latest request, and the tokens the model
wrote. For Gemini CLI and Codex CLI it shows a character count and a rough estimate (characters ÷ 4).

The timeline lives in memory (the latest 500 steps per session) and is gone when Hidee restarts. The evidence log
(section 7) keeps the permanent record.

## 7. Evidence

The **Evidence** page shows the record Hidee keeps for every session, whether or not learn mode is on:

- **Chain intact** or **Chain broken at record N**: Hidee recomputes the hash chain each time you open the page.
- The number of records and the **Latest hash**. Write the latest hash down or send it to someone: it fixes the
  state of the log at that moment, so any later rewrite can be detected.
- **Export**: downloads `hidee-evidence.zip` with all records and a manifest.
- A table of sessions: name, agent, start time (UTC), number of records, **Your decisions** (decisions made by a
  person out of all decisions), and network connections.

From the command line:

```bash
hidee evidence verify                         # check the log in ~/.hidee/app/evidence
hidee evidence verify hidee-evidence.zip      # check an exported bundle
hidee evidence export --from 2026-11-01 --to 2026-11-05 --out nov.zip
```

Each record keeps an excerpt of long text with API keys, card numbers, and government IDs masked, plus the length
and SHA-256 of the full text. The record format, the hash computation, and the bundle are described in
[DATA_AND_EVIDENCE.md](DATA_AND_EVIDENCE.md).

## 8. Settings: API keys and updates

**Settings** holds one key per provider: Anthropic (Claude Code), Gemini (Gemini CLI), and OpenAI (Codex CLI). On
the start panel, "My saved key" lists the saved keys; the agent you pick signs in with its own provider's key.

- **Save**: the key must be 8 to 300 characters of letters, digits, and `_ - . :`. On macOS it goes into the login
  keychain (service `hidee`); elsewhere into `~/.hidee/app/keys.json`, readable only by you.
- **Test it**: sends one read-only request (list models) to the provider and shows whether the key works.
- **Remove**: deletes the key.

Hidee shows a saved key only as its last four characters. It hands the key to one session through that session's
environment, never on a command line, and unsets competing sign-in variables first (for Anthropic:
`CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_FOUNDRY`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_BASE_URL`; for Gemini: `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_API_KEY`; for OpenAI: `OPENAI_BASE_URL`).
If you already sign in from your terminal, you need no key here.

### Updates

The first time `hidee app` runs in a terminal it asks once: "Check for Hidee updates every 2 weeks? [Y/n]".
With a yes, `hidee app` checks at most every two weeks and, when there is a newer version, shows what is new and
asks "Update now? [y/N]". The workspace also shows a notice, and **Settings → Updates** has the switch, the date of
the last check, and **Check now**. To install, run:

```bash
hidee update          # checks now, shows what is new, asks before installing (--yes skips the question)
```

An update installs only if its signature verifies against Hidee's release key; then start `hidee app` again. A
copy running from a source folder is updated with `git pull` instead.

## 9. Limits you should know

- **Hidee sees only its own sessions.** An agent started in another terminal is not checked or recorded.
- **Hooks see what a tool call says.** Hidee checks web searches, page fetches, MCP calls, shell commands that use
  a network program or send mail, and scripts those commands run directly (up to 256 KB). A command that builds its
  network call in a way the patterns do not recognize is treated as local work.
- **The network gate sees hosts, not HTTPS content.** It is an HTTP proxy set through `HTTP_PROXY` and
  `HTTPS_PROXY`. Programs that ignore proxy settings or open raw sockets bypass it unless safe mode blocks their
  network (safe mode routes or restricts it for all three agents).
  The gate can refuse a host; it cannot hold a connection for your review.
- **Codex hosted web search runs on OpenAI's servers.** No local hook sees it. Safe mode turns it off; outside
  safe mode it is not checked.
- **Model traffic always passes.** Everything the agent reads becomes part of the conversation and goes to the
  model provider. Hidee shows this but does not filter it. The gate recognizes Anthropic, Claude, Vertex AI,
  Google OAuth, and Bedrock hosts as model traffic; other providers' endpoints are treated as ordinary
  connections, which the default rules allow.
- **The detector uses patterns, not a language model.** It finds the categories in [RULES.md](RULES.md) and can
  miss details written in other forms, or flag harmless numbers.
- **If Hidee stops, checked calls are blocked** (the hook fails closed) and sessions lose network access through
  the gate. Watching hooks (prompts, results, answers) fail open, so those steps are simply missing.
- **Auto modes.** Claude Code safe sessions pin the permission mode to `default`. Outside safe mode, Hidee's reply
  still applies; Claude Code documents that with server-side review in auto mode, a hook's changed input for
  WebSearch, WebFetch, or MCP calls can be refused.
- **A shorter version changes only what Hidee detected.** Text the detector did not mark is sent as written.
- **Free-text detection works by wording.** It catches common ways of stating a sensitive fact and restatements of
  your designated files, not every possible phrasing, and it can highlight harmless sentences.
- **A detail your request names is kept by the shorter version.** Hidee matches the value (spacing, case, and
  number formatting ignored) or a word for its kind next to an inclusion word ("with their phone number", "include
  the date of birth"); "without" or "don't include" turns this off. The match is by wording, so an unusual phrasing
  may not be recognized; the card always shows what will be kept and what removed before you choose.
- **The evidence log is tamper-evident, not tamper-proof.** A user with full control of the machine can rewrite
  the whole chain. Recording the latest hash elsewhere makes such a rewrite detectable.
