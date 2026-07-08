# vikunja-mcp

A workflow-level MCP server for [Vikunja](https://vikunja.io) — not a generic
task CRUD wrapper. It exposes a small set of tools that push an agent through
a fixed pipeline, and the gates (what's allowed from which stage, what
evidence is required) live in the tools themselves, not in prompts:

```
Backlog → Queue → Design → Build → Review → Done
                     ↕        ↕
                     Your Call
```

- `Backlog` and `Done` are human territory (triage in, sign-off out) —
  agents never move a task into `Done` themselves.
- `Queue → Design → Build → Review` is the agent loop: claim, spec, build,
  hand off for review.
- `Your Call` (abbreviated `YC`) is a side branch reachable from `Design`/`Build`
  when an agent needs a decision or input; the human answers and moves it back.

Run that loop under Claude Code's `/loop` with no interval — that lets the model
self-pace. The agent drains the queue one task at a time and schedules its own
wake-ups when the queue is empty, so it keeps working without a human
re-prompting it each turn.

## Install

```bash
uvx --from git+https://github.com/ufna/vikunja-mcp@v0.1.0 vikunja-mcp
```

Register it with Claude Code via `.mcp.json`:

```json
{
  "mcpServers": {
    "tracker": {
      "command": "uvx",
      "args": ["--refresh-package", "vikunja-mcp", "--from", "git+https://github.com/ufna/vikunja-mcp@stable", "vikunja-mcp"]
    }
  }
}
```

Or register it with [opencode](https://opencode.ai) via `opencode.json`
(repo root, or `~/.config/opencode/opencode.json` globally; `.jsonc` also
works). MCP servers live under a top-level `mcp` key, and a local (stdio)
server takes the command and its arguments as one `command` array
([docs](https://opencode.ai/docs/mcp-servers/)):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "tracker": {
      "type": "local",
      "command": ["uvx", "--refresh-package", "vikunja-mcp", "--from", "git+https://github.com/ufna/vikunja-mcp@stable", "vikunja-mcp"],
      "enabled": true
    }
  }
}
```

As with `.mcp.json`, no token goes in this file — the server reads it from the
same four config layers below (`VIKUNJA_TOKEN`, `.vikunja-mcp.env`, or the user
env file). This repo commits exactly such an `opencode.json` at its root to
dogfood itself against the `stable` channel.

To hand an opencode agent the tracker process rules (queue discipline, stage
gates, `call_human` vs `return_task`), run `vikunja-mcp install-skill` — it
installs the packaged `SKILL.md` for both Claude Code and opencode and prints an
`instructions` line to add to your `opencode.json`:

```json
{
  "instructions": ["/home/you/.config/opencode/skills/tracker/SKILL.md"]
}
```

opencode also auto-loads `AGENTS.md` from the repo root (falling back to
`CLAUDE.md`), so the rules can live there instead — see
[opencode rules](https://opencode.ai/docs/rules/).

## Configuration

Config is resolved from four layers, in priority order:

1. **Environment variables** — `VIKUNJA_URL`, `VIKUNJA_TOKEN`, `VIKUNJA_PROJECT_ID`
2. **Repo-local env file** `.vikunja-mcp.env` (`KEY=VALUE` lines, same
   directory as `.vikunja-mcp.toml`, found by the same walk-up) —
   per-project token for machines that work across multiple repos, without
   touching the user env file. **Never commit it** — add it to the
   consuming repo's `.gitignore`
3. **Repo file** `.vikunja-mcp.toml` (found by walking up from the cwd) —
   `url` and `project_id`, safe to commit (no secret)
4. **User env file** `~/.config/vikunja-mcp/env` (`KEY=VALUE` lines,
   `chmod 600`) — the usual place for `VIKUNJA_TOKEN`

```toml
# .vikunja-mcp.toml (commit this)
[tracker]
url = "https://vikunja.example.com"
project_id = 12
project = "My Project"   # informational label; not used for lookup
```

```
# .vikunja-mcp.env (same directory as .vikunja-mcp.toml — gitignore it, never commit)
VIKUNJA_TOKEN=tk_xxxxxxxxxxxx
```

```
# ~/.config/vikunja-mcp/env (chmod 600, keep out of git)
VIKUNJA_URL=https://vikunja.example.com
VIKUNJA_TOKEN=tk_xxxxxxxxxxxx
VIKUNJA_PROJECT_ID=12
```

Note: the token is *never* read from `.vikunja-mcp.toml` — only from
`VIKUNJA_TOKEN` (env), `.vikunja-mcp.env`, or the user env file — so
`.vikunja-mcp.toml` has nothing secret in it and is safe to commit.
`.vikunja-mcp.env` uses the same `KEY=VALUE` parsing rules as the user env
file (quoted values, trailing `# comment` stripping on unquoted ones), and
all three keys (`VIKUNJA_URL`/`VIKUNJA_TOKEN`/`VIKUNJA_PROJECT_ID`) may
appear in it.

## Tools

| Tool | Gate / behavior |
| --- | --- |
| `next_task()` | Returns your active task in Design/Build first (incl. one bounced back from Your Call), else the top-priority free task in Queue. Never returns Backlog or `blocked`-labeled tasks. One task at a time. |
| `claim(task_id)` | Queue → Design only. Assign-then-verify: assigns you, re-reads the task, and backs off if someone else was assigned in the same window (race lost — call `next_task` again). |
| `get_task(task_id)` | Dossier: description, stage, assignees, labels, full comment thread. |
| `comment(task_id, text)` | Adds a progress note to the task's comment log. |
| `advance(task_id, to, spec=, worklog=, evidence=)` | `to="build"`: Design → Build, requires `spec`. `to="review"`: Build → Review, requires `worklog` + `evidence`. `to="done"` is always rejected — Done is human-only. Task must be assigned to you. |
| `call_human(task_id, question)` | Design/Build → `Your Call` (aka `YC`). Keeps your assignment (not a review step, not an external block); posts `question` as a comment. |
| `return_task(task_id, reason)` | For external blockers (no access, missing dependency, someone else's service down). Unassigns you, adds a `blocked` label, moves the task to Backlog for human re-triage. |
| `decompose(task_id, subtasks)` | Requires ≥2 subtasks (each needs a `title`). Creates each as a new task with a `parenttask` relation to the parent and drops it in Queue. Parent is unassigned, labeled `epic`, and moved to Backlog. |
| `file_task(title, description=, priority=, related_task_id=)` | Files an out-of-scope finding (a bug or bit of tech-debt spotted mid-work) into **Backlog** for human triage — not Queue. Stamps a `[filed-by-agent]` comment marker and, when `related_task_id` is given, links it to the originating task with a `related` relation. Distinct from `decompose`, which splits your *own* oversized task into Queue subtasks. |

## Project setup

```bash
VIKUNJA_TOKEN=<admin token> vikunja-mcp setup --project NAME [--share user:read|write|admin ...] --url URL
```

Creates the project if it doesn't exist (matched by title), then
creates/reconciles the seven canonical buckets in order, migrates known
default-Vikunja buckets (`Todo`/`To-Do`/`To-do` → Queue, `Doing` → Build,
moving their tasks), removes empty non-canonical buckets (leaves non-empty
unknown ones alone with a warning), sets Backlog as the default bucket and
Done as the done bucket, applies any `--share` grants, and prints ready-to-
commit `.vikunja-mcp.toml` + `.mcp.json` + `opencode.json` snippets.

```bash
vikunja-mcp install-skill
```

Copies the packaged tracker skill (queue discipline, comment-trail
expectations, `call_human` vs `return_task`) to both
`~/.claude/skills/tracker/SKILL.md` (Claude Code) and
`~/.config/opencode/skills/tracker/SKILL.md` (opencode), and prints the
`instructions` line to wire the latter into an `opencode.json`.

For Claude Code it *also* auto-provisions a conditional **`SessionStart` hook**
(a small `~/.claude/hooks/vikunja-tracker-orchestrator.sh` registered under
`hooks.SessionStart` in `~/.claude/settings.json`) so you don't have to paste an
orchestrator redirect into each project's `CLAUDE.md`. On every session start the
hook walks up from the cwd for a `.vikunja-mcp.toml`; **only** inside a
tracker-configured project it injects a short standing-context that redirects a
bare `/loop` to the tracker orchestrator (drain the Queue: `next_task` → `claim`
→ dispatch a per-task agent) instead of Claude Code's generic autonomous-loop
default — outside a tracker project it emits nothing, so it never affects your
other repos. It's dependency-free (POSIX `sh`, no `jq`), idempotent (re-running
never duplicates the entry and preserves your other hooks and settings), and
takes effect after you restart Claude Code. The full playbook still lives in the
`tracker` skill; the hook just points `/loop` at it.

## Releases: the `stable` channel

Consumers subscribe to the moving `stable` branch with `--refresh-package`
in `.mcp.json` — every MCP server start re-resolves the channel, so releases
roll out to all repos automatically (no per-consumer bumps, no PR bots).
Immutable `vX.Y.Z` tags remain the release history and rollback points.
Admin one-offs (`setup`, `install-skill`) may use `@main`.

**Patch releases are automatic**: every green push to `main` triggers a CI job
that bumps the patch version in both files, tags `vX.Y.Z`, and moves `stable`
onto it — so `stable` always tracks the latest green `main` with no manual step.

Manual steps remain for **minor/major bumps** and **rollback**:

1. bump `version` in `pyproject.toml` and `__version__` in `src/vikunja_mcp/__init__.py`
   (only for a minor/major — patches are automated)
2. commit, wait for CI green
3. `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`
4. `git branch -f stable vX.Y.Z && git push -f origin stable`  # rollout (rollback: same, older tag)

## Development

```bash
uv sync
uv run ruff check .
uv run pytest tests/unit -q
```

Integration tests exercise a real Vikunja instance and are skipped unless
`VIKUNJA_TEST_URL` is set:

```bash
docker run -d --name vikunja -p 3456:3456 \
  -e VIKUNJA_DATABASE_TYPE=sqlite -e VIKUNJA_DATABASE_PATH=/tmp/vikunja.db \
  -e VIKUNJA_FILES_BASEPATH=/tmp/files \
  -e VIKUNJA_SERVICE_JWTSECRET=ci-secret \
  -e VIKUNJA_SERVICE_PUBLICURL=http://localhost:3456/ \
  -e VIKUNJA_SERVICE_ENABLEREGISTRATION=true \
  vikunja/vikunja:2.3.0
timeout 60 bash -c 'until curl -sf http://localhost:3456/api/v1/info; do sleep 1; done'

VIKUNJA_TEST_URL=http://localhost:3456 uv run pytest tests/integration -q
```

Vikunja rate-limits `/login` (10 requests/60s shared with `/register`); the
integration `conftest` retries on HTTP 429 with backoff (up to ~150s worst
case across a full run) — expected, not a bug.

## License

MIT — see [LICENSE](LICENSE).
