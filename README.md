# outreach-agent

An agent that runs my post-application outreach. After I apply to a job, it decides who at the
company can actually move the application, finds them, picks the channel, writes the message,
follows up once if nobody answers, and reacts to replies. I approve every message before it goes out.

## What makes it an agent (not a mail-merge)

Each company gets a goal - *get a reply from a human who can move this application* - and the
model works toward it in a tool-calling loop, choosing its own next step from the current state:

```
get_state -> read_posting -> web_search (who leads this team? who recruits for it?)
          -> save_contact -> find_email (verified only) -> draft_message -> set_status
```

On later runs it sees what happened (sent, no reply after 5 business days, a reply arrived) and
decides again: draft one follow-up, answer a question, pivot to a different contact, or stop.

**Tools**

| tool | what it does |
|---|---|
| `web_search` | server-side search to find current employees on the team (LinkedIn profiles via search results) |
| `read_posting` | job text from the Ashby / Lever / Greenhouse public APIs |
| `save_contact` | records a person, with the evidence they currently work there |
| `find_email` | Hunter.io lookup - returns an address **only if verified**; never guesses |
| `draft_message` | queues an email / LinkedIn note for approval |
| `set_status` | ends the run with a decision: waiting, done, or skipped |
| `get_state` | the agent's memory (SQLite): contacts, messages, replies, events |

**Guardrails enforced in code**, not just in the prompt: connect notes <= 200 chars with no
numbers (they read as a sales pitch), at most one metric per email, no em dashes,
no email without a verified address, max 2 contacts per company, one initial message per contact,
one follow-up and only after 5 business days, and only facts listed in `profile/facts.md`.

**Human in the loop.** The agent never sends. `review` is the approval gate: approve, edit, skip
or reject each draft. Emails go out through the Gmail API (tailored resume attached on the first
email, and my Gmail signature pulled from Gmail settings so drafts look hand-written). LinkedIn notes are copied to the clipboard and the profile opens - I click Send, because
automated LinkedIn messaging gets accounts restricted.

## Setup (macOS)

```bash
cd ~/Downloads/resume-toolkit/outreach-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add ANTHROPIC_API_KEY and HUNTER_API_KEY
cp profile/facts.example.md profile/facts.md   # (already filled in on my machine)
```

**Gmail setup (one time, ~5 min)**
1. console.cloud.google.com -> new project -> enable **Gmail API**.
2. OAuth consent screen -> External -> add yourself as a test user.
3. Credentials -> Create credentials -> OAuth client ID -> **Desktop app** -> download JSON,
   save it as `credentials.json` in this folder.
4. The first `review` / `replies` run opens a browser to log in; a `token.json` is saved.

## Hands-off mode (default)

One-time:
```bash
python -m outreach replies        # first run opens a browser to log in to Gmail -> token.json
./install_schedule.sh             # runs `python -m outreach auto` every day at 7 PM
```

Every evening the agent, on its own:
1. picks up new applications (new resumes in `Job Applications/<date>/`, plus `data/applications.csv`)
2. notices which drafts you sent or deleted in Gmail, and pulls in replies
3. works every company that needs action (new app, reply, follow-up due) within `DAILY_BUDGET_USD`
4. puts email drafts straight into **Gmail Drafts** (tailored resume attached)
5. emails you a digest: LinkedIn notes to paste + what is waiting

**You approve by pressing Send in Gmail.** Deleting a draft = rejected. Nothing else to run.

Manual commands still work: `auto`, `run --company X`, `review` (terminal approval), `status`, `note`.

## Cost control

- Posting + state are pre-loaded (fewer turns), prompts/tools/conversation are prompt-cached,
  web searches capped (`MAX_SEARCHES_PER_COMPANY=3`), turns capped (`MAX_AGENT_TURNS=12`).
- Each run prints an estimated cost and is logged; `DAILY_BUDGET_USD` (default $1.00) stops the
  day's runs once reached. Prices in `.env` are estimates - match them to the Console pricing page.

## Files

```
outreach/agent.py     the loop (Anthropic Messages API + tool use)
outreach/tools.py     tool schemas + implementations + guardrails
outreach/prompts.py   goal, decision rules, writing style
outreach/db.py        SQLite memory
outreach/gmail.py     drafts, send, thread reading
outreach/hunter.py    verified email lookup
outreach/postings.py  job-board readers
outreach/cli.py       commands + approval gate
```

Secrets (`.env`, `credentials.json`, `token.json`) and personal data (`data/`, `profile/facts.md`)
are git-ignored.
