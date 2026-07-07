"""Стадии и гейты агентского флоу. Правила зашиты здесь, не в промптах."""
from typing import Any

STAGES = ["Backlog", "Queue", "Design", "Build", "Review", "Call to Human", "Done"]
ACTIVE_STAGES = ("Design", "Build")
LABEL_BLOCKED = "blocked"
LABEL_EPIC = "epic"

# advance: to -> (откуда, куда)
AGENT_ADVANCE = {"build": ("Design", "Build"), "review": ("Build", "Review")}


class WorkflowError(Exception):
    """Сообщение показывается агенту как результат тулзы."""


class Workflow:
    def __init__(self, api: Any, project_id: int):
        self.api = api
        self.project_id = project_id
        self._me_cache: dict | None = None
        self._view_cache: dict | None = None
        self._buckets_cache: dict[str, dict] | None = None

    # --- кэшируемые справочники ---
    def _me(self) -> dict:
        if self._me_cache is None:
            self._me_cache = self.api.me()
        return self._me_cache

    def _view(self) -> dict:
        if self._view_cache is None:
            self._view_cache = self.api.kanban_view(self.project_id)
        return self._view_cache

    def _bucket(self, title: str) -> dict:
        if self._buckets_cache is None:
            found = self.api.buckets(self.project_id, self._view()["id"])
            self._buckets_cache = {b["title"]: b for b in found}
            missing = [s for s in STAGES if s not in self._buckets_cache]
            if missing:
                raise WorkflowError(
                    f"на канбане проекта нет колонок {missing} — прогони `vikunja-mcp setup`"
                )
        return self._buckets_cache[title]

    # --- поиск и проверки ---
    def _board(self) -> list[dict]:
        return self.api.view_tasks(self.project_id, self._view()["id"])

    def _find_task(self, task_id: int) -> tuple[dict, str]:
        for bucket in self._board():
            for task in bucket.get("tasks") or []:
                if task["id"] == task_id:
                    return task, bucket["title"]
        raise WorkflowError(f"задача {task_id} не найдена на доске проекта {self.project_id}")

    @staticmethod
    def _assignee_ids(task: dict) -> list[int]:
        return [a["id"] for a in task.get("assignees") or []]

    @staticmethod
    def _has_label(task: dict, title: str) -> bool:
        return any(lb.get("title") == title for lb in task.get("labels") or [])

    def _require_mine(self, task: dict) -> None:
        if self._me()["id"] not in self._assignee_ids(task):
            raise WorkflowError(f"задача {task['id']} не на тебе — сначала claim")

    @staticmethod
    def _summary(task: dict) -> dict:
        return {
            "id": task["id"],
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": (task.get("description") or "")[:500],
        }

    # --- тулзы ---
    def next_task(self) -> dict:
        board = {b["title"]: (b.get("tasks") or []) for b in self._board()}
        my_id = self._me()["id"]

        mine = [
            (stage, t)
            for stage in ACTIVE_STAGES
            for t in board.get(stage, [])
            if my_id in self._assignee_ids(t)
        ]
        if mine:
            mine.sort(key=lambda st: -st[1].get("priority", 0))
            stage, task = mine[0]
            return {
                "resume": True, "stage": stage, "task": self._summary(task),
                "note": "это твоя активная задача — продолжай её, новую не клеймить",
            }

        queue = [
            t for t in board.get("Queue", [])
            if not self._assignee_ids(t) and not self._has_label(t, LABEL_BLOCKED)
        ]
        if queue:
            queue.sort(key=lambda t: -t.get("priority", 0))
            return {"resume": False, "task": self._summary(queue[0])}
        return {"task": None, "message": "очередь пуста — работы для агента нет"}

    def claim(self, task_id: int) -> dict:
        task, stage = self._find_task(task_id)
        if stage != "Queue":
            raise WorkflowError(f"задача в '{stage}', клеймить можно только из Queue")
        existing = task.get("assignees") or []
        if existing:
            names = ", ".join(a.get("username", "?") for a in existing)
            raise WorkflowError(f"уже занята ({names}) — возьми следующую через next_task")

        me = self._me()
        self.api.add_assignee(task_id, me["id"])
        fresh = self.api.get_task(task_id)
        others = [a for a in fresh.get("assignees") or [] if a["id"] != me["id"]]
        if others:
            self.api.remove_assignee(task_id, me["id"])
            raise WorkflowError("проиграна гонка за задачу — возьми следующую через next_task")

        view = self._view()
        self.api.move_task(self.project_id, view["id"], self._bucket("Design")["id"], task_id)
        self.api.add_comment(task_id, f"[claim] {me['username']} взял задачу в работу")
        return {
            "claimed": True, "task": self._summary(fresh),
            "next": "опиши подход и вызови advance(to='build', spec=...)",
        }
