# Cross-Project `file_task` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `file_task` file a new card into ANOTHER project's Backlog (`project_id` param) so an agent in project A can hand work to project B's queue — true agent-to-agent coordination, with the scoped API token as the only security boundary.

**Architecture:** The REST client (`api.py`) already targets arbitrary projects — every relevant method takes an explicit `project_id`. Only the Workflow-level board helpers (`_view`/`_bucket`/`_move` and their caches) are bound to `self.project_id`. We do NOT generalize them: a new, separate resolver `_target_backlog(project_id)` serves ONLY `file_task`'s foreign path (fresh `kanban_view`+`buckets` read, fail-fast BEFORE the card is created), while the own-project path stays byte-identical. FakeAPI gains multi-project boards first, so the feature is honestly unit-testable.

**Tech Stack:** Python 3.11+ / uv / pytest / ruff (line-length 100). No new dependencies.

## Global Constraints

- TDD: failing test first, then the minimal implementation (`superpowers:test-driven-development`).
- `uv run pytest tests/unit -q` and `uv run ruff check .` green after every task.
- FakeAPI stays 1:1 with the real client's behavior — never MORE generous than real 2.3.0 (the #125 lesson, per CLAUDE.md).
- Tool docstrings in `server.py` and SKILL.md are agent-facing UX copy: prescriptive (when to call), not just descriptive.
- The scoped API token is the security boundary — do NOT add local authorization; only surface Vikunja's refusal clearly.
- Repo is PUBLIC: no token literals anywhere (integration reads `VIKUNJA_TEST_URL` env only).
- Commit per task: `type(scope): … (tracker #N)` + trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Substitute the real tracker task id for `#N`.
- A green push to `main` auto-releases: CI patch-bumps, tags, and moves `stable` — the feature SHIPS on merge (CLAUDE.md “Releases”). No separate release task.

## Sizing

**S — 3 tasks.** ~60 lines in `workflow.py`/`server.py`, ~70 lines of FakeAPI extension, ~200 lines of tests + a SKILL.md paragraph. No API-client change at all (so no new MockTransport tests: `api.py` is untouched).

---

## Current behavior + Evidence

- `src/vikunja_mcp/workflow.py:136-144` — `Workflow.__init__(api, project_id, …)` binds ONE `project_id`; `_view_cache`/`_buckets_cache` are single-slot (not keyed by project).
- `workflow.py:152-155` — `_view()` caches `api.kanban_view(self.project_id)`.
- `workflow.py:157-166` — `_bucket(title)` caches `{title: bucket}` from `api.buckets(self.project_id, view_id)`; missing canonical STAGES → `WorkflowError("… run `vikunja-mcp setup`")`.
- `workflow.py:753-756` — **the crux**: `_move(task_id, stage)` = `api.move_task(self.project_id, self._view()["id"], self._bucket(stage)["id"], task_id)`. All three coordinates (project, view id, bucket id) belong to the OWN project. The real endpoint is `POST /projects/{p}/views/{v}/buckets/{b}/tasks` (`api.py:318-322`) — a task created in project B cannot be moved with A's view/bucket ids (the server 404s: the bucket isn't on that project's view). So `_move` cannot target another project as-is.
- `workflow.py:1074-1103` — `file_task`: validate title → `api.create_task(self.project_id, …)` → `_move(new_id, "Backlog")` (explicit: “не полагаемся на то, что default-бакет проекта == Backlog”, :1089) → optional `add_relation(new_id, related_task_id, "related")` → `[filed-by-agent]` marker comment → result dict.
- `src/vikunja_mcp/api.py` — the client is ALREADY cross-project-capable: `create_task(project_id, …)` :127-133 (`PUT /projects/{pid}/tasks`), `kanban_view(project_id)` :214-218 (raises `VikunjaError(404, "project has no kanban view — run `vikunja-mcp setup`")` when absent), `buckets(project_id, view_id)` :220-221, `move_task(project_id, view_id, bucket_id, task_id)` :318-322, `add_relation(task_id, other, kind)` :185-189 (task-scoped — Vikunja relations are task-id↔task-id, project-agnostic), `add_comment(task_id, text)` :168-175 (task-scoped, the `text_to_html` chokepoint). Codified gotchas (PUT=create/POST=full-replace :1, kanban manual-mode :324-338) are NOT touched — this feature does only GETs + create + move + relation + comment.
- `src/vikunja_mcp/server.py:385-399` — the `file_task` MCP tool + its agent-facing docstring; `server.py:176-214` — `_tool`'s 401 reload-and-retry (**load-bearing constraint**: workflow must NOT swallow 401s into `WorkflowError`, or the rotated-token self-heal dies for this path); `tests/unit/test_server.py:240-250` — the scope-gap test pins that `file_task`'s writes are not duplicated by the whole-tool retry.
- `src/vikunja_mcp/config.py:20-29, 59-103` — a single `project_id: int`; no parent/sibling/registry concept. A cross-project target is therefore an arbitrary id passed per call, not config-validated.
- `tests/unit/fakes.py` — **the FakeAPI gap, confirmed**: exactly ONE project (`self.project`/`self.view`/`self._buckets`, :29-33); `create_task` ignores `project_id` and drops the task into `self._buckets[0]` (:181-190); `kanban_view`/`buckets` ignore their args (:239-243); `move_task` ignores project AND view (:281-282). A cross-project unit test against today's fake is vacuous: workflow passing the WRONG project's coords would still “pass” — the exact #125 too-generous-fake failure mode the fake's own docstrings warn about (:116-126).
- `src/vikunja_mcp/skills/tracker/SKILL.md:235-241` — the `file_task` process bullet (`«файлинг находок»`) to extend; `tests/unit/test_skill_contract.py:75` greps `[filed-by-agent]` in both workflow source and SKILL.md — our changes keep that marker prefix, so the contract stays green.
- `tests/integration/conftest.py` — session fixtures `boss_jwt`, `agent_jwts` (two agent users), `mint_scoped_token(jwt)` with the production permission slice; `setup_cmd.reconcile(api, title, shares)` provisions a canonical board (used by `test_agent_flow.py:36-50`). Everything a cross-project integration test needs already exists.

## Decisions

All decisions below are **defaultable** (recommended defaults, no user-blocking fork). The two closest to taste are flagged.

1. **Target spec: `project_id: int | None = None`** (id, not name; `None` = own project). Named `project_id` — not the also-considered `project` — for consistency with `task_id`/`related_task_id`/`attachment_id` and because it self-documents id-not-name. **No name→id lookup**: config speaks ids, a title lookup needs a `projects()` listing plus ambiguity handling, and the target id arrives from task/human context (SKILL.md tells agents: don't guess — `call_human` if unknown). *(taste-flagged: param name)*
2. **Board-helper generalization: a dedicated foreign-path resolver, NOT parameterized helpers.** New `_target_backlog(project_id) -> (view_id, bucket_id)` used only by `file_task`'s cross path. `_view`/`_bucket`/`_move` and their single-slot caches stay untouched — the own-project fast path (and every hot-path caller: claim/advance/next_task/review_task/…) is byte-identical, zero blast radius. **No cache for foreign lookups**: filing cross-project is a rare coordination event; two extra GETs per call beat a new staleness/invalidation surface. Rejected: per-project-keyed caches threaded through `_view`/`_bucket`/`_move` (touches ~10 hot-path call sites for a rare event).
3. **Fail-fast: resolve the target board BEFORE `create_task` (cross path only).** A wrong id, a no-access token, a project with no kanban view, or a board without a `Backlog` column all refuse with NOTHING orphaned in the target's default bucket — mirroring the repo's resolve-before-write discipline. The own-project path keeps today's create→move order literally (back-compat, and `test_server.py:240` pins its write pattern). Bonus: under a scope-gap 401 the cross path fails on a read with zero writes — strictly safer than the own path.
4. **Error mapping: wrap 403/404 into `WorkflowError` naming the target; NEVER wrap 401.** The wrapped message carries the server's own text (`exc.message`) and owns all causes (no access / wrong id / no kanban board) without branching on status — the #140 lesson (don't over-diagnose an ambiguous status). 401 must propagate as `VikunjaError` so `server._tool`'s rotated-token reload-and-retry still fires (`server.py:202-211`).
5. **Landing + marker: always the TARGET's Backlog** (never Queue — a foreign Queue would inject un-triaged work past that project's human gate; Backlog-for-human-triage is the product's own rule). Cross marker text: `[filed-by-agent] заведено агентом из проекта id={source} для триажа человеком` (+ the existing ` (по ходу работы над #{related_task_id})` suffix). Source as **id only**: `Workflow` doesn't hold a project name (`config.project_name` never reaches it), an extra ctor param isn't worth it, and the `related` link (whose ref carries the source project's identifier prefix, e.g. `VMCP-27`) is the real navigation aid. Own-path marker stays byte-for-byte today's string. *(taste-flagged: marker wording)*
6. **Relation: keep the optional `related` link.** `related` is symmetric (`_INVERSE_RELATION`, `fakes.py:11-18`), task-id↔task-id, and works across projects on real Vikunja — the filed card links back to the task that spawned it. Confirmed, unchanged.
7. **Result shape: additive keys on the cross path only.** `filed.project_id = target` plus a note that warns honestly: the card lives on the TARGET board, so the filer's `get_task`/`comment` (bound to the own board via `_find_task`, `workflow.py:193-198`) won't see it. Own-path result unchanged.
8. **Guard `target <= 0`** (cross path only): Vikunja pseudo-projects have negative ids (`api.projects()` filters `id > 0`, `api.py:193`); refuse early with a clear message instead of a confusing 404.
9. **FakeAPI gap → its own task (Task 1).** Additive `add_project()` registry + per-project dispatch on `views`/`kanban_view`/`buckets`/`create_task`/`move_task`/`view_tasks`/`projects`/`stage_of`; `move_task` VALIDATES the bucket belongs to that project's view (the tripwire that makes the workflow tests non-vacuous); unknown id → 404, registered-but-`forbidden` → 403 (real 2.3.0 wording) to model the token boundary. The primary project's attributes and every existing test path are untouched (all existing tests use one consistent project id).
10. **Back-compat: byte-identical when `project_id` is `None` OR equals the own project id** — the `cross` flag is `target != self.project_id`, and the non-cross branch reproduces today's body exactly (same call order, same marker, same result keys).
11. **SKILL.md: yes, a short addition** to the existing `file_task` bullet — cross-project filing is a new agent-facing PROCESS rule (before this, “the fix lives in another repo” had only `return_task`/`call_human` as outs). `test_skill_contract.py` stays green (it greps markers, all preserved).

**Non-goals (deliberate):** no name→id resolution; no `list_projects` discovery tool (target ids come from task/human context — file a follow-up only if agents actually hit the wall); no cross-project `next_task`/`claim`/`comment`/`get_task`/`decompose`; no landing in a foreign Queue; no `api.py` changes.

## File Structure

- **Modify** `tests/unit/fakes.py` — multi-project registry + project-aware dispatch (Task 1).
- **Create** `tests/unit/test_fakes.py` — the fake's multi-project contract (the tripwires Task 2's tests lean on).
- **Modify** `src/vikunja_mcp/workflow.py` — `_target_backlog` helper + `file_task(project_id=…)` (Task 2).
- **Modify** `tests/unit/test_workflow_gates.py` — 5 new gate tests beside the existing `file_task` tests (Task 2).
- **Modify** `src/vikunja_mcp/server.py` — tool param + rewritten agent-facing docstring (Task 2).
- **Modify** `tests/unit/test_server.py` — passthrough test (Task 2).
- **Modify** `src/vikunja_mcp/skills/tracker/SKILL.md` — cross-project filing process rule (Task 2).
- **Create** `tests/integration/test_cross_project.py` — real-Vikunja happy path + no-access refusal (Task 3).

---

### Task 1: FakeAPI multi-project boards (test infrastructure)

**Files:**
- Modify: `tests/unit/fakes.py`
- Test: `tests/unit/test_fakes.py` (create)

**Interfaces:**
- Consumes: existing `FakeAPI` internals (`self._ids`, `self.project`, `self.view`, `self._buckets`, `self.task_bucket`).
- Produces (Task 2 relies on these): `FakeAPI.add_project(title, buckets=(), identifier="", forbidden=False) -> dict` (returns the project dict, `["id"]` is the target id); `kanban_view`/`views`/`buckets`/`create_task`/`move_task`/`view_tasks` dispatch per `project_id` (unknown → `VikunjaError(404, …)`, forbidden → `VikunjaError(403, …)`); `move_task` raises `VikunjaError(404)` for a bucket not on that project's view; `stage_of(task_id)` resolves across ALL projects' buckets.

- [ ] **Step 1: Write the failing contract tests**

Create `tests/unit/test_fakes.py`:

```python
"""Контракт мульти-проектного FakeAPI (кросс-проектный file_task). Фейк обязан РАЗЛИЧАТЬ
проекты: до этого каждый project-scoped метод игнорировал project_id, и workflow-баг,
двигающий задачу координатами ЧУЖОЙ доски, был невидим юнитам — ровно #125-режим
«фейк щедрее сервера». Эти тесты — растяжки, на которые опираются кросс-тесты workflow."""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import STAGES


def test_second_project_has_its_own_view_and_disjoint_buckets():
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    assert other["id"] != api.project["id"]
    other_view = api.kanban_view(other["id"])
    assert other_view["id"] != api.view["id"]
    own_ids = {b["id"] for b in api.buckets(api.project["id"], api.view["id"])}
    other_ids = {b["id"] for b in api.buckets(other["id"], other_view["id"])}
    assert own_ids.isdisjoint(other_ids)
    # primary state untouched — existing single-project tests see zero change
    assert api.kanban_view(api.project["id"])["id"] == api.view["id"]


def test_create_task_lands_in_the_target_projects_default_bucket():
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=["Inbox", *STAGES])
    t = api.create_task(other["id"], "filed elsewhere")
    other_view = api.kanban_view(other["id"])
    inbox = next(
        b for b in api.buckets(other["id"], other_view["id"]) if b["title"] == "Inbox"
    )
    assert api.task_bucket[t["id"]] == inbox["id"]   # ЦЕЛЕВОЙ дефолт-бакет, не свой
    assert api.stage_of(t["id"]) == "Inbox"          # stage_of видит чужие доски


def test_move_task_refuses_a_bucket_of_another_projects_view():
    # РАСТЯЖКА: workflow, передавший координаты СВОЕЙ доски для задачи в чужом
    # проекте, обязан здесь упасть — как реальный сервер (bucket не на том view -> 404).
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    t = api.create_task(other["id"], "x")
    own_backlog = api.bucket_id("Backlog")           # бакет ПЕРВИЧНОГО проекта
    with pytest.raises(VikunjaError) as err:
        api.move_task(other["id"], api.kanban_view(other["id"])["id"], own_backlog, t["id"])
    assert err.value.status == 404


def test_unknown_project_404s_and_forbidden_project_403s():
    api = FakeAPI(buckets=STAGES)
    secret = api.add_project("secret", buckets=STAGES, forbidden=True)
    with pytest.raises(VikunjaError) as e403:
        api.kanban_view(secret["id"])
    assert e403.value.status == 403                  # есть, но токену не расшарен
    with pytest.raises(VikunjaError) as e404:
        api.kanban_view(999999)
    assert e404.value.status == 404                  # не существует вовсе
    assert all(p["id"] != secret["id"] for p in api.projects())  # и в листинге его нет
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_fakes.py -q`
Expected: FAIL — `AttributeError: 'FakeAPI' object has no attribute 'add_project'`.

- [ ] **Step 3: Extend FakeAPI**

In `tests/unit/fakes.py`, add to `__init__` (after `self.shares = []`, keeping existing lines untouched):

```python
        # кросс-проектный file_task: реестр ВТОРИЧНЫХ проектов (см. add_project).
        # Первичный (self.project/self.view/self._buckets) не трогаем — все старые
        # тесты работают на нём и не видят изменений.
        self.other_projects = {}   # pid -> {"project", "view", "buckets"}
        self._forbidden = set()    # pid, «не расшаренные» токену -> 403 как у сервера
```

Add the test helper + dispatcher (near `add_bucket`):

```python
    def add_project(self, title, buckets=(), identifier="", forbidden=False):
        """Test helper (кросс-проектный file_task): зарегистрировать ВТОРОЙ проект со своим
        kanban-view и бакетами. forbidden=True моделирует проект, который СУЩЕСТВУЕТ, но не
        расшарен пользователю токена: любой project-scoped вызов 403-ит, как реальная 2.3.0
        («You don't have the right…») — так поверхностью становится сама граница токена.
        Никогда не регистрировавшийся id, напротив, 404-ит."""
        proj = {"id": next(self._ids), "title": title, "identifier": identifier}
        view = {"id": next(self._ids), "title": "Kanban", "view_kind": "kanban",
                "position": 400}
        entry = {"project": proj, "view": view, "buckets": []}
        self.other_projects[proj["id"]] = entry
        if forbidden:
            self._forbidden.add(proj["id"])
        for t in buckets:
            entry["buckets"].append({
                "id": next(self._ids), "title": t,
                "position": (len(entry["buckets"]) + 1) * 100,
            })
        return proj

    def _project_state(self, project_id):
        """Диспетчер project-scoped вызова на ПРАВИЛЬНУЮ доску — ужесточение, делающее
        кросс-проектный файлинг тестируемым: раньше project_id игнорировался, и баг,
        использующий view/bucket-иды чужой доски, юниты не ловили (#125-режим).
        Неизвестный id -> 404, зарегистрированный-но-forbidden -> 403 (формулировки 2.3.0)."""
        if project_id == self.project["id"]:
            return {"project": self.project, "view": self.view, "buckets": self._buckets}
        if project_id in self._forbidden:
            raise VikunjaError(403, "You don't have the right to see this project.")
        entry = self.other_projects.get(project_id)
        if entry is None:
            raise VikunjaError(404, "The project does not exist.")
        return entry
```

Rewire the project-scoped surface (replace the bodies; signatures unchanged):

```python
    def _task_identity(self, project=None):
        """Mirror Vikunja: every task read carries a per-project `index` and a computed
        `identifier` = '<project identifier>-<index>' (or '#<index>' when the project has
        no identifier prefix — verified against real 2.3.0). `project` picks whose prefix
        (default: the primary); the index counter stays GLOBAL — a documented shortcut
        (uniqueness is what tests rely on, never per-project density)."""
        idx = next(self._task_index)
        prefix = (project or self.project).get("identifier") or ""
        return idx, (f"{prefix}-{idx}" if prefix else f"#{idx}")

    def stage_of(self, task_id):
        bid = self.task_bucket[task_id]
        pools = [self._buckets, *(e["buckets"] for e in self.other_projects.values())]
        return next(b["title"] for pool in pools for b in pool if b["id"] == bid)

    def create_task(self, project_id, title, description="", priority=0):
        state = self._project_state(project_id)
        idx, identifier = self._task_identity(state["project"])
        t = {
            "id": next(self._ids), "title": title, "description": description,
            "priority": priority, "index": idx, "identifier": identifier,
            "done": False, "assignees": [], "labels": [],
        }
        self.tasks[t["id"]] = t
        self.task_bucket[t["id"]] = state["buckets"][0]["id"]  # default = первый бакет ЦЕЛИ
        return dict(t)

    def projects(self):
        return [dict(self.project)] + [
            dict(e["project"]) for pid, e in self.other_projects.items()
            if pid not in self._forbidden
        ]

    def views(self, project_id):
        return [dict(self._project_state(project_id)["view"])]

    def kanban_view(self, project_id):
        return dict(self._project_state(project_id)["view"])

    def buckets(self, project_id, view_id):
        found = self._project_state(project_id)["buckets"]
        return [dict(b) for b in sorted(found, key=lambda x: x["position"])]

    def move_task(self, project_id, view_id, bucket_id, task_id):
        # ужесточено: реальный эндпоинт POST /projects/{p}/views/{v}/buckets/{b}/tasks
        # 404-ит на бакете, не принадлежащем view ЭТОГО проекта; старый фейк игнорировал
        # project_id целиком, и такой баг проходил молча (#125-режим).
        state = self._project_state(project_id)
        if bucket_id not in {b["id"] for b in state["buckets"]}:
            raise VikunjaError(404, "bucket does not exist on this project's view")
        self.task_bucket[task_id] = bucket_id
```

And in `view_tasks`, change ONLY the iteration source (rest of the body untouched):

```python
        for b in self._project_state(project_id)["buckets"]:
```

(replacing `for b in self._buckets:`).

- [ ] **Step 4: Run the new tests, then the FULL unit suite**

Run: `uv run pytest tests/unit/test_fakes.py -q` → expected: 4 passed.
Run: `uv run pytest tests/unit -q` → expected: ALL passed (every existing test uses one consistent project id — the dispatch is a no-op for them; this run is the proof).
Run: `uv run ruff check .` → expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/fakes.py tests/unit/test_fakes.py
git commit -m "test(fakes): multi-project boards in FakeAPI for cross-project filing (tracker #N)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `file_task(project_id=…)` — workflow gate, server tool, SKILL.md

**Files:**
- Modify: `src/vikunja_mcp/workflow.py:1074-1103` (file_task) + new helper after `_move` (`workflow.py:756`)
- Modify: `src/vikunja_mcp/server.py:383-399` (tool)
- Modify: `src/vikunja_mcp/skills/tracker/SKILL.md:235-241` (file_task bullet)
- Test: `tests/unit/test_workflow_gates.py` (after line 208), `tests/unit/test_server.py`

**Interfaces:**
- Consumes: Task 1's `FakeAPI.add_project(...)`; existing `api.kanban_view/buckets/create_task/move_task/add_relation/add_comment`.
- Produces: `Workflow.file_task(title, description="", priority=0, related_task_id=None, project_id: int | None = None)`; `Workflow._target_backlog(project_id) -> tuple[int, int]`; MCP tool `file_task(title, description="", priority=0, related_task_id=None, project_id=None)`. Cross result adds `filed.project_id`.

- [ ] **Step 1: Write the failing workflow tests**

Append to `tests/unit/test_workflow_gates.py` (after `test_file_task_without_relation_has_no_link`, line 208):

```python
def test_file_task_cross_project_lands_in_targets_backlog(env):
    api, wf, t = env
    # Backlog у цели НЕ первый бакет: дефолт-бакет = Inbox, так что пропущенный move
    # оставил бы карточку в Inbox и тест бы упал (create-в-нужном-проекте недостаточно).
    other = api.add_project("neighbor", buckets=["Inbox", *STAGES])
    res = wf.file_task(
        title="repo B: нужен эндпоинт для A",
        description="координация агент→агент",
        priority=1,
        related_task_id=t["id"],
        project_id=other["id"],
    )
    new_id = res["filed"]["id"]
    other_view = api.kanban_view(other["id"])
    other_backlog = next(
        b for b in api.buckets(other["id"], other_view["id"]) if b["title"] == "Backlog"
    )
    assert api.task_bucket[new_id] == other_backlog["id"]  # Backlog ЦЕЛИ, не свой
    assert res["filed"]["project_id"] == other["id"]
    assert res["filed"]["stage"] == "Backlog"
    assert (new_id, t["id"], "related") in api.relations   # связь через границу проектов
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert f"из проекта id={wf.project_id}" in marker      # provenance для людей цели
    assert f"#{t['id']}" in marker


def test_file_task_cross_project_no_access_fails_fast_nothing_created(env):
    api, wf, _t = env
    secret = api.add_project("secret", buckets=STAGES, forbidden=True)
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="can't file into project"):
        wf.file_task(title="x", project_id=secret["id"])
    assert len(api.tasks) == before        # fail-fast: доска резолвится ДО create_task


def test_file_task_cross_project_unknown_or_pseudo_project_refused(env):
    api, wf, _t = env
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="can't file into project 999999"):
        wf.file_task(title="x", project_id=999999)
    with pytest.raises(WorkflowError, match="positive"):
        wf.file_task(title="x", project_id=-1)  # псевдо-проекты Vikunja (favorites = -1)
    assert len(api.tasks) == before


def test_file_task_cross_project_target_without_backlog_refused(env):
    api, wf, _t = env
    virgin = api.add_project("virgin", buckets=["To-Do", "Doing", "Done"])  # без setup
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="Backlog"):
        wf.file_task(title="x", project_id=virgin["id"])
    assert len(api.tasks) == before


def test_file_task_explicit_own_project_id_is_todays_behavior(env):
    api, wf, t = env
    res = wf.file_task(title="own finding", related_task_id=t["id"], project_id=wf.project_id)
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Backlog"
    assert "project_id" not in res["filed"]    # без кросс-добавок в результате
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert marker == (
        f"[filed-by-agent] заведено агентом для триажа человеком "
        f"(по ходу работы над #{t['id']})"
    )
```

And append to `tests/unit/test_server.py` (after `test_scope_gap_401_does_not_duplicate_the_filed_card`, ~line 250 — reuses that file's imports):

```python
def test_file_task_tool_passes_project_id_through(monkeypatch):
    """The MCP tool must thread project_id into the workflow — a param added in workflow.py
    but forgotten in server.py would silently never be exposed to agents."""
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    monkeypatch.setattr(server, "_wf", lambda: Workflow(api, api.project["id"]))
    result = server.file_task("cross-filed", project_id=other["id"])
    assert result["filed"]["project_id"] == other["id"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_workflow_gates.py tests/unit/test_server.py -q`
Expected: the 6 new tests FAIL with `TypeError: … unexpected keyword argument 'project_id'`; everything else passes.

- [ ] **Step 3: Implement `_target_backlog` + the `file_task` change**

In `src/vikunja_mcp/workflow.py`, add after `_move` (line 756):

```python
    def _target_backlog(self, project_id: int) -> tuple[int, int]:
        """(view_id, bucket_id) колонки Backlog на ЧУЖОЙ доске — кросс-проектная половина
        file_task. Сознательно ОТДЕЛЬНА от _view/_bucket/_move: те (и их кэши) привязаны к
        self.project_id и питают каждый горячий гейт, а кросс-файлинг — редкое событие
        координации, поэтому здесь свежий kanban_view+buckets на каждый вызов (без кэша ->
        без новой поверхности устаревания). Резолв происходит ДО создания карточки
        (fail-fast): кривой id, недоступный токену проект или не-трекерная доска отказывают,
        НИЧЕГО не осиротив в дефолт-бакете цели. 403/404 заворачиваются в actionable
        WorkflowError с именем цели — граница безопасности ЗДЕСЬ сам скоуп-токен (решает
        Vikunja, мы только внятно показываем отказ). 401 НЕ заворачиваем намеренно: он
        должен дойти до server._tool как VikunjaError, чтобы сработал reload-and-retry
        ротации токена (#140)."""
        try:
            view = self.api.kanban_view(project_id)
            found = self.api.buckets(project_id, view["id"])
        except VikunjaError as exc:
            if exc.status in (403, 404):
                raise WorkflowError(
                    f"can't file into project {project_id}: Vikunja said {exc.status} "
                    f"({exc.message}). Either the token's user has no access to that "
                    f"project (the scoped API token is the security boundary — a human "
                    f"must share the target project with this agent), the project id is "
                    f"wrong, or the project has no kanban board. Nothing was created."
                ) from exc
            raise
        backlog = next((b for b in found if b["title"] == "Backlog"), None)
        if backlog is None:
            raise WorkflowError(
                f"can't file into project {project_id}: its board has no 'Backlog' "
                f"column — not a tracker-managed board (run `vikunja-mcp setup` for it "
                f"first). Nothing was created."
            )
        return view["id"], backlog["id"]
```

Replace `file_task` (workflow.py:1074-1103) with:

```python
    def file_task(
        self, title: str, description: str = "", priority: int = 0,
        related_task_id: int | None = None, project_id: int | None = None,
    ) -> dict:
        """File a finding (a bug/tech-debt OUTSIDE the current task) into Backlog for
        human triage — NOT into Queue (a human prioritizes). Optionally: a 'related'
        relation to the task it was found during. No ownership required — this is a new
        card, not an edit of your task (unlike decompose). project_id (agent-to-agent
        coordination): file into ANOTHER project's Backlog; the target board is resolved
        BEFORE the card is created (fail-fast — no orphan in its default bucket), the
        token's access to the target is Vikunja's call (403 -> clear refusal), and the
        marker names the SOURCE project so the target's humans see provenance. None (or
        the own project id) keeps today's behavior bit-for-bit."""
        if not (title or "").strip():
            raise WorkflowError("a non-empty title is required for the new task")
        target = self.project_id if project_id is None else int(project_id)
        cross = target != self.project_id
        if cross and target <= 0:
            raise WorkflowError(
                f"project_id must be a positive Vikunja project id, got {target} "
                f"(negative ids are Vikunja pseudo-projects like favorites)"
            )
        # кросс: резолвим доску ЦЕЛИ до create_task (fail-fast, см. _target_backlog);
        # свой проект: порядок сегодняшний (create -> _move), байт-в-байт.
        coords = self._target_backlog(target) if cross else None
        created = self.api.create_task(
            target, title.strip(),
            description=(description or "").strip(), priority=int(priority or 0),
        )
        new_id = created["id"]
        # явно в Backlog: не полагаемся на то, что default-бакет проекта == Backlog
        if cross:
            view_id, bucket_id = coords
            self.api.move_task(target, view_id, bucket_id, new_id)
        else:
            self._move(new_id, "Backlog")
        if related_task_id is not None:
            self.api.add_relation(new_id, related_task_id, "related")
        if cross:
            # provenance: люди ЦЕЛЕВОГО проекта должны видеть, откуда пришла карточка
            marker = (
                f"[filed-by-agent] заведено агентом из проекта id={self.project_id} "
                f"для триажа человеком"
            )
        else:
            marker = "[filed-by-agent] заведено агентом для триажа человеком"
        if related_task_id is not None:
            marker += f" (по ходу работы над #{related_task_id})"
        self.api.add_comment(new_id, marker)
        result = {
            "filed": {"id": new_id, "title": created["title"], "stage": "Backlog"},
            "note": "in Backlog for human triage (not Queue — a human prioritizes)",
        }
        if cross:
            result["filed"]["project_id"] = target
            result["note"] = (
                f"filed into project {target}'s Backlog for THAT project's human to "
                f"triage (not Queue — a human prioritizes). The card lives on the TARGET "
                f"board: your other tools (get_task/comment/next_task) are bound to your "
                f"own project and won't see it — the 'related' link is the cross-reference"
            )
        if related_task_id is not None:
            result["related_to"] = related_task_id
        return result
```

- [ ] **Step 4: Thread the param through the MCP tool with the new agent-facing docstring**

Replace the `file_task` tool in `src/vikunja_mcp/server.py:383-399` with:

```python
@mcp.tool()
@_tool
def file_task(
    title: str, description: str = "", priority: int = 0,
    related_task_id: int | None = None, project_id: int | None = None,
) -> dict:
    """File a task DISCOVERED mid-work (a bug/tech-debt OUTSIDE your current task) into
    Backlog for human triage. WHEN: you hit a problem unrelated to the current task with
    nowhere to put it — park it here, do NOT fix it silently and do NOT drag it into your
    diff. This is NOT splitting your own large task — use decompose for that (it puts
    subtasks in Queue with a parenttask). Files into Backlog (NOT Queue — a human
    prioritizes), marks it with a [filed-by-agent] comment and, if related_task_id is
    given, adds a 'related' relation to the task it was found during. No ownership needed
    — this is a new card.
    CROSS-PROJECT (agent-to-agent coordination): pass project_id — a numeric Vikunja
    project id — to file into ANOTHER project's Backlog, e.g. when your work needs a
    change owned by that project's repo/agent. Take the id from the task/human context;
    if you don't know it, ask via call_human — do NOT guess. Access is the API token's
    call: no access to the target means a clear refusal with NOTHING created. The card
    lands in the TARGET's Backlog for THAT project's human to triage; the marker names
    your project, and related_task_id still links it back to your current task across the
    project boundary. The filed card lives on the target board — your get_task/comment
    won't see it afterwards. Omit project_id (default) to file into your own project."""
    return _wf().file_task(
        title, description=description, priority=priority,
        related_task_id=related_task_id, project_id=project_id,
    )
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/unit/test_workflow_gates.py tests/unit/test_server.py tests/unit/test_fakes.py -q`
Expected: all pass (incl. the 6 new).

- [ ] **Step 6: Add the SKILL.md process rule**

In `src/vikunja_mcp/skills/tracker/SKILL.md`, extend the `file_task` bullet (after «…паркует стороннюю по смыслу находку в Backlog на триаж человеку.», line 241) with:

```markdown
- **Находка живёт в ЧУЖОМ проекте/репо — файли СРАЗУ в их очередь.** Если правка нужна
  на стороне другого проекта (его репо, его агент), передай
  `file_task(..., project_id=<id целевого проекта>)`: карточка ляжет в Backlog ЦЕЛЕВОГО
  проекта (триажит их человек), маркер `[filed-by-agent]` назовёт твой проект, а
  `related_task_id` свяжет её с твоей текущей задачей через границу проектов — это канал
  координации агент→агент. Не чини чужой репо в своём диффе и не паркуй чужую работу в
  СВОЙ Backlog. id целевого проекта бери из контекста задачи/от человека; не знаешь —
  `call_human`, не угадывай. Нет доступа у токена — получишь внятный отказ (граница —
  сам скоуп-токен), карточка не создастся. Заведённую карточку твои `get_task`/`comment`
  не увидят (она на чужой доске) — след у тебя остаётся через `related`-связь.
```

- [ ] **Step 7: Full verification**

Run: `uv run pytest tests/unit -q` → expected: ALL pass (incl. `test_skill_contract.py` — the `[filed-by-agent]` marker grep still matches both sources).
Run: `uv run ruff check .` → expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/vikunja_mcp/workflow.py src/vikunja_mcp/server.py src/vikunja_mcp/skills/tracker/SKILL.md tests/unit/test_workflow_gates.py tests/unit/test_server.py
git commit -m "feat(workflow): file_task can file into another project's Backlog (tracker #N)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Integration test on real Vikunja (what the fake can't prove)

**Files:**
- Create: `tests/integration/test_cross_project.py`

**Interfaces:**
- Consumes: `tests/integration/conftest.py` fixtures `boss_jwt`, `agent_jwts`, helper `mint_scoped_token`; `setup_cmd.reconcile(api, title, shares)`; Task 2's `file_task(project_id=…)`.
- Produces: nothing downstream — this is the real-server proof of the permission boundary (403 shape for an unshared project) and the cross-project relation shape.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_cross_project.py`:

```python
"""Кросс-проектный file_task против реальной Vikunja 2.3.0 — то, чего фейк не докажет:
реальная форма отказа на нерасшаренном проекте (объектная 403 у скоуп-токена) и что
'related'-связь реально живёт через границу проектов."""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import Workflow, WorkflowError

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def cross(boss_jwt, agent_jwts):
    boss = VikunjaAPI(BASE, boss_jwt)
    suffix = uuid.uuid4().hex[:8]
    pid_home = reconcile(boss, f"xhome-{suffix}", shares=[("agent1", 1)])
    pid_target = reconcile(boss, f"xtarget-{suffix}", shares=[("agent1", 1)])
    pid_private = reconcile(boss, f"xprivate-{suffix}", shares=[])  # agent1 БЕЗ доступа
    jwt1, _ = agent_jwts
    wf = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid_home)
    return boss, wf, pid_home, pid_target, pid_private


def test_file_task_lands_in_target_projects_backlog_with_relation(cross):
    boss, wf, pid_home, pid_target, _ = cross
    src = boss.create_task(pid_home, "работа в A, требующая правки в B")
    res = wf.file_task(
        title="сделать эндпоинт в B для A",
        description="агент A просит агента B",
        related_task_id=src["id"],
        project_id=pid_target,
    )
    new_id = res["filed"]["id"]
    assert res["filed"]["project_id"] == pid_target
    # карточка реально в Backlog ЦЕЛЕВОГО борда (координаты чужого view/bucket сработали)
    view = boss.kanban_view(pid_target)
    board = boss.view_tasks(pid_target, view["id"])
    backlog = next(b for b in board if b["title"] == "Backlog")
    assert any(t["id"] == new_id for t in backlog.get("tasks") or [])
    # 'related' видна с ИСХОДНОЙ стороны границы проектов
    related = boss.get_task(src["id"]).get("related_tasks") or {}
    assert any(rt["id"] == new_id for rt in related.get("related") or [])


def test_file_task_into_unshared_project_refused_nothing_created(cross):
    boss, wf, _home, _target, pid_private = cross
    title = f"never-lands-{uuid.uuid4().hex[:6]}"
    with pytest.raises(WorkflowError, match="can't file into project"):
        wf.file_task(title=title, project_id=pid_private)
    # fail-fast: в закрытом проекте не осиротело НИЧЕГО (проверяет boss — владелец)
    view = boss.kanban_view(pid_private)
    board = boss.view_tasks(pid_private, view["id"])
    assert not any(
        t["title"] == title for b in board for t in (b.get("tasks") or [])
    )
```

- [ ] **Step 2: Run it against a real container**

```bash
docker run -d --name vikunja-test -p 3456:3456 \
  -e VIKUNJA_DATABASE_TYPE=sqlite -e VIKUNJA_DATABASE_PATH=/tmp/vikunja.db \
  -e VIKUNJA_FILES_BASEPATH=/tmp/files -e VIKUNJA_SERVICE_JWTSECRET=integration-test-secret \
  -e VIKUNJA_SERVICE_PUBLICURL=http://localhost:3456/ -e VIKUNJA_SERVICE_ENABLEREGISTRATION=true \
  vikunja/vikunja:2.3.0
until curl -sf http://localhost:3456/api/v1/info >/dev/null; do sleep 1; done
VIKUNJA_TEST_URL=http://localhost:3456 uv run pytest tests/integration -q
docker rm -f vikunja-test
```

Expected: all integration tests pass (the whole suite, not just the new file — the shared fixtures/rate-limit retry are exercised together). If the unshared-project refusal surfaces a status OTHER than 403/404, that is a real finding — adjust `_target_backlog`'s wrapped-status set to match the observed reality and note it in the commit body (never widen to 401 — see decision 4).

- [ ] **Step 3: Full local verification + commit**

Run: `uv run pytest tests/unit -q && uv run ruff check .` → expected: green.

```bash
git add tests/integration/test_cross_project.py
git commit -m "test(integration): cross-project file_task on real Vikunja (tracker #N)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Then push. Reminder: the green push to `main` auto-releases (patch bump + tag + `stable` move) — the feature ships immediately; `evidence` for the tracker card = the feature commit sha, and CI must show a run (a stray `[skip ci]` in a message would silently skip the release).

---

## Self-review

1. **Spec coverage:** target-as-id param ✓ (Task 2); board-helper generalization without touching the fast path ✓ (`_target_backlog`, decision 2); landing in target Backlog + provenance marker ✓ (Task 2 step 3 + test 1); `related` across projects ✓ (unit + integration); 403 → clear `WorkflowError` naming the target, fail-fast before create ✓ (tests 2-4 + integration test 2); FakeAPI gap closed as its own task ✓ (Task 1); back-compat byte-identical ✓ (test 5 pins the exact marker string and result keys; the non-cross branch is today's body verbatim); docstring + SKILL.md updated as agent UX ✓; token never reimplemented locally ✓; auto-release awareness ✓ (Task 3 step 3).
2. **Placeholder scan:** no TBDs; every code step carries the full transcribable code; error messages written out verbatim.
3. **Type consistency:** `add_project(...) -> dict` with `["id"]` used consistently in Tasks 1-2; `_target_backlog(project_id) -> tuple[int, int]` unpacked as `(view_id, bucket_id)`; `file_task`'s `project_id: int | None = None` matches the server tool signature and both test call sites; `VikunjaError(status, message)` fields (`.status`, `.message`) match `api.py:10-14`.
4. **Known accepted residuals:** (a) foreign-board coords are not cached — two extra GETs per cross-file, by design; (b) the marker names the source by id, not title (Workflow holds no name — the `related` ref carries the source's identifier prefix); (c) `_move`'s own-project create→move ordering is intentionally unchanged (an own board missing Backlog can still orphan a card, exactly as today — out of this slice); (d) FakeAPI's task `index` counter stays global across projects (documented shortcut, uniqueness-only).
