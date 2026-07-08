"""Vikunja REST client. Gotchas baked in: PUT=create, POST=full-replace update -> RMW."""
from typing import Any

import httpx


class VikunjaError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"Vikunja API {status}: {message}")


class VikunjaAPI:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        base = base_url.rstrip("/")
        if not base.endswith("/api/v1"):
            base += "/api/v1"
        self._client = client or httpx.Client(
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

    def _req(self, method: str, path: str, json: Any = None, params: dict | None = None) -> Any:
        r = self._client.request(method, path, json=json, params=params)
        if r.status_code >= 400:
            raise VikunjaError(r.status_code, r.text[:300])
        return r.json() if r.content else None

    # --- identity ---
    def me(self) -> dict:
        return self._req("GET", "/user")

    # --- tasks ---
    def get_task(self, task_id: int) -> dict:
        return self._req("GET", f"/tasks/{task_id}")

    def update_task(self, task_id: int, **fields: Any) -> dict:
        current = self.get_task(task_id)
        current.update(fields)
        return self._req("POST", f"/tasks/{task_id}", json=current)

    def create_task(
        self, project_id: int, title: str, description: str = "", priority: int = 0
    ) -> dict:
        return self._req(
            "PUT", f"/projects/{project_id}/tasks",
            json={"title": title, "description": description, "priority": priority},
        )

    # --- comments ---
    def comments(self, task_id: int) -> list[dict]:
        return self._req("GET", f"/tasks/{task_id}/comments") or []

    def add_comment(self, task_id: int, text: str) -> dict:
        return self._req("PUT", f"/tasks/{task_id}/comments", json={"comment": text})

    # --- assignees ---
    def add_assignee(self, task_id: int, user_id: int) -> None:
        self._req("PUT", f"/tasks/{task_id}/assignees", json={"user_id": user_id})

    def remove_assignee(self, task_id: int, user_id: int) -> None:
        self._req("DELETE", f"/tasks/{task_id}/assignees/{user_id}")

    # --- relations ---
    def add_relation(self, task_id: int, other_task_id: int, kind: str) -> None:
        self._req(
            "PUT", f"/tasks/{task_id}/relations",
            json={"other_task_id": other_task_id, "relation_kind": kind},
        )

    # --- projects ---
    def projects(self) -> list[dict]:
        return [p for p in (self._req("GET", "/projects") or []) if p.get("id", 0) > 0]

    def create_project(self, title: str) -> dict:
        return self._req("PUT", "/projects", json={"title": title})

    def project_users(self, project_id: int) -> list[dict]:
        return self._req("GET", f"/projects/{project_id}/users") or []

    def share_project(self, project_id: int, username: str, permission: int) -> None:
        for share in self.project_users(project_id):
            if share.get("username") == username:
                return
        self._req(
            "PUT", f"/projects/{project_id}/users",
            json={"username": username, "permission": permission},
        )

    # --- views & buckets ---
    def views(self, project_id: int) -> list[dict]:
        return self._req("GET", f"/projects/{project_id}/views") or []

    def kanban_view(self, project_id: int) -> dict:
        for v in self.views(project_id):
            if v["view_kind"] == "kanban":
                return v
        raise VikunjaError(404, "у проекта нет kanban-вида — прогони `vikunja-mcp setup`")

    def buckets(self, project_id: int, view_id: int) -> list[dict]:
        return self._req("GET", f"/projects/{project_id}/views/{view_id}/buckets") or []

    def create_bucket(self, project_id: int, view_id: int, title: str) -> dict:
        return self._req(
            "PUT", f"/projects/{project_id}/views/{view_id}/buckets", json={"title": title}
        )

    def delete_bucket(self, project_id: int, view_id: int, bucket_id: int) -> None:
        self._req("DELETE", f"/projects/{project_id}/views/{view_id}/buckets/{bucket_id}")

    def update_bucket(
        self, project_id: int, view_id: int, bucket: dict, position: float
    ) -> dict:
        # full-replace бакета: шлём title + position, порядок колонок = position
        return self._req(
            "POST", f"/projects/{project_id}/views/{view_id}/buckets/{bucket['id']}",
            json={"title": bucket["title"], "position": position},
        )

    # эмпирически против vikunja 2.3.0 (см. отчёт F1): GET .../views/{v}/tasks пагинирует
    # tasks[] ВНУТРИ каждого бакета независимо через params={"page": n} с фиксированным
    # page size = max_items_per_page сервера (50 в дефолтной поставке; per_page на эту
    # вложенную пагинацию не влияет). Страницы могут перекрываться на 1-2 задачи из-за
    # нестабильной сортировки при равных ключах (без ORDER BY тайбрейкера) — наблюдался
    # дубль, ни разу не пропуск. Мёржим по (bucket_id, task_id), останавливаемся когда ни
    # один бакет не отдал полную страницу (значит дальше для всех пусто) ИЛИ страница не
    # принесла ни одной новой задачи (защита от зацикливания на нестабильной сортировке).
    _VIEW_TASKS_PAGE_SIZE = 50

    def view_tasks(self, project_id: int, view_id: int) -> list[dict]:
        merged: dict[int, dict] = {}
        seen: dict[int, set] = {}
        page = 1
        while True:
            buckets = self._req(
                "GET", f"/projects/{project_id}/views/{view_id}/tasks", params={"page": page}
            ) or []
            if not buckets:
                break
            saw_full_page = False
            added_new = False
            for bucket in buckets:
                bid = bucket["id"]
                dest = merged.setdefault(bid, {**bucket, "tasks": []})
                ids = seen.setdefault(bid, set())
                tasks = bucket.get("tasks") or []
                if len(tasks) >= self._VIEW_TASKS_PAGE_SIZE:
                    saw_full_page = True
                for task in tasks:
                    if task["id"] not in ids:
                        ids.add(task["id"])
                        dest["tasks"].append(task)
                        added_new = True
            if not saw_full_page or not added_new:
                break
            page += 1
        return list(merged.values())

    def move_task(self, project_id: int, view_id: int, bucket_id: int, task_id: int) -> None:
        self._req(
            "POST", f"/projects/{project_id}/views/{view_id}/buckets/{bucket_id}/tasks",
            json={"task_id": task_id},
        )

    def configure_kanban(
        self, project_id: int, view: dict, default_bucket_id: int, done_bucket_id: int
    ) -> dict:
        # full-replace: без mode+position канбан теряет колонки
        return self._req(
            "POST", f"/projects/{project_id}/views/{view['id']}",
            json={
                "title": view["title"],
                "view_kind": "kanban",
                "bucket_configuration_mode": "manual",
                "position": view["position"] if view.get("position") is not None else 400,
                "default_bucket_id": default_bucket_id,
                "done_bucket_id": done_bucket_id,
            },
        )

    # --- labels ---
    def labels(self) -> list[dict]:
        return self._req("GET", "/labels") or []

    def create_label(self, title: str) -> dict:
        return self._req("PUT", "/labels", json={"title": title})

    def add_label(self, task_id: int, label_id: int) -> None:
        self._req("PUT", f"/tasks/{task_id}/labels", json={"label_id": label_id})

    def get_or_create_label(self, title: str) -> dict:
        # Vikunja labels are owned per-user; GET /labels surfaces every label used on a
        # task the caller can read (not just its own), so match case- and whitespace-
        # insensitively to REUSE an existing label instead of minting a divergent
        # duplicate. Without this an agent typing "Bug"/"bug " forks a second, colorless
        # label beside the canonical one (real incident 2026-07-08: a bot did exactly that).
        want = title.strip().casefold()
        for label in self.labels():
            if (label.get("title") or "").strip().casefold() == want:
                return label
        return self.create_label(title)
