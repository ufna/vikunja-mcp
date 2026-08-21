"""In-memory дублёр VikunjaAPI для unit-тестов workflow/setup."""
import copy
import itertools

from vikunja_mcp.api import VikunjaError
from vikunja_mcp.formatting import html_to_text, text_to_html

# Real Vikunja 2.3.0 auto-creates the reciprocal relation on the OTHER task: write one side
# ("P precedes S") and the inverse surfaces on the far end ("S follows P"). This map (verified
# against real 2.3.0) is applied on READ in get_task so self.relations stays the literal written
# set. See epic #94 / #104.
_INVERSE_RELATION = {
    "subtask": "parenttask", "parenttask": "subtask",
    "related": "related",
    "duplicateof": "duplicates", "duplicates": "duplicateof",
    "blocking": "blocked", "blocked": "blocking",
    "precedes": "follows", "follows": "precedes",
    "copiedfrom": "copiedto", "copiedto": "copiedfrom",
}


class FakeAPI:
    def __init__(self, me_id=2, me_username="agent-infra", buckets=None):
        self._ids = itertools.count(100)
        self._task_index = itertools.count(1)   # per-project running index (Vikunja `index`)
        self.me_user = {"id": me_id, "username": me_username}
        self.users = {me_id: self.me_user}
        # projects carry an `identifier` prefix (like the real "VMCP"); tasks then read
        # back a computed `identifier` = "<prefix>-<index>" (see _task_identity)
        self.project = {"id": 3, "title": "hgdev-infra", "identifier": "HGI"}
        self.view = {"id": 11, "title": "Kanban", "view_kind": "kanban", "position": 400}
        self._buckets = []
        for title in buckets or []:
            self.add_bucket(title)
        self.tasks = {}          # id -> task dict (assignees/labels: списки dict'ов)
        self.task_bucket = {}    # task_id -> bucket_id
        self._attachments = {}   # task_id -> [{"id", "task_id", "file": {...}}]
        self._attachment_bytes = {}  # (task_id, attachment_id) -> bytes
        self._comments = {}      # task_id -> [{"comment", "author"}]
        self._labels = []
        self.relations = []      # (task_id, other_id, kind)
        # id -> task dict for cards removed by vanish(): absent from get_task (404) but
        # still embeddable in another card's related_tasks. See vanish().
        self._vanished = {}
        self.view_config = None  # последний configure_kanban
        self.shares = []         # (project_id, username, permission)
        # кросс-проектный file_task: реестр ВТОРИЧНЫХ проектов (см. add_project).
        # Первичный (self.project/self.view/self._buckets) не трогаем — все старые
        # тесты работают на нём и не видят изменений.
        self.other_projects = {}   # pid -> {"project", "view", "buckets"}
        self._forbidden = set()    # pid, «не расшаренные» токену -> 403 как у сервера
        self.last_require_titles = None  # require_titles последнего view_tasks (#43, для тестов)
        self.view_tasks_calls = 0  # #126: сколько раз звали view_tasks (1 без escalation, 2 с ним)
        # #126: как max_items_per_page реального сервера — не-required бакеты усекаются до первой
        # страницы на лёгком борде (#43); дефолт 50 не трогает существующие тесты (<50 задач/бакет)
        self.page_size = 50
        # #885: id задач, чья копия В KANBAN-ВИДЕ приезжает с ПУСТЫМ assignees, тогда как
        # GET /tasks/<id> отдаёт их. Это НЕ удобство теста, а моделирование ИЗМЕРЕННОГО поведения
        # живой Vikunja 2.3.0 (project 10, 2026-08-06: /tasks/854 -> [(7, 'agent-vikunja-mcp')],
        # копия на доске -> []). Без этого knob'а расхождение здесь НЕПРЕДСТАВИМО — обе копии
        # фейка выводятся из одного стора, — то есть весь класс дефектов «код прочитал копию с
        # доски и решил по ней» непинуем, ровно как aliasing из _snapshot ниже.
        self.kanban_assignee_blackout = set()

    # --- helpers для тестов ---
    def _task_identity(self, project=None):
        """Mirror Vikunja: every task read carries a per-project `index` and a computed
        `identifier` = '<project identifier>-<index>' (or '#<index>' when the project has
        no identifier prefix — verified against real 2.3.0). `project` picks whose prefix
        (default: the primary); the index counter stays GLOBAL — a documented shortcut
        (uniqueness is what tests rely on, never per-project density)."""
        idx = next(self._task_index)
        prefix = (project or self.project).get("identifier") or ""
        return idx, (f"{prefix}-{idx}" if prefix else f"#{idx}")

    def add_bucket(self, title):
        b = {"id": next(self._ids), "title": title, "position": (len(self._buckets) + 1) * 100}
        self._buckets.append(b)
        return b

    def bucket_id(self, title):
        return next(b["id"] for b in self._buckets if b["title"] == title)

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

    def add_task(self, title, bucket_title, priority=0, assignee=None, labels=()):
        idx, identifier = self._task_identity()
        t = {
            "id": next(self._ids), "title": title, "description": "", "priority": priority,
            "index": idx, "identifier": identifier,
            # 1:1: every real task read carries the project it belongs to. Without it the
            # fake made "which board is this predecessor on?" unanswerable, so a gate that
            # asks was untestable and read as working (#1179).
            "project_id": self.project["id"],
            "done": False, "assignees": [assignee] if assignee else [],
            "labels": [{"id": next(self._ids), "title": lb} for lb in labels],
        }
        self.tasks[t["id"]] = t
        self.task_bucket[t["id"]] = self.bucket_id(bucket_title)
        return t

    def add_attachment(self, task_id, name, mime, data=b"", size=None, file_id=None):
        """Test helper: attach a file to a task, mirroring real 2.3.0's shape — each entry is
        {id, task_id, file:{id, name, mime, size}} and the download endpoint keys off the
        OUTER id (attachment id), not file.id. `size` overrides the metadata size (defaults to
        len(data)) so a test can exercise the too-large guard without a giant buffer. `file_id`
        defaults to the attachment id but can be set DISTINCT from it — the real server keeps two
        independent id sequences (the `files` table advances on ANY upload incl. avatars/project
        backgrounds, while `task_attachments` advances only per task attachment), so they desync;
        a test passing file_id proves workflow keys off the attachment id, not file.id (#146). The
        stored bytes stay keyed off the OUTER attachment id — 1:1 with the real endpoint."""
        aid = next(self._ids)
        att = {
            "id": aid, "task_id": task_id,
            "file": {
                "id": aid if file_id is None else file_id, "name": name, "mime": mime,
                "size": len(data) if size is None else size,
            },
        }
        self._attachments.setdefault(task_id, []).append(att)
        self._attachment_bytes[(task_id, aid)] = data
        return att

    def stage_of(self, task_id):
        bid = self.task_bucket[task_id]
        pools = [self._buckets, *(e["buckets"] for e in self.other_projects.values())]
        return next(b["title"] for pool in pools for b in pool if b["id"] == bid)

    def comments_text(self, task_id):
        # comments are STORED as HTML (mirrors the real client, #85); this helper renders
        # them back to the plain text a human/agent reads, so marker/content assertions
        # stay meaningful. Use `comments(task_id)` for the raw stored HTML.
        return [html_to_text(c["comment"]) for c in self._comments.get(task_id, [])]

    # --- поверхность VikunjaAPI ---
    def me(self):
        return self.me_user

    @staticmethod
    def _snapshot(task):
        """A read returns a SNAPSHOT — it must share NO mutable object with the store or with any
        earlier read. `dict(t)` is not that: it copies the top level and leaves `labels` and
        `assignees` ALIASED, so every snapshot this fake ever handed out was the same list, and
        a later `add_label` was retro-visible on a board copy read before it.

        1:1 with the real client is the reason, and it is structural rather than a preference:
        `VikunjaAPI.get_task` is `self._req("GET", …)`, freshly parsed JSON per request, which
        CANNOT share an object with a previous response. So the fake's aliasing was not "close
        enough" — it made a state the real client produces routinely (snapshot A older than
        snapshot B) UNREPRESENTABLE, and with it a whole class of defects unpinnable: any bug of
        the form "this code read a stale snapshot" passed the suite green, and no negative pin
        could be written for it. `Workflow.claim` hands `_clear_verdict_labels` the FRESH read and
        not the board copy for exactly that reason; before this, swapping the two left the entire
        suite green (measured on #693, filed as #786). What the deepening BUYS is pinned by
        test_claim_clears_a_verdict_that_appeared_AFTER_the_board_read, which is green under that
        swap until this method stops aliasing.

        Deep rather than one level down: the containers are lists OF DICTS, so copying only the
        list still shares every label/assignee dict, and `{**lb}` on read is what the real client
        gives you for free. Cost measured on the full unit suite — see the card's worklog."""
        return copy.deepcopy(task)

    @staticmethod
    def _related_subdict(task):
        """Mirror real Vikunja 2.3.0: a task embedded inside another task's `related_tasks` is
        HOLLOWED — `labels`, `assignees` and nested `related_tasks` come back as None even when the
        task genuinely carries them; only scalars (id, title, done, identifier, index, description,
        priority, ...) survive. A caller that needs a related task's labels/assignees/relations MUST
        re-fetch it with get_task(id). Verified against a real container in the #118 Part 2 rework:
        the epic marker read a related sub-dict's `labels`, which the too-generous fake returned
        FULLY populated, so the fake agreed with the fake — 12 unit tests were green while the
        feature was dead in production (the exact #125 failure mode). Keep this hollow to stay 1:1
        with the server (a CLAUDE.md invariant); being MORE generous than reality is worse than
        being less capable."""
        return {**task, "labels": None, "assignees": None, "related_tasks": None}

    def vanish(self, task_id):
        """Model the RACE that makes `get_task`'s 404 branch reachable at all: the task is
        gone from GET /tasks/{id} while a relation read taken a moment earlier still embeds
        it. Deleting a card normally takes its relation rows with it, so a predecessor that
        is BOTH embedded and absent exists only inside that window — and without this knob
        the branch is unpinnable, which is how a "gone -> not a blocker" ruling would ship
        untested."""
        self._vanished[task_id] = self.tasks.pop(task_id)

    def drop_kanban_view(self, project_id):
        """Make a NEIGHBOUR's board unreadable while its project and its TASKS stay readable.

        The route is not invented: measured on a live 2.3.0 (#1198, control in the same round)
        by `DELETE /projects/<pid>/views/<kanban>` -> 200, no permission changed. After it the
        relation is still visible on the successor, the far TASK still reads, and only the board
        read fails — which is exactly the state `_offboard_predecessor`'s unreadable-board branch
        is written against, and the one route into it anyone has MEASURED. It is NOT the only
        route: `_foreign_stages` renders 403-on-the-project, 404-on-the-project and no-kanban-view
        identically as None, and the refusal's own escape names the second of those ("run
        `vikunja-mcp setup` against it if its kanban view is missing"). What the OTHER permission
        route cannot do is reach this branch: an unshared project 403s the TASK one branch earlier.
        Before #1200 that branch was driven through `forbidden=True`, which modelled a permission
        the fake then failed to apply to `get_task` at all — so the fixture was a state nothing
        measured produces, and fixing `get_task` would have re-routed those tests to a different
        branch in silence."""
        self.other_projects[project_id]["view"] = None

    def forbid_project(self, project_id):
        """Close an ALREADY-BUILT project to the token, the way life does it: shared, populated,
        then unshared. `add_project(forbidden=True)` cannot express that order.

        Returns the UNDO — what a human granting this token access to that project looks like
        here — because "share it and see what changes" is a measurement two gates want (#1190:
        sharing makes an unknown stage KNOWABLE without releasing the card) and re-opening by
        poking `_forbidden` from a test spells the same act two ways."""
        self._forbidden.add(project_id)

        def share():
            self._forbidden.discard(project_id)

        return share

    def _read_task(self, task_id):
        """The guard SIX task-scoped calls share: `get_task`, `update_task`, `add_assignee`,
        `remove_assignee`, `add_label`, `remove_label` — and `update_task` going through it is the
        point rather than a convenience.

        SIX, NOT ALL, and the count is the guarantee. `add_comment`, `comments`, `add_relation`,
        `upload_attachment`, `download_attachment` and `move_task` are task-scoped on the real
        client too and do NOT come through here — measured on this fake: `add_comment` and
        `comments` on id 999999 return happily, `add_relation` returns None, `upload_attachment`
        answers a success payload, all for a task that does not exist. #1211 closed the four
        endpoints it MEASURED and did not widen past them; this sentence exists so the next reader
        inherits the boundary rather than the word "every", which is what the pre-#1211 wording
        was careful to say and this rewrite briefly lost.

        THE FOUR ASSIGNEE/LABEL CALLS JOINED IN #1211, BY MEASUREMENT AND NOT BY THE ANALOGY.
        They are the ones the real client does NOT read-modify-write — `PUT /tasks/{id}/assignees`
        and `DELETE /tasks/{id}/assignees/{user_id}`, `PUT /tasks/{id}/labels` and
        `DELETE /tasks/{id}/labels/{label_id}`, four single writes — so nothing
        about the read below FOLLOWS from the two above it, and #1200 left them standing rather
        than copy a shape across untested. Probed against a throwaway real 2.3.0:

            case                          add_assignee  remove_assignee  add_label  remove_label
            unknown task id                        404              404        404           404
            task in an unshared project            403              403        403           403
            happy-path control                     201              200        201           200

        i.e. exactly the split spelled out below, which is why all six now share one guard.

        WHICH TOKEN READ WHICH CELL, because it is not one token and the difference is not
        cosmetic. Three columns were probed with a scoped token of the integration suite's own
        AGENT_PERMS shape. The `remove_label` column could NOT be: that suite grants
        `tasks_labels: ["create", "read_all"]` and no `delete`, so every DELETE on that endpoint
        came back 401 `invalid token provided` — on the HAPPY PATH too, i.e. the endpoint was
        never reached at all. Its column was re-probed twice, and the two agree cell for cell: a
        full JWT (no scoping), and a scoped token minted WITH `tasks_labels: delete`, which is
        what the production bootstrap actually grants.

        The BODIES differ and the statuses do not, which is the whole reason this guard is keyed
        on status. The 404 text differs across the pairs — `This project does not exist.` on
        assignees, `This task does not exist` on labels — and the 403 text differs from the READ's:
        all four write endpoints answer a bare `Forbidden`, while `_read_task` raises the GET's
        `You don't have the permission to see this` for all six. `VikunjaError` carries the STATUS,
        and the status is what a production `except` branches on, so none of that is mirrored.

        MEASURED AND STILL NOT MIRRORED — but the two halves are open for DIFFERENT reasons, and
        an earlier draft of this paragraph got the second one wrong, so read the split.

        The REMOVE half is close to unreachable. Real 2.3.0 answers 403 `Forbidden` when a DELETE
        names a label that is NOT on the task, where the fake is an idempotent no-op (pinned by
        the `mirrors_client` test in `test_workflow_gates.py`). `api.remove_label` has exactly ONE
        caller, `workflow._remove_label`, and it sends the DELETE only for a label present on the
        snapshot in hand — so only snapshot staleness gets there, which is a race, not a route.

        The ADD half is REACHABLE, and that was measured rather than reasoned. Real 2.3.0 answers
        400 code 8001 `This label already exists on the task.` when a PUT adds a label the task
        already carries; the fake appends a second copy and stays green. An earlier draft here
        claimed `_add_label` runs behind a `_has_label` check — it does not. `_add_label` has
        three call sites and only the epic-ready one is guarded (by its own idempotency read of a
        re-fetched parent); `review_task`'s approve and needs_work branches are unguarded, and two
        further sites call `api.add_label` DIRECTLY, bypassing `_add_label` altogether
        (`return_task` adding `blocked`, `decompose` adding `epic`). Driven through the real
        `Workflow` over this fake, agent tools only: a second `review_task(..., 'approve')` on a
        card that already carries `reviewed` re-adds it, and a `return_task` on a card a human
        already labelled `blocked` re-adds that. On a real server both are the 400 — and in the
        approve case the `[review] APPROVE` comment is written BEFORE the label, so the refusal
        would land on a verdict that is already half-applied. Filed as VMCP-311 (1216); NOT fixed
        here, because the fix belongs in `workflow` and mirroring the 400 in the fake first would
        turn a live suite red without closing anything.

        `remove_assignee` needed no such decision: measured, the server answers 200 for a user
        that is not assigned AND for one that does not exist, which is what the fake's filter
        already does.

        1:1 with the client, structurally: `VikunjaAPI.update_task` is read-modify-write, and its
        FIRST statement is `self.get_task(task_id)`. So on the real client an unknown id and an
        unreadable one raise from the READ, before any POST is ever sent, and both tools answer
        with the same status get_task would.

        404 — an unknown id. The fake used to raise KeyError here, which no production
        `except VikunjaError` can catch, so every "the task went away under us" branch around an
        update read as untestable and was written blind (#1200; get_task was fixed for this in
        #1179 and its neighbour was left standing).

        403 — the task belongs to a project in `_forbidden`, i.e. one that EXISTS but was never
        shared with this token. Measured on real 2.3.0 (#1198): such a read is
        `{"message":"You don't have the permission to see this"}`, not a 404 — the split
        `_offboard_predecessor` keys "gone" against "unknown" on. Before #1200 `_forbidden` was
        consulted by project-scoped calls only, so the fake could not produce a 403 on a TASK at
        all: #1179 shipped that branch with no fixture of any kind, and #1190 reached it by
        hand-rolling a wrapper around `api.get_task` in the test file (`git log -S'_forbid_task'`
        names `5f26333` and nothing earlier).

        KNOWN, DELIBERATE, STILL OPEN: `get_task` embeds a related task UNCONDITIONALLY, while
        real 2.3.0 filters `related_tasks` by the READER's permission (measured, two readers on
        the same card in the same moment: the owner reads `{'blocked': [4]}`, an agent without
        access to the far project reads `{}` — #1198). So a forbidden project whose card is still
        embedded in a successor models the RACE (access lost between the relation read and this
        one), not the steady state. Teaching the fake to filter is NOT a free fidelity win — it
        would make this 403 unreachable by any permission route and need a third, race-shaped
        knob beside `vanish()` — and what to do about the production gap underneath it is the
        human decision parked on #1198."""
        if task_id in self._vanished or task_id not in self.tasks:
            raise VikunjaError(404, "task does not exist")
        task = self.tasks[task_id]
        if task.get("project_id") in self._forbidden:
            raise VikunjaError(403, "You don't have the permission to see this")
        return task

    def get_task(self, task_id):
        t = self._snapshot(self._read_task(task_id))
        # related_tasks — дикт по kind, выведен из relations "на лету" (не хранится на таске) ->
        # add_relation сразу видно в get_task. Реальная 2.3.0 авто-создаёт ОБРАТНУЮ связь на другой
        # задаче (записали "P precedes S" — на S видно "follows: P"); add_relation не трогаем
        # (self.relations хранит ровно записанное), инверсию синтезируем ЗДЕСЬ, на чтении: если
        # task_id — ЦЕЛЬ связи, отдаём её под инвертированным kind (_INVERSE_RELATION). Значения —
        # НЕ полные дикты, а HOLLOW-копии (labels/assignees/nested related_tasks = None), точно как
        # у сервера (см. _related_subdict): кто читает labels связанной задачи, обязан её дофетчить.
        related: dict[str, list[dict]] = {}
        # `_vanished` counts as embeddable here on purpose — that IS the race vanish() models.
        embeddable = {**self.tasks, **self._vanished}
        for tid, other_id, kind in self.relations:
            if tid == task_id and other_id in embeddable:
                related.setdefault(kind, []).append(self._related_subdict(embeddable[other_id]))
            elif other_id == task_id and tid in embeddable:
                inverse = _INVERSE_RELATION.get(kind, kind)
                related.setdefault(inverse, []).append(self._related_subdict(embeddable[tid]))
        t["related_tasks"] = related
        # attachments arrive INSIDE the task JSON (tasks:read_one), each {id, task_id,
        # file:{name,mime,size}}. Mirror the real server EXACTLY: a task with NONE reads back
        # `attachments: None` (not []), so workflow.get_task must tolerate the None (verified
        # against real 2.3.0). Copy so a test mutating the dossier can't corrupt fake state.
        atts = self._attachments.get(task_id)
        t["attachments"] = (
            [{**a, "file": dict(a["file"])} for a in atts] if atts else None
        )
        return t

    def download_attachment(self, task_id, attachment_id):
        # keyed off the OUTER attachment id (task["attachments"][].id), 1:1 with the real
        # endpoint GET /tasks/{id}/attachments/{attachment_id}; a missing pair 404s like the
        # server (code 4011/4002) rather than KeyError-ing.
        data = self._attachment_bytes.get((task_id, attachment_id))
        if data is None:
            raise VikunjaError(404, "This task attachment does not exist.")
        return data

    def upload_attachment(self, task_id, filename, data, mime=None):
        # 1:1 with the real endpoint PUT /tasks/{id}/attachments (#137): stores the file (so a
        # later get_task surfaces it — round-trip fidelity) and returns the SAME envelope the real
        # 2.3.0 server sends — {"errors": None, "success": [attachment]} — where the attachment has
        # the shape workflow.get_task reads ({id, task_id, file:{id,name,mime,size}}). The created_
        # by/created scalars the server also returns are omitted, exactly as this fake models them
        # nowhere else (get_task's attachment view never reads them). Reuses add_attachment so the
        # stored shape stays identical to a test-seeded one.
        att = self.add_attachment(
            task_id, filename, mime or "application/octet-stream", data=data
        )
        return {"errors": None, "success": [att]}

    def update_task(self, task_id, **fields):
        # read-modify-write, exactly like the real client: the READ is what refuses an unknown
        # (404) or unreadable (403) id, and it happens before anything is written. See _read_task.
        t = self._read_task(task_id)
        # A project_id change is a MOVE, and real 2.3.0 re-indexes on it: measured on a live
        # container, FRNT-2 became BACK-3 when it landed in a project already holding BACK-2 —
        # the target's own counter assigns the next free index, so no collision and the OLD
        # ref stops resolving. Modelling only the field would make a tool that hands back the
        # new ref testable against a fake that never changes it.
        new_project = fields.get("project_id")
        if new_project is not None and new_project != t.get("project_id"):
            state = self._project_state(new_project)
            idx, identifier = self._task_identity(state["project"])
            fields = {**fields, "index": idx, "identifier": identifier}
            # ...and it leaves the source board for the target's DEFAULT bucket (measured:
            # gone from A entirely, sitting in B's first column).
            self.task_bucket[task_id] = state["buckets"][0]["id"]
        t.update(fields)
        return self._snapshot(t)

    def create_task(self, project_id, title, description="", priority=0):
        state = self._project_state(project_id)
        idx, identifier = self._task_identity(state["project"])
        t = {
            "id": next(self._ids), "title": title, "description": description,
            "priority": priority, "index": idx, "identifier": identifier,
            "project_id": state["project"]["id"],
            "done": False, "assignees": [], "labels": [],
        }
        self.tasks[t["id"]] = t
        self.task_bucket[t["id"]] = state["buckets"][0]["id"]  # default = первый бакет ЦЕЛИ
        return self._snapshot(t)

    def comments(self, task_id):
        return list(self._comments.get(task_id, []))

    def add_comment(self, task_id, text):
        # created монотонно растёт и лексикографически сортируем — как ISO у реального API.
        # Храним HTML 1:1 с реальным клиентом (#85): агентский текст -> text_to_html.
        entry = {
            "comment": text_to_html(text), "author": dict(self.me_user),
            "created": f"2026-07-08T00:00:00.{next(self._ids):06d}Z",
        }
        self._comments.setdefault(task_id, []).append(entry)
        return entry

    def add_assignee(self, task_id, user_id):
        # `_read_task` rather than a subscript: MEASURED on real 2.3.0 (#1211), see its docstring.
        task = self._read_task(task_id)
        user = self.users.get(user_id, {"id": user_id, "username": f"u{user_id}"})
        task["assignees"].append(user)

    def remove_assignee(self, task_id, user_id):
        t = self._read_task(task_id)
        t["assignees"] = [a for a in t["assignees"] if a["id"] != user_id]

    def add_relation(self, task_id, other_task_id, kind):
        self.relations.append((task_id, other_task_id, kind))

    def projects(self):
        return [dict(self.project)] + [
            dict(e["project"]) for pid, e in self.other_projects.items()
            if pid not in self._forbidden
        ]

    def create_project(self, title):
        # mirror real 2.3.0: create_task sends only title -> the new project has an empty
        # identifier (tasks in it then read back identifier "#<index>")
        self.project = {"id": next(self._ids), "title": title, "identifier": ""}
        for b in list(self._buckets):
            self._buckets.remove(b)
        for title_ in ["To-Do", "Doing", "Done"]:  # vikunja auto-buckets
            self.add_bucket(title_)
        return dict(self.project)

    def project_users(self, project_id):
        return [{"username": u, "permission": p} for _, u, p in self.shares]

    def share_project(self, project_id, username, permission):
        if not any(u == username for _, u, _ in self.shares):
            self.shares.append((project_id, username, permission))

    def views(self, project_id):
        view = self._project_state(project_id)["view"]
        return [dict(view)] if view is not None else []

    def kanban_view(self, project_id):
        # Derived from views() and refusing with the client's own words, not short-circuited on
        # the stored view: that is what makes drop_kanban_view() reach the same branch production
        # does (VikunjaAPI.kanban_view scans views() and raises this 404 when none is kanban).
        for v in self.views(project_id):
            if v["view_kind"] == "kanban":
                return v
        raise VikunjaError(404, "project has no kanban view — run `vikunja-mcp setup`")

    def buckets(self, project_id, view_id):
        found = self._project_state(project_id)["buckets"]
        return [dict(b) for b in sorted(found, key=lambda x: x["position"])]

    def create_bucket(self, project_id, view_id, title):
        return dict(self.add_bucket(title))

    def update_bucket(self, project_id, view_id, bucket, position):
        # full-replace как у реального клиента: POST шлёт title+position, поэтому
        # заголовок берём из переданного bucket (так работает in-place переименование)
        real = next(b for b in self._buckets if b["id"] == bucket["id"])
        real["title"] = bucket["title"]
        real["position"] = position
        return dict(real)

    def delete_bucket(self, project_id, view_id, bucket_id):
        if any(bid == bucket_id for bid in self.task_bucket.values()):
            raise AssertionError("нельзя удалять непустой бакет")
        self._buckets = [b for b in self._buckets if b["id"] != bucket_id]

    def _kanban_copy(self, task_id, task):
        """The snapshot a KANBAN read hands out. Identical to `_snapshot` unless this id is in
        `kanban_assignee_blackout`, in which case `assignees` comes back EMPTY while
        `get_task` keeps returning them — the #885 divergence, measured on live 2.3.0."""
        snap = self._snapshot(task)
        if task_id in self.kanban_assignee_blackout:
            snap["assignees"] = []
        return snap

    def view_tasks(self, project_id, view_id, require_titles=None):
        # mirror the real client (#43/#126): require_titles restricts EXHAUSTIVE paging to those
        # buckets; every OTHER bucket returns only its first page (page_size), NOT its full history
        # — an unbounded Done/Backlog/Your Call is not fully read on the light next_task board.
        # require_titles=None => exhaustive board (no truncation), as claim/advance/setup read it.
        # (Was: always the full board regardless of require_titles — which is exactly why no unit
        # test caught the #126 livelock; task_bucket.get lets an orphaned task be off every board.)
        self.last_require_titles = require_titles
        self.view_tasks_calls += 1
        out = []
        for b in self._project_state(project_id)["buckets"]:
            tasks = [
                self._kanban_copy(tid, t) for tid, t in self.tasks.items()
                if self.task_bucket.get(tid) == b["id"]
            ]
            if require_titles is not None and b["title"] not in require_titles:
                tasks = tasks[: self.page_size]  # non-required bucket -> only its first page
            out.append({**b, "tasks": tasks})
        return out

    def move_task(self, project_id, view_id, bucket_id, task_id):
        # ужесточено: реальный эндпоинт POST /projects/{p}/views/{v}/buckets/{b}/tasks
        # 404-ит на бакете, не принадлежащем view ЭТОГО проекта; старый фейк игнорировал
        # project_id целиком, и такой баг проходил молча (#125-режим).
        state = self._project_state(project_id)
        if bucket_id not in {b["id"] for b in state["buckets"]}:
            raise VikunjaError(404, "bucket does not exist on this project's view")
        self.task_bucket[task_id] = bucket_id

    def configure_kanban(self, project_id, view, default_bucket_id, done_bucket_id):
        self.view_config = {
            "default_bucket_id": default_bucket_id, "done_bucket_id": done_bucket_id,
            "bucket_configuration_mode": "manual",
        }
        return self.view_config

    def labels(self):
        return [dict(lb) for lb in self._labels]

    def create_label(self, title):
        lb = {"id": next(self._ids), "title": title}
        self._labels.append(lb)
        return dict(lb)

    def add_label(self, task_id, label_id):
        # the task is read FIRST, as on the server: measured, an unknown task id answers 404
        # `This task does not exist` even when the label_id is valid (#1211).
        task = self._read_task(task_id)
        lb = next(x for x in self._labels if x["id"] == label_id)
        task["labels"].append(dict(lb))

    def remove_label(self, task_id, label_id):
        # идемпотентно по label_id: фильтруем по id, отсутствующий id — no-op. That HALF is a
        # measured divergence, deliberately kept — see `_read_task`'s "MEASURED AND NOT MIRRORED".
        t = self._read_task(task_id)
        t["labels"] = [lb for lb in t["labels"] if lb["id"] != label_id]

    def get_or_create_label(self, title):
        for lb in self._labels:
            if lb["title"] == title:
                return dict(lb)
        return self.create_label(title)
