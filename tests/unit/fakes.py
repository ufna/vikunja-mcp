"""In-memory дублёр VikunjaAPI для unit-тестов workflow/setup."""
import itertools


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
        self._comments = {}      # task_id -> [{"comment", "author"}]
        self._labels = []
        self.relations = []      # (task_id, other_id, kind)
        self.view_config = None  # последний configure_kanban
        self.shares = []         # (project_id, username, permission)
        self.last_require_titles = None  # require_titles последнего view_tasks (#43, для тестов)

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

    def stage_of(self, task_id):
        bid = self.task_bucket[task_id]
        return next(b["title"] for b in self._buckets if b["id"] == bid)

    def comments_text(self, task_id):
        return [c["comment"] for c in self._comments.get(task_id, [])]

    # --- поверхность VikunjaAPI ---
    def me(self):
        return self.me_user

    def get_task(self, task_id):
        t = dict(self.tasks[task_id])
        # зеркалим реальную vikunja 2.3.0: related_tasks — дикт по kind, значения —
        # ПОЛНЫЕ таск-дикты (наблюдалось эмпирически), выведен из relations "на лету"
        # (не хранится отдельно на таске) -> add_relation сразу видно в get_task.
        related: dict[str, list[dict]] = {}
        for tid, other_id, kind in self.relations:
            if tid == task_id and other_id in self.tasks:
                related.setdefault(kind, []).append(dict(self.tasks[other_id]))
        t["related_tasks"] = related
        return t

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
        # created монотонно растёт и лексикографически сортируем — как ISO у реального API
        entry = {
            "comment": text, "author": dict(self.me_user),
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
        # реальный клиент постранично мёржит бакеты; фейк держит всё в памяти и всегда
        # отдаёт полный борд, поэтому require_titles тут не влияет на результат — лишь
        # записываем его, чтобы тест мог проверить, что next_task просит лёгкий борд (#43).
        self.last_require_titles = require_titles
        out = []
        for b in self._buckets:
            tasks = [dict(t) for tid, t in self.tasks.items() if self.task_bucket[tid] == b["id"]]
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
