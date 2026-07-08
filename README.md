# vikunja-mcp

A workflow-level MCP server for [Vikunja](https://vikunja.io) — not a generic
task CRUD wrapper. It exposes a small set of tools that push an agent through
a fixed pipeline, and the gates (what's allowed from which stage, what
evidence is required) live in the tools themselves, not in prompts:

```
Backlog → Queue → Design → Build → Review → Done
                     ↕        ↕
                     Call to Human
```

- `Backlog` and `Done` are human territory (triage in, sign-off out) —
  agents never move a task into `Done` themselves.
- `Queue → Design → Build → Review` is the agent loop: claim, spec, build,
  hand off for review.
- `Call to Human` is a side branch reachable from `Design`/`Build` when an
  agent needs a decision or input; the human answers and moves it back.

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
      "args": ["--from", "git+https://github.com/ufna/vikunja-mcp@v0.1.0", "vikunja-mcp"]
    }
  }
}
```

## Configuration

Config is resolved from three layers, in priority order:

1. **Environment variables** — `VIKUNJA_URL`, `VIKUNJA_TOKEN`, `VIKUNJA_PROJECT_ID`
2. **Repo file** `.vikunja-mcp.toml` (found by walking up from the cwd) —
   `url` and `project_id`, safe to commit (no secret)
3. **User env file** `~/.config/vikunja-mcp/env` (`KEY=VALUE` lines,
   `chmod 600`) — the usual place for `VIKUNJA_TOKEN`

```toml
# .vikunja-mcp.toml (commit this)
[tracker]
url = "https://vikunja.example.com"
project_id = 12
project = "My Project"   # informational label; not used for lookup
```

```
# ~/.config/vikunja-mcp/env (chmod 600, keep out of git)
VIKUNJA_URL=https://vikunja.example.com
VIKUNJA_TOKEN=tk_xxxxxxxxxxxx
VIKUNJA_PROJECT_ID=12
```

Note: the token is *never* read from the repo toml — only from
`VIKUNJA_TOKEN` (env) or the user env file — so `.vikunja-mcp.toml` has
nothing secret in it and is safe to commit.

## Tools

| Tool | Gate / behavior |
| --- | --- |
| `next_task()` | Returns your active task in Design/Build first (incl. one bounced back from Call to Human), else the top-priority free task in Queue. Never returns Backlog or `blocked`-labeled tasks. One task at a time. |
| `claim(task_id)` | Queue → Design only. Assign-then-verify: assigns you, re-reads the task, and backs off if someone else was assigned in the same window (race lost — call `next_task` again). |
| `get_task(task_id)` | Dossier: description, stage, assignees, labels, full comment thread. |
| `comment(task_id, text)` | Adds a progress note to the task's comment log. |
| `advance(task_id, to, spec=, worklog=, evidence=)` | `to="build"`: Design → Build, requires `spec`. `to="review"`: Build → Review, requires `worklog` + `evidence`. `to="done"` is always rejected — Done is human-only. Task must be assigned to you. |
| `call_human(task_id, question)` | Design/Build → `Call to Human`. Keeps your assignment (not a review step, not an external block); posts `question` as a comment. |
| `return_task(task_id, reason)` | For external blockers (no access, missing dependency, someone else's service down). Unassigns you, adds a `blocked` label, moves the task to Backlog for human re-triage. |
| `decompose(task_id, subtasks)` | Requires ≥2 subtasks (each needs a `title`). Creates each as a new task with a `parenttask` relation to the parent and drops it in Queue. Parent is unassigned, labeled `epic`, and moved to Backlog. |

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
commit `.vikunja-mcp.toml` + `.mcp.json` snippets.

```bash
vikunja-mcp install-skill
```

Copies the packaged tracker skill (queue discipline, comment-trail
expectations, `call_human` vs `return_task`) to `~/.claude/skills/tracker/SKILL.md`.

## Releases

Consumers pin a release tag in `.mcp.json` (deterministic updates: bump the
tag in the consuming repo, developers pick it up via `git pull`). Admin
one-offs (`setup`, `install-skill`) may use `@main`.

Cutting a release:

1. bump `version` in `pyproject.toml` and `__version__` in `src/vikunja_mcp/__init__.py`
2. commit, wait for CI green
3. `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`
4. bump the tag in consumers' `.mcp.json` (hgdev-infra, dogiators/front, ...)

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
