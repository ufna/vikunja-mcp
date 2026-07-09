"""In-memory дублёр VikunjaAPI для unit-тестов workflow/setup."""
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
        self.view_config = None  # последний configure_kanban
        self.shares = []         # (project_id, username, permission)
        self.last_require_titles = None  # require_titles последнего view_tasks (#43, для тестов)
        self.view_tasks_calls = 0  # #126: сколько раз звали view_tasks (1 без escalation, 2 с ним)
        # #126: как max_items_per_page реального сервера — не-required бакеты усекаются до первой
        # страницы на лёгком борде (#43); дефолт 50 не трогает существующие тесты (<50 задач/бакет)
        self.page_size = 50

    # --- helpers для тестов ---
    def _task_identity(self):
        """Mirror Vikunja: every task read carries a per-project `index` and a computed
        `identifier` = '<project identifier>-<index>' (or '#<index>' when the project has
        no identifier prefix — verified against real 2.3.0)."""
        idx = next(self._task_index)
        prefix = self.project.get("identifier") or ""
        return idx, (f"{prefix}-{idx}" if prefix else f"#{idx}")

    def add_bucket(self, title):
        b = {"id": next(self._ids), "title": title, "position": (len(self._buckets) + 1) * 100}
        self._buckets.append(b)
        return b

    def bucket_id(self, title):
        return next(b["id"] for b in self._buckets if b["title"] == title)

    def add_task(self, title, bucket_title, priority=0, assignee=None, labels=()):
        idx, identifier = self._task_identity()
        t = {
            "id": next(self._ids), "title": title, "description": "", "priority": priority,
            "index": idx, "identifier": identifier,
            "done": False, "assignees": [assignee] if assignee else [],
            "labels": [{"id": next(self._ids), "title": lb} for lb in labels],
        }
        self.tasks[t["id"]] = t
        self.task_bucket[t["id"]] = self.bucket_id(bucket_title)
        return t

    def add_attachment(self, task_id, name, mime, data=b"", size=None):
        """Test helper: attach a file to a task, mirroring real 2.3.0's shape — each entry is
        {id, task_id, file:{id, name, mime, size}} and the download endpoint keys off the
        OUTER id (attachment id), not file.id. `size` overrides the metadata size (defaults to
        len(data)) so a test can exercise the too-large guard without a giant buffer."""
        aid = next(self._ids)
        att = {
            "id": aid, "task_id": task_id,
            "file": {
                "id": aid, "name": name, "mime": mime,
                "size": len(data) if size is None else size,
            },
        }
        self._attachments.setdefault(task_id, []).append(att)
        self._attachment_bytes[(task_id, aid)] = data
        return att

    def stage_of(self, task_id):
        bid = self.task_bucket[task_id]
        return next(b["title"] for b in self._buckets if b["id"] == bid)

    def comments_text(self, task_id):
        # comments are STORED as HTML (mirrors the real client, #85); this helper renders
        # them back to the plain text a human/agent reads, so marker/content assertions
        # stay meaningful. Use `comments(task_id)` for the raw stored HTML.
        return [html_to_text(c["comment"]) for c in self._comments.get(task_id, [])]

    # --- поверхность VikunjaAPI ---
    def me(self):
        return self.me_user

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

    def get_task(self, task_id):
        t = dict(self.tasks[task_id])
        # related_tasks — дикт по kind, выведен из relations "на лету" (не хранится на таске) ->
        # add_relation сразу видно в get_task. Реальная 2.3.0 авто-создаёт ОБРАТНУЮ связь на другой
        # задаче (записали "P precedes S" — на S видно "follows: P"); add_relation не трогаем
        # (self.relations хранит ровно записанное), инверсию синтезируем ЗДЕСЬ, на чтении: если
        # task_id — ЦЕЛЬ связи, отдаём её под инвертированным kind (_INVERSE_RELATION). Значения —
        # НЕ полные дикты, а HOLLOW-копии (labels/assignees/nested related_tasks = None), точно как
        # у сервера (см. _related_subdict): кто читает labels связанной задачи, обязан её дофетчить.
        related: dict[str, list[dict]] = {}
        for tid, other_id, kind in self.relations:
            if tid == task_id and other_id in self.tasks:
                related.setdefault(kind, []).append(self._related_subdict(self.tasks[other_id]))
            elif other_id == task_id and tid in self.tasks:
                inverse = _INVERSE_RELATION.get(kind, kind)
                related.setdefault(inverse, []).append(self._related_subdict(self.tasks[tid]))
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

    def update_task(self, task_id, **fields):
        self.tasks[task_id].update(fields)
        return dict(self.tasks[task_id])

    def create_task(self, project_id, title, description="", priority=0):
        idx, identifier = self._task_identity()
        t = {
            "id": next(self._ids), "title": title, "description": description,
            "priority": priority, "index": idx, "identifier": identifier,
            "done": False, "assignees": [], "labels": [],
        }
        self.tasks[t["id"]] = t
        self.task_bucket[t["id"]] = self._buckets[0]["id"]  # default = первый бакет
        return dict(t)

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
        user = self.users.get(user_id, {"id": user_id, "username": f"u{user_id}"})
        self.tasks[task_id]["assignees"].append(user)

    def remove_assignee(self, task_id, user_id):
        t = self.tasks[task_id]
        t["assignees"] = [a for a in t["assignees"] if a["id"] != user_id]

    def add_relation(self, task_id, other_task_id, kind):
        self.relations.append((task_id, other_task_id, kind))

    def projects(self):
        return [dict(self.project)]

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
        return [dict(self.view)]

    def kanban_view(self, project_id):
        return dict(self.view)

    def buckets(self, project_id, view_id):
        return [dict(b) for b in sorted(self._buckets, key=lambda x: x["position"])]

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
        for b in self._buckets:
            tasks = [
                dict(t) for tid, t in self.tasks.items()
                if self.task_bucket.get(tid) == b["id"]
            ]
            if require_titles is not None and b["title"] not in require_titles:
                tasks = tasks[: self.page_size]  # non-required bucket -> only its first page
            out.append({**b, "tasks": tasks})
        return out

    def move_task(self, project_id, view_id, bucket_id, task_id):
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
        lb = next(x for x in self._labels if x["id"] == label_id)
        self.tasks[task_id]["labels"].append(dict(lb))

    def remove_label(self, task_id, label_id):
        # идемпотентно: фильтруем по id, отсутствующий id — no-op
        t = self.tasks[task_id]
        t["labels"] = [lb for lb in t["labels"] if lb["id"] != label_id]

    def get_or_create_label(self, title):
        for lb in self._labels:
            if lb["title"] == title:
                return dict(lb)
        return self.create_label(title)
