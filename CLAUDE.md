# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What This Is

Workflow-level MCP server for a self-hosted [Vikunja](https://vikunja.io)
tracker — NOT a CRUD wrapper. The pipeline and its gates ARE the product:

```
Backlog → Queue → Design → Build → Review → [human] → Done
                     ↕        ↕
                  Your Call              (+ independent bug review in Review)
```

9 agent tools (`next_task`, `claim`, `get_task`, `comment`, `advance`,
`call_human`, `return_task`, `decompose`, `review_task`); agents can never
move a task to Done — that transition is human-only by design. Gates are
guardrails for agents; the real security boundary is the scoped API token.

## Commands

```bash
uv sync                                   # env (Python 3.11+, uv)
uv run pytest tests/unit -q               # 69 unit tests (FakeAPI, MockTransport)
uv run ruff check .                       # lint (line-length 100)
uv run vikunja-mcp --version              # smoke

# integration — real Vikunja 2.3.0 in docker (skipped without VIKUNJA_TEST_URL):
docker run -d --name vikunja-test -p 3456:3456 \
  -e VIKUNJA_DATABASE_TYPE=sqlite -e VIKUNJA_DATABASE_PATH=/tmp/vikunja.db \
  -e VIKUNJA_FILES_BASEPATH=/tmp/files -e VIKUNJA_SERVICE_JWTSECRET=integration-test-secret \
  -e VIKUNJA_SERVICE_PUBLICURL=http://localhost:3456/ -e VIKUNJA_SERVICE_ENABLEREGISTRATION=true \
  vikunja/vikunja:2.3.0
until curl -sf http://localhost:3456/api/v1/info >/dev/null; do sleep 1; done
VIKUNJA_TEST_URL=http://localhost:3456 uv run pytest tests/integration -q
docker rm -f vikunja-test
```

## Architecture

- `src/vikunja_mcp/config.py` — 4-layer config: env (`VIKUNJA_URL/TOKEN/PROJECT_ID`)
  > repo-local `.vikunja-mcp.env` (same dir as the toml, found by the same walk-up,
  gitignored) > repo `.vikunja-mcp.toml` (walk-up from cwd) > `~/.config/vikunja-mcp/env`.
  Token is NEVER read from the repo toml (so it can't be committed and used).
- `src/vikunja_mcp/api.py` — REST client. **Vikunja gotchas are codified here:
  PUT = create, POST = FULL-REPLACE update** → every update is
  read-modify-write; kanban view updates must always send
  `bucket_configuration_mode="manual"` + `position` + `title` + `view_kind`
  or the board loses its columns; board fetch paginates per bucket
  (page size read from `/info`'s `max_items_per_page`, `_PAGE_SIZE_FALLBACK`
  when unavailable, dedupe by bucket+task id).
- `src/vikunja_mcp/workflow.py` — the product rules: stages, gates,
  assign-then-verify claim (with self-heal), review offering (verdict vs
  worklog timestamps), comment markers `[claim] [spec] [worklog] [нужен
  человек] [blocked] [decompose] [review]` plus mutually-exclusive verdict
  labels `reviewed`/`review-failed` (push-review of bug fixes:
  `advance(to='review')` nudges `review_needed` and resets `review-failed`).
  Behavior changes belong here, with a unit test per gate.
- `src/vikunja_mcp/server.py` — thin FastMCP wiring; `_tool` decorator
  converts `WorkflowError/ConfigError/VikunjaError/httpx.HTTPError` into
  `{"error": ...}` tool results (never crashes the stdio server). Tool
  docstrings are agent-facing rules — treat them as UX copy, keep them
  prescriptive (when to call, not just what it does).
- `src/vikunja_mcp/setup_cmd.py` — `vikunja-mcp setup` (idempotent board
  reconcile: canonical buckets + ORDER via positions, `Todo→Queue` /
  `Doing→Build` migration, shares) and `install-skill`.
- `src/vikunja_mcp/skills/tracker/SKILL.md` — process rules for agents
  (queue discipline, orchestrator-dispatches-subagents, report format,
  independent bug review). Ships inside the wheel; root `skills` is a symlink.

## Testing Philosophy

TDD. Unit tests drive `Workflow` through `tests/unit/fakes.py::FakeAPI` —
an in-memory mirror of the real client's full surface (keep it 1:1 when you
extend `VikunjaAPI`; it seeds Vikunja's auto To-Do/Doing/Done buckets on
create_project, enforces delete-only-empty buckets, monotonic comment
`created`). Integration tests hit a real container and exist to catch what
the fake can't: permission scopes, pagination shape, relation shapes,
`/login` rate limit (10/60s — conftest retries 429).

## Releases: the `stable` channel

Consumers' `.mcp.json` subscribes to the moving `stable` branch with
`--refresh-package` → every session start re-resolves it (auto-rollout,
no per-consumer bumps). Immutable `vX.Y.Z` tags = history + rollback.

Release: bump `version` in `pyproject.toml` AND `__version__` in
`src/vikunja_mcp/__init__.py` → commit, CI green →
`git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z` →
`git branch -f stable vX.Y.Z && git push -f origin stable`.
`stable` moves ONLY to tagged, CI-green commits. Rollback = same command
with an older tag.

## Dogfood: this repo's own tasks

This project tracks itself in the same tracker (project `vikunja-mcp`,
id 10 — see `.vikunja-mcp.toml`). Follow the tracker flow for real work
here: `next_task` → claim → spec → dispatch a fresh sub-agent per task →
structured report → commit+push → Review (bugs get independent agent review).
Each task lands as its own commit on `main`, pushed at `advance(to='review')`
time (`… (tracker #N)`, `evidence` = the sha) — a completed task commits and
pushes itself; only the tag + `stable` move wait for the release task. The repo
is PUBLIC — this repo's own token is supplied via the repo-local
`.vikunja-mcp.env` (sits next to `.vikunja-mcp.toml`, gitignored), never
committed.

## Live instance notes

- Tracker: `https://tracker.zz.hgdev.com` (public) / `tracker.vpn.hgdev.com`
  (overlay). Board reconcile of a human-owned project 403s on the view
  config — admin share or agent-owned projects only (details in
  hgdev-infra `docs/vikunja-mcp-usage.md`).
- Scoped tokens REQUIRE permission groups `other:user` and
  `projects:views_buckets` (401 on all tools otherwise); minting lives in
  hgdev-infra `roles/vikunja/files/vikunja-bootstrap.py`.
