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
