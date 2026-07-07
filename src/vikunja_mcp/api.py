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
        return next(v for v in self.views(project_id) if v["view_kind"] == "kanban")

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

    def view_tasks(self, project_id: int, view_id: int) -> list[dict]:
        return self._req("GET", f"/projects/{project_id}/views/{view_id}/tasks") or []

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
        for label in self.labels():
            if label.get("title") == title:
                return label
        return self.create_label(title)
