<h1 align="center">vikunja-mcp</h1>

<p align="center">
  <strong>A tracker your coding agents can be left alone with.</strong><br>
  An MCP server that turns a self-hosted <a href="https://vikunja.io">Vikunja</a> board into a
  pipeline with gates — where the rules live in the tools, not in a prompt you hope was read.
</p>

<p align="center">
  <a href="https://github.com/ufna/vikunja-mcp/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ufna/vikunja-mcp/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/ufna/vikunja-mcp/releases"><img alt="release" src="https://img.shields.io/github/v/tag/ufna/vikunja-mcp?label=release&color=blue"></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <a href="https://modelcontextprotocol.io"><img alt="MCP" src="https://img.shields.io/badge/MCP-server-8A2BE2"></a>
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

![The seven-column board an agent actually works](docs/images/board.png)

## What this is

Most task-tracker integrations are CRUD wrappers: they hand an agent `create_task`,
`update_task`, `delete_task` and hope the prompt keeps it honest. This one does the opposite.
It exposes fifteen narrow tools, and each one refuses the moves that would break the process:

```
Backlog → Queue → Design → Build → Review → [human] → Done
                     ↕        ↕
                  Your Call        (+ independent review of every task in Review)
```

- **`Backlog` and `Done` are human territory.** Triage in at one end, sign off at the other.
  There is no argument to `advance` that reaches `Done` — an agent that tries is told
  *only a human moves a task to Done after review*.
- **`Queue → Design → Build → Review` is the agent loop.** Claim a task, write a spec to leave
  Design, produce a worklog and an evidence sha to leave Build.
- **`Your Call`** is the side branch for when an agent needs a decision it should not make
  alone. It keeps its assignment and its context; the human answers on the card.

Gates are guardrails for agents, not a security boundary — the real boundary is the scoped
API token Vikunja mints. See [SECURITY.md](SECURITY.md).

## Why

An autonomous agent left running against a plain task API drifts in ways that are individually
reasonable and collectively useless: it marks its own work done, it starts the next thing before
finishing this one, it "fixes" a bug by deleting the test, and the only record of any of it is a
chat log that scrolled away three hours ago.

None of that is fixed by a longer prompt. The prompt is advice; the tool call is the decision
point. So the process is enforced where the decision happens:

| Instead of hoping the agent… | …the tool refuses |
| --- | --- |
| doesn't grade its own homework | `advance(to="done")` — always rejected, Done is human-only |
| writes down a plan before coding | `advance(to="build")` without a `spec` |
| says what it did and where | `advance(to="review")` without a `worklog` **and** an `evidence` sha |
| works on one thing at a time | `claim` past the project's WIP limit |
| escalates instead of guessing | `call_human` exists, and parks the card without dropping it |
| leaves a trail a human can audit | every transition writes a marked comment on the card |

What you get back is a board where each card carries its own history — the claim, the plan, the
work, the independent verdict — in the order it happened.

## What it looks like in practice

A card that has been all the way through the loop. Nothing here was typed by a human: the
markers, the labels and the stage are what the tools wrote as the agents moved it.

<img src="docs/images/task-trail.png" alt="A task's comment trail: claim, spec, worklog with an evidence sha, and an independent review verdict" width="820">

Read top to bottom, that is `claim` → `advance(to="build", spec=…)` → `advance(to="review",
worklog=…, evidence=…)` → a **different** agent's `review_task(verdict="approve", report=…)`.
The `reviewed` label is what the verdict left behind; the card now sits in Review waiting for
a human to sign it off. Every task gets that review, not just bug fixes — only an `epic`
container is exempt, because its code lives in its children.

And when the agent hits a decision that isn't its to make, it parks the card instead of
guessing:

<img src="docs/images/yourcall.png" alt="A card parked in Your Call with the agent's question and its recommendation" width="820">

The card keeps its assignee, so it comes back to the same agent when you answer. Set
`VIKUNJA_NOTIFY_WEBHOOK` and you also get a Slack-shaped ping with a deep link, so parking a
question doesn't mean waiting for someone to notice a board.

## Quick start

**1. Install** — no clone needed, `uvx` runs it straight from the repo:

```bash
uvx --from git+https://github.com/ufna/vikunja-mcp@stable vikunja-mcp --version
```

**2. Create the board.** With an admin token, this creates the project if it's missing and
reconciles the seven canonical columns (it also migrates a default Vikunja board's
`Todo`/`Doing` columns, and prints ready-to-commit config snippets):

```bash
VIKUNJA_TOKEN=<admin token> uvx --from git+https://github.com/ufna/vikunja-mcp@stable \
  vikunja-mcp setup --project "My Project" --share agent-bot:write --url https://vikunja.example.com
```

**3. Point the repo at it.** Commit `.vikunja-mcp.toml`; keep the token out of it:

```toml
[tracker]
url = "https://vikunja.example.com"
project_id = 12
wip_limit = 3          # how many Design/Build tasks one token may claim into at once
language = "en"        # "en" | "ru" — what language cards are written in
```

```bash
# .vikunja-mcp.env — same directory, gitignored, NEVER committed
VIKUNJA_TOKEN=tk_xxxxxxxxxxxx
```

**4. Register the server** with Claude Code (`.mcp.json`) or [opencode](https://opencode.ai)
(`opencode.json`). Both subscribe to the moving `stable` branch, so releases roll out on the
next session start with no per-repo bumps:

```json
{ "mcpServers": { "tracker": {
    "command": "uvx",
    "args": ["--refresh-package", "vikunja-mcp",
             "--from", "git+https://github.com/ufna/vikunja-mcp@stable", "vikunja-mcp"]
} } }
```

```json
{ "$schema": "https://opencode.ai/config.json", "mcp": { "tracker": {
    "type": "local",
    "command": ["uvx", "--refresh-package", "vikunja-mcp",
      "--from", "git+https://github.com/ufna/vikunja-mcp@stable", "vikunja-mcp"],
    "enabled": true
} } }
```

**5. Teach the agent the process** — `vikunja-mcp install-skill` installs the packaged
tracker skill (queue discipline, when to escalate, what a worklog owes a reviewer) for both
Claude Code and opencode. For Claude Code it also provisions a conditional `SessionStart`
hook so that inside a tracker-configured project a bare `/loop` drains the queue instead of
falling back to the generic "don't start work on your own" default. Outside such a project
the hook emits nothing.

Then run the loop. `/loop 10m` for unattended work, plain `/loop` when you're watching.

## The fifteen tools

| Tool | Gate / behavior |
| --- | --- |
| `next_task()` | One thing, in order: your active Design/Build card (including one bounced back from Your Call), then a Queue card already assigned to you, then a card in Review awaiting an independent verdict, then the top free Queue card. Never offers Backlog, a `blocked`-labeled card, or an epic container. |
| `claim(task_id)` | Queue → Design only, and only under the WIP limit. Assign-then-verify: it assigns you, re-reads the card, and backs off if someone else won the same window. |
| `get_task(task_id)` | The dossier: description, stage, assignees, labels, attachments, full comment thread. |
| `search(query)` | Find cards by a keyword, board-wide — the duplicate check before `file_task`. The whole query is matched as ONE substring over title and description, so pass one distinctive word. Read-only; every hit carries `id`, `ref`, `project_id`, `done`, and its stage when it is on this board. |
| `comment(task_id, text)` | A progress note on the card. |
| `advance(task_id, to, spec=, worklog=, evidence=)` | `to="build"` needs a `spec`; `to="review"` needs a `worklog` **and** an `evidence` sha. `to="done"` is always rejected. The card must be assigned to you. |
| `review_task(task_id, verdict, report)` | `approve` or `needs_work`, with a report of what you ran. Applies the `reviewed` / `review-failed` label; `needs_work` sends the card back to the implementer in Build. You must not be the author — enforceable as a hard gate once a second identity exists. |
| `call_human(task_id, question)` | Design/Build → Your Call, keeping your assignment. Posts the question and, if configured, pings a webhook. |
| `return_task(task_id, reason)` | For *external* blockers (no access, a dependency missing, someone else's service down). Unassigns you, adds `blocked`, returns the card to Backlog for re-triage. |
| `decompose(task_id, subtasks)` | Splits your own oversized task into ≥2 Queue subtasks linked to the parent; the parent becomes an `epic` container in Backlog. |
| `file_task(title, …)` | Files an out-of-scope finding into **Backlog** for human triage — never straight into Queue. Optionally linked to the card you found it on. |
| `handoff(task_id, to, title)` | Parks YOUR active card and files the work it waits for onto a NEIGHBOUR project's board — their human triages it. Your card stands down in **Queue**, carrying NO `blocked` label: the blocked *relation* to their new card is what withholds it now and re-offers it the moment their card reaches Review. |
| `transfer_task(task_id, to, reason)` | Moves a card — whole comment history riding along — onto a neighbour board's Backlog: a misfile anyone can see, no ownership needed. |
| `attach_file(task_id, path, note=)` | Attaches a local file — typically a screenshot of the finished work — so the reviewer can *see* the result. Journals itself on the card. |
| `download_attachment(task_id, attachment_id)` | Returns a path to read, not base64, so a screenshot never bloats the agent's context. |

## Beyond the tools

Three commands round out the loop; none of them speak MCP, and the SDK is imported lazily so
they don't pay for it.

**`vikunja-mcp claimable`** — one JSON line answering "is there claimable work for this token
right now?", exit 0 if the check ran. It calls the real `next_task()`, so it cannot drift from
the gates, and it is read-only by contract. Built for a supervisor that would otherwise boot a
paid agent session every poll tick just to discover there was nothing to do.

**`vikunja-mcp workspace <id>`** — a per-task git worktree on a throwaway `task/<id>` branch,
so several agents can drain the queue in parallel without fighting over one checkout.
`--release` pushes and cleans up; `--gc` reaps orphans and fast-forwards your main checkout.
Its safety rule is one line: **push OK → remove, push FAIL → keep.** Dirty, unpushed or
unreachable work is reported, never destroyed. (One real exception, documented rather than
papered over: git-*ignored* files are invisible to the dirty check. Carry screenshots out of
the worktree before you release it — see [the dossier](docs/dossier/workspace.md).)

**`vikunja-mcp setup` / `install-skill`** — idempotent board reconcile, and the agent-facing
skill install described above. Both are safe to re-run; the MCP server also self-heals the
installed skill on start, so a moving `stable` refreshes it automatically.

## Configuration

Four layers, highest priority first:

1. **Environment** — `VIKUNJA_URL`, `VIKUNJA_TOKEN`, `VIKUNJA_PROJECT_ID`, `VIKUNJA_NOTIFY_WEBHOOK`
2. **`.vikunja-mcp.env`** — repo-local `KEY=VALUE` file beside the toml, **gitignored**. The
   per-project token for a machine that works across several repos.
3. **`.vikunja-mcp.toml`** — committed, found by walking up from the cwd. Safe to commit
   because it holds no secret.
4. **`~/.config/vikunja-mcp/env`** — the usual home for a personal `VIKUNJA_TOKEN` (`chmod 600`).

Two rules make that split matter, and they run in opposite directions:

- **A secret is never read from the toml.** Not the token, not the webhook URL. So the
  committed file cannot leak one even by accident.
- **Team policy is never read from the environment.** `wip_limit`,
  `require_review_independence` and `language` are toml-only, because they describe how *the
  project* works, not which machine you're on. Unset, `wip_limit` is **3** — not "unlimited";
  `wip_limit = 0` is a config error, because "no limit" is deliberately not expressible. Unset,
  `language` is `"en"`, and an unrecognised value is a config error for the same reason.

`worktree_root` sits on the machine side of that line, so there the environment does win.

`language` governs more than the tool's own output. The spec, the worklog and the review report
are the bulk of a card's text and the tool does not write them — the agent does — so the value
also rides in every `next_task` response, and the packaged rulebook tells the agent to write in
it. What it never touches is the comment markers (`[worklog]`, `[review]`, …): two of them are
matched with `startswith` to decide whether a card is offered for review, so they are frozen in
every language.

Full reasoning, including why the WIP limit gates one transition rather than policing a count:
[docs/dossier/config.md](docs/dossier/config.md).

## Releases

Consumers subscribe to the moving `stable` branch. Every green push to `main` auto-bumps the
patch version, tags `vX.Y.Z`, and moves `stable` onto it — so a fix reaches every consuming
repo at their next session start, with no PR bots and no per-repo version bumps. Immutable
tags remain the history and the rollback points:

```bash
git branch -f stable vX.Y.Z && git push -f origin stable   # rollback to a known-good tag
```

Minor and major bumps are a hand-edited commit; CI resumes auto-patching from the new baseline.
[docs/dossier/releases.md](docs/dossier/releases.md) has the race analysis behind the atomic
push and the forward-only channel.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest tests/unit -q
```

Integration tests run against a real Vikunja container and skip themselves without
`VIKUNJA_TEST_URL` — the recipe is in [CONTRIBUTING.md](CONTRIBUTING.md), along with the
house rules that are less obvious than they look (why line length is two numbers, and why a
mutation sweep without a control round measures nothing).

## Documentation

[**docs/**](docs/) — the rules live in `CLAUDE.md`; the *evidence* lives in nine dossiers, one
per subsystem. If you are about to change a guard, its dossier is where the measurement that
put it there is written down.

## License

MIT — see [LICENSE](LICENSE).
