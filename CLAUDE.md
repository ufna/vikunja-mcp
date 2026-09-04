# CLAUDE.md

Guidance for Claude Code when working in this repository.

**How this file is organised — read this first.** What lives here are RULES: short,
imperative, binding. The evidence under them — measurements, constructed stands,
refuted wordings, the per-card post-mortems — lives in `docs/dossier/*.md`, one file
per subsystem, linked at the end of each section and from the module's own docstring.

Two consequences, both binding:

- **Changing a module? Read its dossier first.** The rule here is short precisely
  because the proof is there. This repo has repeatedly "fixed" a guard by reasoning
  instead of measuring, several rounds running; if a rule looks redundant or
  simplifiable, the answer is almost certainly in the dossier. The reviewer checks
  that it was read.
- **A new card's post-mortem goes to the card and to the dossier, NOT here.** This
  file enters EVERY session's context — the orchestrator's, every per-task agent's
  and every reviewer's; at `wip_limit = 3` that is ~7 contexts per round. Edit here
  only when the RULE itself changed. The size of both rulebooks is pinned by
  `tests/unit/test_rulebook_size.py`, which is a ratchet: when it shrinks, lower the
  ceiling in the same commit.

## What This Is

Workflow-level MCP server for a self-hosted [Vikunja](https://vikunja.io)
tracker — NOT a CRUD wrapper. The pipeline and its gates ARE the product:

```
Backlog → Queue → Design → Build → Review → [human] → Done
                     ↕        ↕
                  Your Call         (+ independent review of EVERY task in Review)
```

14 agent tools (`next_task`, `claim`, `get_task`, `comment`, `advance`,
`call_human`, `return_task`, `decompose`, `file_task`, `review_task`,
`attach_file`, `download_attachment`, `handoff`, `transfer_task`); agents can
never move a task to Done —
that transition is human-only by design. Gates are guardrails for agents; the
real security boundary is the scoped API token.

## Commands

```bash
uv sync                                   # env (Python 3.11+, uv)
uv run pytest tests/unit -q               # 500+ unit tests (FakeAPI, MockTransport)
uv run ruff check .                       # lint — wrap at 100, RED above 110 (see below)
uv run vikunja-mcp --version              # smoke
uv run vikunja-mcp claimable              # one JSON line: is there claimable work for this
                                          # token? (hgdev-acp hub's pre-launch idle check)

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

**Line length is TWO numbers, and only one of them is a gate (tracker #669).** Wrap at
**100** — that is `line-length`, the formatter's target and what this repo wraps to by
hand. CI goes red at **111**: `E501` is selected with `max-line-length = 110`. The gap is
honest slack, not an oversight, and three things follow. **The band 101-110 is convention
with nothing behind it** — a 103-character line ships green, so keep measuring your own
additions rather than reading a green `ruff check` as "wrapped correctly". **Measure in
CHARACTERS, never bytes**: in python that is `len(line)`, not `len(line.encode())`; the
shell reflex (`awk '{print length($0)}'`, `wc -c`) is wrong here, where the prose is full
of em-dashes (3 bytes) and Cyrillic (2 bytes each). **And "red at 111" has exactly TWO
exemptions ruff applies ON ITS OWN** — a `# noqa` silences it too, but that is an opt-out
someone writes, not a decision the rule makes: (1) a line holding fewer than two
whitespace-separated chunks, and (2) a line whose LAST chunk contains the literal `://`
while the rest fits the limit. **It is a ratchet, not a preference** — lowering it further
is the intended direction, and the decision point is the `_HARD_LIMIT` assertion in
`tests/unit/test_line_length_gate.py`, which `pyproject.toml` must agree with.

→ **Dossier: `docs/dossier/linting.md`** — why two numbers, what the 120→110 step cost,
why running the formatter would not have caught the defect that created the gate, and the
measured shape of both exemptions.

## Architecture

- `src/vikunja_mcp/config.py` — 4-layer config: env (`VIKUNJA_URL/TOKEN/PROJECT_ID`)
  > repo-local `.vikunja-mcp.env` (same dir as the toml, found by the same walk-up,
  gitignored) > repo `.vikunja-mcp.toml` (walk-up from cwd) > `~/.config/vikunja-mcp/env`.
  **The token is NEVER read from the repo toml** (so it can't be committed and used);
  optional `VIKUNJA_NOTIFY_WEBHOOK` (`notify.py` — best-effort Slack-shaped ping when
  `call_human` parks a card) is a secret of the same class: env layers only, never the
  toml. **`wip_limit` sits on the opposite side of that split — repo toml ONLY, never
  env**, because it is committed TEAM POLICY: how many Design/Build tasks one token may
  CLAIM into at once. **Unset means `DEFAULT_WIP_LIMIT` = 3, not "no gate"** (tracker
  #524); precedence is explicit `wip_limit` → else 1 when `enforce_single_wip = true` →
  else 3, resolved in `workflow._effective_wip_limit`. `wip_limit = 0` is a `ConfigError`:
  "no limit" is deliberately not expressible. **It is a gate on ONE transition (`claim`),
  not an invariant on the active count** (tracker #529) — a card re-enters Build without
  passing it, so `wip.active` legitimately EXCEEDS `wip.limit`, and that is correct,
  because rework must be receivable at the limit. `worktree_root` /
  `VIKUNJA_WORKTREE_ROOT` is MACHINE-local, so there the env layers DO win over the toml.
  **`require_review_independence` is a third toml-ONLY key, default FALSE** (tracker #37).
  In a SOLO setup one scoped token is the whole fleet, so the ABSENCE of an authorship
  check is the CONDITION OF OPERATION; independence is carried by the agents' separated
  CONTEXTS, which nothing server-side can observe. Turn it on without a second identity
  and NOBODY can review anything. **`language = "en" | "ru"` is a FOURTH toml-only key,
  default `en`** (tracker #1165) — an unknown value is a `ConfigError`, on the `wip_limit = 0`
  precedent. It governs the prose the tool authors onto a card (the two-COLUMN table in
  `cardtext.py`, ONE module by rule) and — the larger half — rides in every `next_task` payload
  so the AGENT writes its spec/worklog/review report in the same language. **It NEVER governs a
  marker.** Two of the twelve are literally PARSED — the review offering compares the last
  `startswith("[worklog]")` comment against the last `startswith("[review]")` one — so a
  per-language spelling THERE drops every card written under the other setting out of the
  offering, silently; the other ten are frozen with them so the vocabulary is not
  half-translated.
  **`siblings = { backend = 17 }` is a FIFTH toml-only key, default `{}`** (tracker #1179) — the
  OTHER tracker projects this repo may hand work to, by name. Same class and same reason as the
  four above: which boards this repo can push work onto is committed policy, never widened by one
  machine's env. **It is NOT a gate** — the scoped token still decides what a cross-project write
  may touch, and `file_task`'s free-form `project_id` is deliberately left un-narrowed by it. What
  it buys is DISCOVERABILITY, and that is the whole feature: it rides in every `next_task` payload,
  because an agent in `dogiators-front` had no way to learn a `dogiators-backend` existed at all,
  let alone that it was id 17 — its own toml named neither. Refused by name: a non-table value, a
  blank name, a non-int or bool id (TOML `true` would silently address project 1), a non-positive
  id, THIS project's own id (a self-handoff deadlocks), and two names for one id (the registry is
  read id->name too, for provenance).
  → **Dossier: `docs/dossier/config.md`**
- `src/vikunja_mcp/api.py` — REST client. **Vikunja gotchas are codified here: PUT =
  create, POST = FULL-REPLACE update** → every update is read-modify-write; kanban view
  updates must always send `bucket_configuration_mode="manual"` + `position` + `title` +
  `view_kind` or the board loses its columns; board fetch paginates per bucket (page size
  read from `/info`'s `max_items_per_page`; when the server never says, the size is
  UNKNOWN — **never guessed** — and the loop pages until no NEW task arrives). There is
  deliberately no fallback constant: a guessed size silently TRUNCATED the board, and a
  truncated board told `--gc` a live task was gone, so it reaped a live worktree (tracker
  #543). That branch is also BOUNDED and hitting the bound RAISES rather than returning a
  short board (tracker #548): a read that cannot finish must fail LOUDLY.
  → **Dossier: `docs/dossier/api.md`**
- `src/vikunja_mcp/workflow.py` — the product rules: stages, gates, assign-then-verify
  claim (with self-heal), review offering (verdict vs worklog timestamps), comment markers
  `[claim] [spec] [worklog] [needs-human] [blocked] [decompose] [review] [attach]` plus
  mutually-exclusive verdict labels `reviewed`/`review-failed` (push-review of EVERY task,
  not just bug fixes — tracker #117: `advance(to='review')` nudges `review_needed` +
  `review_kind` (`'bug'`|`'change'`) for any card WITHOUT the `epic` label, and resets a
  stale verdict). An epic container is the lone exception: its code lives in its children.
  **`Icebox` (#1640) is the eighth stage and the ONE OPTIONAL column**: `_bucket` checks
  `REQUIRED_STAGES`, NEVER `STAGES` — widening it fails every tool on every board that has not
  run `setup`, at the next `stable` resolve. The COLUMN gates (it is not in `NEXT_TASK_STAGES`);
  the `icebox` LABEL never does — that filter drops a card SILENTLY, and a human's drag into
  Queue is an instruction, not an oversight. Agent entrance: `file_task(icebox=True)`; the exit
  is a HUMAN's — `_find_task` refuses Icebox beside Done (reads opt out), because a drag into
  the freezer KEEPS the assignee, so `decompose` from there put children back in Queue.
  **A predecessor may live in ANOTHER project, and the gate must resolve it there** (tracker
  #1179). Vikunja relations are task-to-task and cross projects freely — measured: a card moved
  between projects kept a `blocked` link to one left behind — but `_unfinished_predecessors`
  resolved stages against THIS project's board only, so a neighbour's card fell into the
  "genuinely gone -> not a blocker" branch and the card was released with its blocker untouched.
  Measured with a same-project control in the same round: control REFUSED/withheld, cross
  ALLOWED/OFFERED. Off-board predecessors now resolve via `get_task` + that project's board, and
  **every unresolvable one that REACHES the guard BLOCKS rather than vanishes** (403, no kanban
  view, not in any bucket): unknown must never be spelled "gone". **A predecessor whose whole
  PROJECT is invisible to the token never reaches it** (tracker #1198) — measured with a
  two-reader control, the server strips it from `related_tasks` first, so that card is released
  with its blocker untouched. Accepted limit, not a defect to fix quietly.
  `handoff` and `transfer_task` are the two ways a card crosses the boundary —
  `[handoff]` parks YOUR card in Queue blocked on a new one in the neighbour's Backlog (no
  `blocked` LABEL: the label suppresses the offer permanently and would defeat the self-clearing
  resume), `[moved]` carries the card itself over. **Both land in the
  target's BACKLOG, never its Queue**, and both are shut from Review and Your Call, where
  something is pending on THIS board.
  **Behavior changes belong here, with a unit test per gate.**
  → **Dossier: `docs/dossier/workflow.md`**
- `src/vikunja_mcp/server.py` — thin `MCPServer` wiring (the mcp 2.0 SDK; FastMCP is
  gone — `version=` is passed explicitly because `MCPServer` defaults it to `""`); `_tool`
  decorator converts `WorkflowError/ConfigError/VikunjaError/httpx.HTTPError` into
  `{"error": ...}` tool results (never crashes the stdio server). **Tool docstrings are
  agent-facing rules — treat them as UX copy, keep them prescriptive** (when to call, not
  just what it does). **The MCP SDK is imported LAZILY** (`_server()`, the lone import
  site) — never move it back to module scope: `claimable`/`workspace`/`setup`/
  `install-skill`/`--version` don't speak MCP and would pay ~0.43s of SDK import each,
  worst on `claimable`, which hgdev-acp spawns per poll tick.
- `src/vikunja_mcp/setup_cmd.py` — `vikunja-mcp setup` (idempotent board reconcile:
  canonical buckets + ORDER via positions, `Todo→Queue` / `Doing→Build` migration, shares)
  and `install-skill` (copies the packaged SKILL.md **and its `references/`** for Claude
  Code + opencode AND auto-provisions a conditional `SessionStart` hook — a
  dependency-free POSIX-`sh` script registered in `~/.claude/settings.json` that, ONLY
  inside a tracker project, injects the orchestrator standing-context so a bare `/loop`
  drains the Queue). `sync_installed_artifacts` self-heals those on **MCP server start**
  (called from `server.main`, so a moving-`stable` rollout refreshes them as automatically
  as the code): refresh-only (rewrites an installed copy only when it exists and differs —
  never provisions `~/.claude`), best-effort (never raises → never crashes the stdio
  server, never writes stdout), opt out with `VIKUNJA_MCP_NO_SKILL_SYNC`.
- `src/vikunja_mcp/claimable_cmd.py` — `vikunja-mcp claimable`: the sibling-EXPORTED
  claimable verdict (ONE JSON line `{"claimable","kind","task_id"}`, exit 0 = the check
  ran / 1 = it failed) that hgdev-acp's repo-agent loop spawns as its pre-launch idle
  check. It runs the REAL `Workflow.next_task()` — zero gate drift by construction, which is a
  property of the CONSTRUCTION as much as of the call, so **a `Config` key `Workflow` reads on a
  path is wired at EVERY site that builds one** — three here (tracker #1169: one missing kwarg
  let `claimable` disagree with the server's own `next_task`). Running that real `next_task`
  is also what makes it **READ-ONLY BY CONTRACT**: the hub polls it per loop tick, so a side
  effect there becomes a per-poll tracker mutation. **The JSON keys and the exit-code split are a
  public cross-repo contract**; changing them breaks the hub's check. **STDERR is the
  opposite kind of channel — a breadcrumb trail, explicitly NOT a contract** (tracker
  #536): ONE line, one token per tracker request, written BEFORE the request and flushed
  per token, so an unterminated line is precisely "killed on this token". ON BY DEFAULT
  with a `VIKUNJA_MCP_NO_TRACE=1` opt-out. **Do not let it grow a consumer** — its shape
  may change in any release.
  → **Dossier: `docs/dossier/claimable.md`** (the $105/day dogfood regression that created
  this command, and why the trail must stay terse — a 200-BYTE cap in the consumer)
- `src/vikunja_mcp/workspace_cmd.py` — `vikunja-mcp workspace`: per-task git worktrees for
  the parallel drain (`wip_limit > 1`). **The ONLY module in the package that runs git** —
  `server.py`/`workflow.py`/`api.py` stay git-free by rule, not by accident (a subprocess
  in the stdio server's path is a new class of crash). `git worktree add` refuses a branch
  that is already checked out, so each agent gets its own throwaway `task/<id>` branch and
  pushes with `git push origin HEAD:main` — "one task = one commit on main". Create and
  `--release` need neither the tracker nor a token (create is not offline, though — it runs
  `git fetch origin`); only `--gc` reads the tracker, because only the board can say
  whether the task behind an orphaned tree is still alive. Every entry point canonicalises
  to the MAIN worktree first, so create / release / gc agree on paths and config even when
  invoked from INSIDE a linked tree.
  **`--gc` also FAST-FORWARDS that main worktree** (`sync_main_checkout`, optional
  `main_checkout` key, `VIKUNJA_MCP_NO_MAIN_SYNC=1` to opt out): nothing else in the drain
  moves it, so the folder a human works in falls behind monotonically (measured: 58 commits
  over ONE session). It is **fast-forward ONLY and refuses rather than resolves** —
  `reset --hard`, `checkout -f`, `clean`, `stash`, `pull`, a bare `merge` and switching
  branches are all deliberately absent and must stay absent, because that is somebody
  else's working directory. What protects uncommitted work is GIT, not a guard of ours.
  **Safety invariant** taken from hgdev-acp's reaper: push OK → remove, push FAIL → KEEP
  (dirty, unpushed, or reachable-from-no-ref ⇒ reported, never destroyed).
  **Housekeeping is never how an agent's work disappears — except for IGNORED files, and
  that exception is real, measured, and deliberately NOT closed** (#710, #764): `dirty` is
  `git status --porcelain`, which does not report ignored paths at all, so a tree holding
  `shot-<id>.png` or `.playwright-mcp/<id>/` reads CLEAN and is destroyed with them. The
  human's decision is REPORT, NEVER HOLD — `removed_ignored`, `overwritten_ignored`,
  `half_applied` — so **what protects an agent is SKILL.md's carry-it-out-of-the-tree-
  before-`advance` rule and not this code.**
  **Those loss keys read in ONE direction only.** Present ⇒ something was destroyed.
  Absent ⇒ NOT a proof that nothing was. Present ⇒ NOT a proof that the list is COMPLETE.
  That third reading has ONE key that states it outright since tracker #940:
  **`overwritten_ignored_incomplete`** counts the PLACES the probe could not look at — not the
  files lost, since one denied directory hides a whole subtree — and it is emitted even with no
  `overwritten_ignored` beside it, which is the case that looks safest and is not. Its channel
  needs no permissions at all: git addresses paths RELATIVE to the checkout while the probe
  walked them ABSOLUTE, so between those two lengths git destroys a file that `os.scandir`
  cannot even see. It closes the SILENCE, never the loss.
  **Only ONE of the two refusal channels is coded, and the split is deliberate.** Every
  `--release`/`--gc` refusal is exit 0 + `released: false` + a machine-readable `code`
  beside the prose `reason` ("the tool RAN and is protecting your work"), and `--gc` GRADES
  those codes into `kept` (a human should look) and `expected` (two routine states); an
  unknown code lands in `kept`, because noisy beats quiet, and a `released` entry can still
  need action (`branch_deleted: false` + `warning`), so read `kept` AND scan `released`. On
  create the channel is the other one: `{"error": …}` + exit 1 and no `code` at all ("the
  tool could NOT do the work"), so there the EXIT CODE is the whole machine-readable
  verdict. Main-checkout codes are `MAIN_SYNC_*` and NOT `CODE_*` on purpose: that prefix
  is the closed per-worktree vocabulary `_keep_is_expected` grades, and these never reach
  the grader.
  → **Dossier: `docs/dossier/workspace.md`** — the most important of the nine. It carries
  `half-applied` (`merge --ff-only` is NOT atomic), the typechange-onto-a-live-gitlink gap,
  why the probes read the TREE and never git's messages, and why `git diff` had to become
  `diff-index`. **Do not touch a guard in this file without reading it.**
- `src/vikunja_mcp/skills/tracker/SKILL.md` — process rules for agents (queue discipline,
  orchestrator-dispatches-subagents, report format, independent review of EVERY task, and
  — when `wip.limit > 1` — the parallel drain). Ships inside the wheel; root `skills` is a
  symlink; its own evidence lives in `skills/tracker/references/*.md`, loaded on demand.
  **THIS file is the authoritative copy** — `sync_installed_artifacts` refreshes the
  installed `~/.claude/skills/tracker/SKILL.md` once, at MCP server start, and a session's
  server starts once, so the text the `tracker` skill serves is frozen at session start
  while this one moves with every landing. Working here, read it from the worktree; a task
  whose deliverable IS a SKILL.md edit therefore cannot verify itself by invoking the skill
  (it gets the pre-session text back and reads as "my edit did not take") — `grep`/`diff`
  the worktree file and say so in the `[worklog]`.

## Testing Philosophy

TDD. Unit tests drive `Workflow` through `tests/unit/fakes.py::FakeAPI` — an in-memory
mirror of the real client's full surface (keep it 1:1 when you extend `VikunjaAPI`; it
seeds Vikunja's auto To-Do/Doing/Done buckets on create_project, enforces
delete-only-empty buckets, monotonic comment `created`). Integration tests hit a real
container and exist to catch what the fake can't: permission scopes, pagination shape,
relation shapes, `/login` rate limit (10/60s — conftest retries 429).

**The unit count above is a FLOOR (`500+`), and must stay one — never re-pin it to an
exact figure.** Its only job is a tripwire: a mistyped path makes `pytest` select NOTHING
and print "no tests ran", which looks very much like a pass. A floor catches that and
survives every landing; an exact count is stale by construction here. **Capture your own
count from your own run — a figure read out of this file was only ever true at the sha
that wrote it.** Where a figure genuinely needs precision, name the SHA it was measured
at, because **a DATE does not name a TREE**. Better still, where a reader will ACT on the
figure, assert the property instead of writing the number. Anchors written as `N at
`<sha>`` are checked by `tests/unit/test_measured_figure_anchors.py`, but only as a LABEL:
the commit must exist and be an ancestor of `HEAD`; the figure itself is never re-derived.

**A mutation sweep opens with an UNMUTATED CONTROL round on the SAME selection, and every
round count is a DELTA against it.** `N failed` is a kill count only if the same selection
failed ZERO times before a single mutation was applied, and nothing in a `-q` summary says
whether it did — card 594 swept in a tree where 30 tests failed constantly, so every row
came out inflated by exactly 30 and its headline was wrong by a factor of 16. So run the
control FIRST and WRITE ITS FAILED COUNT beside the round's: `control 0 failed; mutation 2
failed` still means something a month later. Record the FAILED count, never the pass total
— the total moves with every test the repo adds, the failed count does not.
"Beside" is enforced IN THE SAME PARAGRAPH (card 688): the scanner's unit is the paragraph, so a
control declared once at the top of a long section stops vouching for the rounds below the
next blank line. `tests/unit/test_mutation_sweep_contract.py` enforces that shape.

**And a control only helps if the ROUND was read right —
so READ A ROUND BY COUNTING `FAILED` LINES, never by the first `N failed` in pytest's
output.** This fails silently
DOWNWARD: pytest prints a failing test's own DOCSTRING inside the traceback, and in this
repo those docstrings are sweep records saying `control 0 failed`, so a naive parser finds
the MUTANT'S OWN PROSE. Measured, rounds that really failed 1, 1, 2 and 1 all read as
**0**; card #716 shipped 7, 2, 1 and 5 into a table as "0 failed". A sweep table that lies
in MINUS reads a live pin as BLIND, which invites deleting the pin. Count lines beginning
`FAILED `, count lines beginning `ERROR ` separately (a collection error is not a kill),
then CROSS-CHECK the selection size — pytest's `collected` line — against the control's.
One gotcha: `-q` prints NO `collected` line, so a script asking for it under `-q` gets
nothing back and the cross-check quietly never runs. Drop `-q` in a scripted sweep.

**Measure a tree-property figure AFTER the last rebase, immediately before the push.** The
mandatory `git fetch && git rebase origin/main && <re-run the gates> && git push` re-runs
the GATES and not the PROSE, so an absolute lands describing a tree that is in no history
— and at `wip_limit = 3` siblings are landing beside you, so staleness is the ordinary
case (tracker #888).

**A clean control does not mean the round MEASURED anything.** It is the cheapest detector,
not a complete one. CAUGHT: a constant background failure, and stale bytecode — cache
validity is the pair (source mtime in SECONDS, source size), and
`PYTHONDONTWRITEBYTECODE=1` stops Python WRITING bytecode, not READING it, so with a stale
`.pyc` on disk only deleting `__pycache__` moved the round. Do both. NOT CAUGHT: a mutation
that never reached the interpreter — a tree copied with `cp -R` drags `.venv` along, whose
editable `.pth` holds an ABSOLUTE path to the ORIGINAL `src`, after which control and
rounds are all green (card 646). **Build the sweep tree with `git clone --no-hardlinks`,
the one method SKILL.md also prescribes** — git does not track `.venv`, so it cannot
follow, and the clone is a REPOSITORY. `git archive` and `rsync -a --exclude .venv` stood
here until #1462 and are WITHDRAWN, for DIFFERENT failures: no `.git` means no
`git ls-files`, while an rsync of a linked worktree — where every per-task agent stands —
copies a `.git` FILE still addressing the LIVE repository and SHARES its index, so a write
there lands on the live branch and a sibling's staged file reddens your round. Print
`vikunja_mcp.__file__` in every round.
NOT CAUGHT EITHER, and the `collected` cross-check is what misses it: a stand where the
tests never RAN. `collected` counts SKIPPED items, so it reads the SAME on a sound stand
and a blind one. **Record SKIPPED beside FAILED**, treat a skip the control did not have
as a broken stand and not a result, and read a round only from output you PROVED exists —
`grep -c` over a deleted path prints NOTHING and exits 2, `2>/dev/null` hides why, and an
empty count reads as a zero. HALF-CAUGHT: a CONCURRENT WRITER in the same
tree — its mutant under your round is caught loudly, your restore under its round is not.
The remedy is a separate tree, not a stronger control.

**A prose claim that quotes a string as being IN this repository is checked** —
`tests/unit/test_repo_quotation_claims.py` reads the sentence around one of the assertive
idioms its `_CLAIM_TRIGGERS` names (read the SYMBOL, not the paraphrase beside it) and
requires every phrase quoted there to occur, whitespace-flattened, somewhere in what `git
ls-files` carries OUTSIDE THE FILE making the claim. Two consequences: **use one of those
idioms when you mean it** (the gate is exactly as wide as its vocabulary), and **when the
quotation is NOT meant to be a repo string** — another repository, a card description, a
tool's output, a wording quoted BECAUSE it was retracted — name it in that file's ratchet
with your reason beside it.

→ **Dossier: `docs/dossier/testing.md`** — how one sweep lied by 16× and in both
directions at once, the four measured forms of a blind control, why the stale-figure sweep
must not be line-fed (and why a cleverer grep does not fix it), and what the naive
quotation rule would have cost.

## Releases: the `stable` channel

Consumers' `.mcp.json` subscribes to the moving `stable` branch with `--refresh-package` →
every session start re-resolves it (auto-rollout, no per-consumer bumps). Immutable
`vX.Y.Z` tags = history + rollback.

**Patch releases are automatic** during active development. Every green push to `main`
fires the `release` job: `scripts/bump_version.py` bumps the patch in ALL THREE version
files — `pyproject.toml`, `src/vikunja_mcp/__init__.py` and `uv.lock`'s self-entry (the
lock is easy to forget and it is a *dependency-resolution* file, so "version-only" does not
mean "touches nothing that matters") — commits `chore: vX.Y.Z`, tags, and moves `stable`.
The bump commit is pushed with `GITHUB_TOKEN`, which by design does NOT re-trigger CI.

**The channel moves FORWARD ONLY** — a property of `scripts/release.sh`, not of ci.yml: the
channel push is un-forced, so git itself refuses to point `stable` at a commit that does
not contain the channel's current head, and the refusal is then GRADED. **The bump and its
tag are ONE server transaction** (`git push --atomic`), plus a separate check that the tag
really arrived: `--atomic` stops the half-state from EXISTING, the tag check stops it from
passing QUIETLY. **The release belongs to the TIP of `main`; a superseded landing skips,
green** — but only with positive proof that the supersession is benign, so a green
`release` job no longer implies a new tag exists; the log line `release skipped: …` is what
tells the two apart.

**Never let the literal ci-skip marker into a commit MESSAGE — quoting counts.** The marker
is matched anywhere in the message, body and code spans included, so a commit that merely
quotes the bump commit's subject cancels its own CI run, silently. It is a family, not one
spelling. The push succeeds, both evidence-sha checks pass, and the task looks landed, but
there is no run, no auto-release, and the change never reaches consumers. Name the marker
descriptively in messages, and after pushing confirm a run actually EXISTS for your sha
(`gh run list --commit "$(git rev-parse HEAD)"` — the FULL 40-char sha; an abbreviated one
returns `[]`, which reads exactly like "no run").

**"No run" has a SECOND cause, and reading it as the marker is a false diagnosis (tracker
#937): GitHub creates one run per PUSH, attached to that push's TIP**, so a commit that
arrives NON-TIP inside a multi-commit push gets no run at all — while the work lands and
reaches consumers. So ask first whether a DESCENDANT on `main` has a run (`git log
--oneline <full sha>..origin/main`, then `gh run list --commit <that full sha>`), and raise
the marker alarm only when nothing is above you or the descendant has no run either. What
that does NOT buy back is the gate: the tree AT your commit was never linted or tested.

**And build the message with `git commit -F - <<'MSG'`, never `-m "…"`** (tracker #773).
Inside double quotes a backtick is command substitution, and the house style wraps every
identifier in backticks — so the more faithfully an agent follows it, the likelier the
shell eats part of the message. The loss is not only omission: `$(…)` INSERTS foreign
output. The quoting of the heredoc delimiter is load-bearing, not cosmetic.

**A run that EXISTS is not a run that PASSED, and that gap silently cost seven landings in
one night** (tracker #614): 7 of 15 consecutive runs on `main` were red, every one of them
`lint-and-unit` success + `integration` failure + `release` **skipped**, so `stable` never
moved — while every agent had truthfully reported "a run exists". The two checks are two
because their DEADLINES differ: existence asks about a fact that does not ripen, the
outcome does. Measured over 40 runs, a run concludes 42–120 s after it appears, median
60 s; the bias helps but does not SEPARATE — red runs are 42–55 s (median 46) against
53–120 s for green (median 65), so the bands overlap. And the reason is NOT that
`integration` fails early: per-job timing says it is never the critical path (16–29 s
against `lint-and-unit`'s 38–46 s); a run's length is set by `lint-and-unit`, and a GREEN
run additionally runs `release` (8–15 s), which a red one skips. So the outcome is read ONCE and LAST — after `advance(to='review')` and `workspace
--release`, which cost about that long anyway — and never by waiting: `gh run view <id>
--json status,conclusion,jobs`, branching on `status` FIRST, because `conclusion` is
meaningful only at `status == "completed"` — an in-flight run renders it as the EMPTY
STRING, which is not `null`, so a jq `// "unknown"` fallback does not fire either. A
still-running run is therefore reported as UNKNOWN, never as green, and the card's
independent reviewer is the backstop.

**That bump commit is also a racer, and sizing the drain's retry loop is its job.** Because it lands 37 s–2 m 55 s after the task commit that triggered it (median 1 m 41 s), a per-task agent's freshly-completed rebase goes stale within about two minutes of *any* landing — so under a parallel drain a rejected `git push origin HEAD:main` is the expected outcome, not an anomaly. The `GITHUB_TOKEN`/ci-skip property above is what BOUNDS it: the release never triggers itself, so it can cost an agent at most one round. That bound sizes SKILL.md's integration ceiling, and the ceiling is a FORMULA, not a constant. Two steps: the worst purely MECHANICAL run at N racing agents is 2·(N−1) + 1 rounds — **5** at the default 3 — and the ceiling must sit STRICTLY ABOVE that, otherwise it fires on arithmetic. So the ceiling is **`2 × wip_limit`**: 2 at limit 1, **6** at this repo's default 3, 8 at 4, 10 at 5. **N is how many tasks are ACTUALLY in Design/Build — `wip.active`, not the limit** (tracker #939), since rework re-enters Build past the `claim` gate; the operative formula is `2 × max(wip_limit, wip.active)`, the `max` keeping the ceiling from DROPPING below the table when fewer tasks are in flight. And the count is only the budget: what decides whether a round was owed at all is asked in two steps, in this order. First *did it land anyway?* — a server can take the ref update and still leave the client reporting failure, so `git merge-base --is-ancestor HEAD origin/main` (after a fetch) comes first, and exit 0 means the work is already on `main`: verify the sha and move on, never wake anyone. Only exit 1 reaches the second question, *what won the race* (`git log --oneline HEAD..origin/main` — empty means it was never a race, so retrying is futile and the agent escalates without spending the budget). That order is load-bearing rather than tidy: a landed push with a sibling on top shows a NON-empty range, and the retry it invites rebases the already-upstream commit away.

Manual procedure remains for:
- **Rollback**: `git branch -f stable vX.Y.Z && git push -f origin stable`
  onto an older, known-good tag. `stable` moves ONLY to tagged, CI-green commits.
- **Minor / major bumps**: hand-edit `version`/`__version__` to `X.(Y+1).0`
  or `(X+1).0.0` in a commit; CI resumes auto-patching from the new baseline.

→ **Dossier: `docs/dossier/releases.md`** — the #716/#723/#737/#740/#769 race analysis, the
four constructed "swallows" and which two are closed, why a bare `--force-with-lease` was
measured and rejected, and the run-timing data behind the numbers above.

## Dogfood: this repo's own tasks

This project tracks itself in the same tracker (project `vikunja-mcp`, id 10 — see
`.vikunja-mcp.toml`). Follow the tracker flow for real work here: the orchestrator is a
thin pump — `next_task` → claim → dispatch ONE fresh per-task agent for the WHOLE task →
drain next. That agent owns the whole lifecycle (`get_task` → spec/`advance(to='build')` →
implement, possibly spawning its own sub-agents → commit+push → `advance(to='review')`);
the orchestrator does no task content itself. EVERY task reaching Review gets independent
agent review, not just bugs (the orchestrator dispatches a sibling reviewer; only an `epic`
container is exempt). Whenever the effective limit exceeds 1 — this repo's
`.vikunja-mcp.toml` says `wip_limit = 3`, and a project that says nothing gets the same 3
by default — the same pump keeps several per-task agents in flight, each in its OWN
worktree from `vikunja-mcp workspace <id>`, and passes `exclude=[ids it has a live agent
on]`. **Any `workspace` failure degrades to one slot in this checkout, never a stopped
loop.**

Run it under `/loop`. **Pick the mode by supervision**: self-paced (`/loop`, no interval) is
fine WHEN SUPERVISED, but for UNATTENDED / overnight runs use an INTERVAL-backed loop
(`/loop 10m`). A self-paced loop arms its next tick only via an end-of-turn
`ScheduleWakeup`, so a turn killed before that call arms nothing and the loop silently dies
forever; an interval loop stores its cadence as a persistent session cron the harness
daemon fires BETWEEN turns. Honest limit: neither mode survives the session PROCESS
exiting — that needs a human `claude --resume` (which restores session crons within 7 days)
or an external supervisor (the sibling project hgdev-acp), not anything vikunja-mcp can
ship; the SessionStart hook only FRAMES a running loop, it cannot restart a dead one. This loop deliberately OVERRIDES the generic autonomous-`/loop` default ("steward,
not initiator: don't start fresh work without a human go-ahead, stop when idle") — the
Queue is human-triaged work, so claiming a fresh Queue task and dispatching IS the mandate;
an empty queue means yield-to-next-tick, never a stop. **When the orchestrator needs a human
answer, it asks via `call_human` (a card) — never a console prompt**
(`AskUserQuestion`/`ExitPlanMode`/plain text), since the human isn't at the console; after
asking it keeps draining. Each task lands as its own commit on `main`, pushed at
`advance(to='review')` time (`… (tracker #N)`, `evidence` = the sha), and that green push
auto-releases a patch. The repo is PUBLIC — this repo's own token is supplied via the
repo-local `.vikunja-mcp.env` (gitignored), never committed.

**Committed `.claude/settings.json` sets `PLAYWRIGHT_MCP_ISOLATED=true`** (tracker #558) —
do not delete it as stray local state; `.gitignore` deliberately re-includes that one file.
`@playwright/mcp` derives its on-disk browser profile PER WORKSPACE ROOT, so two `claude`
sessions on the SAME repo (a human's plus the hgdev-acp repo-agent, the normal case here)
resolve to one profile and the second browser refuses to start at all. The env var is the
documented equivalent of `--isolated`. Cost: the profile lives in memory, so browser logins
do not persist between sessions.

**`PLAYWRIGHT_MCP_STORAGE_STATE` does NOT buy that cost back, and is deliberately set
NOWHERE here** (tracker #585). Upstream documents it as `--isolated`'s complement and it is
one — but only for LOADING. It is never WRITTEN: after a login and a clean shutdown the
file stayed byte-identical, so it converts "log in every session" into "hand-maintain a
seed file". Two further measurements make a committed value actively harmful: a path whose
file does not exist yet makes EVERY `browser_*` call fail, which is worse than the status
quo for anyone who clones; and the value is a machine-local path to LIVE SESSION COOKIES —
a secret of the same class as `.vikunja-mcp.env`.

**The `.gitignore` guard reduces that accident; it does not make it impossible.** A
name-based rule can only ever cover a LIST, and `browser_storage_state` takes ANY filename
anywhere under its root — so what guarantees nothing depends on the name is a unit test:
it asks git what `git add -A` would publish and fails on any file of storage-state SHAPE
under any name, tracked or untracked, at any SIZE, reading BOTH the index blob and the
worktree bytes. That is a GATE (red in the pre-push `pytest` run and in CI), not a lock on
`git commit` — every stronger option reduces to "works on whichever machine ran an
installer".

**Write browser artifacts only under `.playwright-mcp/<id>/`** (tracker #703, #736) — the
one directory `.gitignore` covers wholesale, independently of name and format. A `filename`
argument is resolved against the SERVER's cwd (the main checkout), so a bare name lands in
the repo ROOT; the directory must exist first (`mkdir -p`), because a caller-chosen
`filename` does not create it. The same value goes for your own browser's `--output-dir`:
`.playwright-mcp/<id>`, or somewhere outside the repo entirely. Extensions are the wrong
axis — the format comes from the `type` argument, not the name — so the gates read magic
bytes and whole-file grammars instead, and they are complete about NAMES, never about
formats.

→ **Dossier: `docs/dossier/browser.md`** — exactly what leaked and how it was measured, the
storage-state seed that IS constructible and why it still is not worth shipping, and where
each gate is deliberately blind.

## Live instance notes

- Tracker: `https://tracker.zz.hgdev.com` (public) / `tracker.vpn.hgdev.com`
  (overlay). Board reconcile of a human-owned project 403s on the view
  config — admin share or agent-owned projects only (details in
  hgdev-infra `docs/vikunja-mcp-usage.md`).
- Scoped tokens REQUIRE permission groups `other:user` and
  `projects:views_buckets` (401 on all tools otherwise); minting lives in
  hgdev-infra `roles/vikunja/files/vikunja-bootstrap.py`.
