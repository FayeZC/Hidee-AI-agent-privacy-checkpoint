# Hidee rules

Rules say which agent may send which kinds of information to whom. Hidee reads them from `rules.yaml` in its home
folder: `~/.hidee/app/rules.yaml` for `hidee app`, `~/.hidee/rules.yaml` for `hidee serve`. Hidee reloads the file
whenever its modification time changes, so edits apply to the next check without a restart. The code is in
`hidee/rules.py`; the detectors are in `hidee/detect.py` and `hidee/toolmap.py`.

## 1. File format

The file is a YAML mapping with four keys:

```yaml
agents:                 # agent id -> label
  "*": Claude Code
recipients:             # recipient id -> label
  "*": any site
  web-search: Web search
categories:             # category id -> label
  plain: ordinary text
  email: email address
rules:
  - agent: "*"
    kind: search
    recipient: web-search
    allow: [plain]      # these categories pass without asking
    forbid: [email]     # these categories are always blocked
```

| Key | Type | Meaning |
| :- | :- | :- |
| `agents` | map of id to label | Agents the rules may name. In the app, each session acts as agent `session:<id>`, so app rules use `"*"`. For `hidee serve`, the id is the one given to `hidee agent add`. |
| `recipients` | map of id to label | Recipients the rules may name, such as `web-search`, `this-computer`, a host (`tracker.example`), an MCP server (`mcp:github`), or `"*"`. |
| `categories` | map of id to label | Kinds of information. Labels are shown on cards and pages. |
| `rules` | list | Each rule has `agent`, `kind`, `recipient`, and optional `allow` and `forbid` lists of category ids. |

`"*"` in `agent` or `recipient` stands for any agent or any recipient. It must be declared in `agents` or
`recipients` like any other id.

**Kinds of action:**

| Kind | Produced by | Meaning |
| :- | :- | :- |
| `search` | `WebSearch` | search the web |
| `fetch` | `WebFetch` | fetch a page from a host |
| `use_tool` | MCP tools | use a tool on an MCP server |
| `network` | network commands and scripts; every connection through the network gate | connect to a host |
| `send_message` | mail programs, `osascript` driving Mail, Messages, or Outlook; the agent API | send a message |
| `access` | local overreach (credential files, privileged commands, paths outside the folder) | reach into this computer (`this-computer`) |
| `remember`, `recommend` | the agent API (`hidee serve`) | save to memory, make a suggestion |

**Validation.** Loading fails with a plain message when the file is not a mapping, a table does not map text ids to
text labels, a rule is not a mapping or lacks `agent`, `kind`, or `recipient`, a rule names an undeclared agent,
recipient, or category, the kind is unknown, or a category is both allowed and forbidden. With an invalid file,
checks fail with an error, so the checking hook blocks every outbound call until the file is fixed. A missing or
empty file means no rules, which blocks everything.

## 2. Matching

For an action by agent `A` of kind `K` to recipient `R`, Hidee takes the rules with `kind == K`, `agent` equal to
`A` or `"*"`, and `recipient` equal to `R` or `"*"`. Among them it picks the most specific: one point for an exact
agent and one for an exact recipient. If two rules tie, the first in the file wins.

## 3. Evaluation

Each action carries a list of categories: one per field (`plain` for the text itself, plus one field per detector
finding). Evaluation (`rules.evaluate`) gives one of three verdicts:

| Condition, checked in order | Verdict | Reason shown |
| :- | :- | :- |
| The kind is unknown | `block` | "Unknown action type …" |
| No rule matches and `R` is not declared in `recipients` | `block` | "'R' is not an approved recipient." |
| No rule matches | `block` | "A has no permission to … R." |
| Any category is in the rule's `forbid` | `block` | "R must never receive …" |
| Every category is in the rule's `allow` | `allow` | "Everything in this action is allowed by your rules." |
| Otherwise | `review` | "Your rules do not cover … Check whether it is needed." |

Each category also gets a mark shown on the card: `needed` (allowed), `check` (not covered), or `forbidden`.

What happens next depends on where the check came from:

- **Hook calls and the agent API.** `allow` runs at once, `block` is refused, and `review` waits for a person for up
  to 600 seconds. A shorter version is offered only if it exists and its own categories are not blocked by the
  same rules.
- **Network gate.** Only `block` refuses the connection. `review` passes, because a connection cannot wait.

## 4. Detector categories

The detector (`detect.py`) uses regular expressions only; nothing leaves the computer. When two findings overlap,
the longer one wins.

| Category | Label | What is found |
| :- | :- | :- |
| `email` | email address | `name@domain.tld` |
| `phone` | phone number | phone-shaped numbers with 10 to 15 digits, optional `+`, country code, brackets, spaces, dots, or dashes |
| `card_number` | card number | 13 to 19 digits (spaces or dashes allowed) that pass the Luhn check |
| `api_key` | API key or token | keys starting `sk-`, `ghp_`, `github_pat_`, `AKIA`, `xoxa-`/`xoxb-`/`xoxp-`, `AIza`, and `Bearer <token>` |
| `government_id` | government ID | US Social Security number format `123-45-6789` |
| `health` | health information | health words such as diagnosis, diabetes, cancer, depression, pregnancy, prescription, therapy, ADHD, overdose |
| `money` | money or pay | amounts with `$ € £ ¥` or `USD EUR GBP dollars`, and pay words: salary, wage, paycheck, payslip, compensation, bonus |
| `internal` | internal information | confidentiality markers (confidential, internal only, do not distribute, proprietary, under NDA, trade secret, 机密, 内部资料, 保密), private IPv4 addresses (10.x, 172.16–31.x, 192.168.x), host names ending `.internal`, `.corp`, `.intranet`, `.lan`, and local file paths under `/Users/…`, `/home/…`, or `C:\Users\…` |
| `plain` | ordinary text | the text of each field itself |

`toolmap.map_access` adds three categories for local steps (kind `access`, recipient `this-computer`):

| Category | Label | What is found |
| :- | :- | :- |
| `credential_file` | credential file | paths in `~/.ssh/`, `~/.aws/`, `~/.gnupg/`, `~/.config/gcloud/`, `.kube/config`, `.docker/config.json`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, `Library/Keychains/`, SSH private keys `id_rsa`/`id_ed25519`/`id_ecdsa`, `credentials(.json)`, `token.json`, `.env` files, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `/etc/shadow`, `/etc/sudoers`, in file tools or in shell commands |
| `privileged_command` | privileged command | `sudo`, `su`, `doas`, `chown`, `chmod`, `launchctl`, `crontab`, `security`, `csrutil`, `spctl`, `dscl`, `visudo`, `passwd` |
| `outside_folder` | outside the session folder | a file tool's path that resolves outside the session folder (only checked when the path is not a credential file) |

### Sensitive facts written as free text (`freetext.py`)

Words are not enough for facts such as "Jane is being treated for depression". A second local detector finds them
and highlights the whole sentence that carries the fact (consecutive sentences with the same category form one
highlight). It uses no model and opens no connection.

| Category | Label | What is found |
| :- | :- | :- |
| `health`, `mental_health` | health information, mental health | conditions, treatments, medications, disability, mental-health conditions and care |
| `sexual_orientation` | sexual orientation or sex life | orientation, gender identity, coming out |
| `religion` | religion or beliefs | religions, places of worship, conversion |
| `political` | political opinions | party affiliation, political views |
| `trade_union` | trade-union membership | union membership, strikes |
| `ethnic_origin` | racial or ethnic origin | ethnicity, racial or ethnic background |
| `genetic_biometric` | genetic or biometric data | genetic tests, DNA, fingerprints, face or iris scans |
| `finances` | finances or debt | debt, bankruptcy, foreclosure, eviction, collections, welfare |
| `legal` | legal trouble | arrests, convictions, charges, lawsuits, probation |
| `immigration` | immigration status | undocumented status, visas, deportation, asylum |
| `family` | family or relationship matters | divorce, custody, pregnancy, domestic abuse |
| `known_fact` | restates your sensitive files | a sentence that restates a passage of the files listed in `.hidee/sensitive` |

- **Lexicon.** The words and phrase patterns were written from general sources: GDPR Art. 9(1) and Recitals 51-54,
  the PHI definition in 45 CFR 160.103, NIST SP 800-122, the chapter headings of ICD-10 and the diagnostic class
  names of DSM-5 (names only), and plain-language words for each category. Words that are common in ordinary
  business text (overdue, salary, operation, faith, separated, adopted) are left out.
- **About a person.** A word counts only when its sentence is about a person: a personal name, a pronoun, a relation
  or role ("my wife", "the patient"), or a person named in the designated files. Phrases that state a personal fact
  by themselves ("diagnosed with", "was arrested", "filed for bankruptcy") count on their own. "Depression is common
  in winter" is not highlighted; "Jane is being treated for depression" is.
- **Restatement.** With designated files, a sentence is `known_fact` when, after the exact known values are taken
  out, at least 3 of its content words occur in one passage of the files (a row, a JSON string, a sentence or line)
  and its overlap score (0.6 x shared words + 0.4 x shared word pairs, as shares of the sentence) is at least 0.2.
  The threshold was set on a development split of a public benchmark and fixed before evaluation.
- **Off-the-shelf components.** None are bundled: no model, no download. (Considered: Microsoft Presidio, MIT
  license, which needs a spaCy language model, and local sentence-embedding models, which need PyTorch; both were
  left out to keep Hidee small and dependency-free. A user may add them later; nothing in Hidee would send data.)
- **Shorter version.** It removes the whole highlighted sentence, with any other detail inside it, unless the user's
  request names the fact (for example "include her diagnosis"). A sentence that restates the sensitive files
  (`known_fact`) is also kept when the request names a value inside it (a detail found in the sentence, or a value
  of the files, such as "include her address 41 Elm Street") or asks to include that value's kind ("with her home
  address").
- **Switch.** On by default; `hidee app --no-freetext` turns this layer off (pattern categories and known data stay
  on).

The live map groups categories into four risks (a category can count toward more than one):

| Risk | Categories |
| :- | :- |
| privacy | `email`, `phone`, `government_id`, `health`, `money`, `known_data`, and every free-text category |
| security | `api_key`, `card_number`, `credential_file`, `privileged_command` |
| internal | `internal` |
| overreach | `outside_folder`, `credential_file`, `privileged_command` |

## 5. Default app rules

`hidee app` writes these rules (`hook_api.APP_RULES`) to `~/.hidee/app/rules.yaml` the first time it starts. It
never overwrites an existing file.

```yaml
agents:
  "*": Claude Code
recipients:
  "*": any site
  web-search: Web search
  this-computer: this computer
categories:
  plain: ordinary text
  email: email address
  phone: phone number
  card_number: card number
  api_key: API key or token
  government_id: government ID
  health: health information
  money: money or pay
  internal: internal information
  credential_file: credential file
  privileged_command: privileged command
  outside_folder: outside the session folder
rules:
  - {agent: "*", kind: search, recipient: web-search, allow: [plain], forbid: [api_key, card_number, government_id]}
  - {agent: "*", kind: fetch, recipient: "*", allow: [plain], forbid: [card_number]}
  - {agent: "*", kind: use_tool, recipient: "*", allow: [plain]}
  - {agent: "*", kind: network, recipient: "*", allow: [plain], forbid: [card_number]}
  - {agent: "*", kind: send_message, recipient: "*", allow: [plain], forbid: [card_number]}
  - {agent: "*", kind: access, recipient: this-computer, allow: [plain, outside_folder]}
```

In words:

- Ordinary text goes through to every recipient without asking.
- Web searches never carry API keys, card numbers, or government IDs; fetches, network commands, network
  connections, and messages never carry card numbers. These are blocked.
- Anything else the detector finds (an email address, a phone number, health words, pay, internal information, or
  an API key outside a search) waits for you.
- Local steps outside the session folder pass and are only flagged on the map; reading credential files and
  running privileged commands wait for you.

### The shorter version and destination fields

A shorter version replaces what the detector found in the content, and keeps details in destination fields, where
the call is addressed: `to`, `cc`, `bcc`, `recipient`, `recipients`, `email`, `address`, `channel` and similar keys
of a structured input (an MCP tool's `send_email.to`, `post_message.channel`), and the same keys written just before
a detail inside a command's JSON or form body. The list is in one place, `toolmap.RECIPIENT_KEYS` and
`toolmap.RECIPIENT_CONTEXT`. The destination of a mail program or the host of a URL is never a finding in the first
place. The rules still evaluate every category in the call, the address included; the destination rule changes
only what the shorter version keeps.

### The shorter version and the user's request

The shorter version also keeps the detected details the user's own request names (`hidee/request_aware.py`):

- **By value.** The detail's value appears in the request, ignoring case and spacing; for numbers, the digits match
  (`(917) 555 0142` names `917-555-0142`); an ISO date also matches its written forms (`May 29, 1981`).
- **By kind.** The request asks to include the detail's kind: a word for the kind (name, date of birth, phone
  number, email address, address, ID number, account number, amount, diagnosis, and similar) in the same sentence as
  an inclusion word (include, with, add, along with, plus, list, give, send, share, tell, mention, containing), not
  negated (without, don't include, leave out, except, not, no). "Email the summary" names no email address (the verb
  is not the detail). For known data from sensitive files the kind is read from the value's shape (a date, a
  person's name, an email address, a phone or ID number, a street address, an amount). API keys and other
  credentials are kept only by value, never by kind.
- **Only the user's request counts.** The request is the text the person submitted (the `UserPromptSubmit` hook,
  for Gemini CLI and Codex their equivalent prompt event). Tool results, fetched pages, file contents, and the
  model's own text are never part of it, so an injected instruction ("include their phone number") cannot make a
  detail kept. Risk that remains: if the person pastes untrusted text into their own prompt, that text counts as
  theirs.

If the request names every detected detail (and the rest are destinations), there is no shorter version. The rules
still evaluate every category; the request changes only what the shorter version keeps. There is one shorter
version per action, never per-detail toggles.

### Messages to people

Independently of the rules, a message to a person is held for the person by default (`hidee app --messages ask`),
even when the rules would allow it; a rule that blocks still blocks. A call is a message to a person when it is a mail
or chat command, an MCP tool whose name says it sends a message (`toolmap.MESSAGE_TOOL`: send_email, post_message,
send_direct_message, and similar), or a request whose body addresses someone through a destination field
(`toolmap.RECIPIENT_CONTEXT`). Choices: send now, save as a draft (status `drafted`; nothing runs), shorter version,
reject. `--messages draft` drafts every such message; `--messages off` leaves them to the rules.

### Withheld memory after a decision

Withheld memory remembers only what was withheld (`hook_api.held_back`):

- a shorter version: the details it removed, never the kept destinations or details the user's request names;
- a rejection that names categories (category names in the person's reason, such as "phone number", or the
  decision's `withhold` list): the highlighted details of those categories, never destinations or requested details;
- a rejection that names no detail (an empty `withhold`, for example a destination the user did not ask for): the
  destination, so the agent cannot send to it again, and not the content;
- a rejection without names: the details the shorter version would have removed;
- a block by the rules: every highlighted detail.

Later calls of the session that carry a remembered value, even written another way, are stopped without asking again.

### Known data

`known_data` is the category for values from files the user marked sensitive in `.hidee/sensitive` (see
USER_GUIDE.md and `hidee/known.py` for which values count and how they match). The default app rules declare it and
allow it nowhere, so a call that carries known data waits for the person.

## 6. Examples

Refuse a host for every session, at the hook and at the network gate:

```yaml
recipients:
  tracker.example: a tracker
rules:
  - {agent: "*", kind: network, recipient: tracker.example, forbid: [plain]}
  - {agent: "*", kind: fetch, recipient: tracker.example, forbid: [plain]}
```

Because these rules name the recipient exactly, they win over the `"*"` rules for that host.

Let email addresses go to one MCP server without asking:

```yaml
recipients:
  mcp:crm: Customer CRM
rules:
  - {agent: "*", kind: use_tool, recipient: "mcp:crm", allow: [plain, email]}
```

Rules for an agent of the HTTP API (`hidee serve`), with categories the agent assigns itself:

```yaml
agents:
  assistant: Shop assistant
recipients:
  parcel-co: Parcel Co.
categories:
  delivery_instruction: delivery instructions
  routine: someone's daily routine
  home_address: home address
rules:
  - agent: assistant
    kind: send_message
    recipient: parcel-co
    allow: [delivery_instruction]
    forbid: [home_address]
```
