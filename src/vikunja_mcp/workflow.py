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

        stuck = [t for t in board.get("Queue", []) if my_id in self._assignee_ids(t)]
        if stuck:
            stuck.sort(key=lambda t: -t.get("priority", 0))
            return {
                "resume": True, "stage": "Queue", "task": self._summary(stuck[0]),
                "note": "клейм не доведён — вызови claim(task_id) повторно",
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
        me = self._me()
        # self-heal: партиальный клейм (assign прошёл, move — нет) или человек руками
        # вернул заклеймленную задачу в Queue — я тут единственный assignee, долечиваем
        # вместо отказа. Кто-то ДРУГОЙ среди assignees (один или вместе со мной) — отказ как раньше.
        self_heal = len(existing) == 1 and existing[0].get("id") == me["id"]
        if existing and not self_heal:
            names = ", ".join(a.get("username", "?") for a in existing)
            raise WorkflowError(f"уже занята ({names}) — возьми следующую через next_task")

        if not self_heal:
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

    def _move(self, task_id: int, stage: str) -> None:
        self.api.move_task(
            self.project_id, self._view()["id"], self._bucket(stage)["id"], task_id
        )

    def advance(
        self, task_id: int, to: str,
        spec: str | None = None, worklog: str | None = None, evidence: str | None = None,
    ) -> dict:
        to = (to or "").strip().lower()
        if to == "done":
            raise WorkflowError("в Done переводит только человек после ревью — тебе туда нельзя")
        if to not in AGENT_ADVANCE:
            raise WorkflowError(f"недопустимый переход '{to}'; доступны: build, review")
        from_stage, to_stage = AGENT_ADVANCE[to]

        task, stage = self._find_task(task_id)
        self._require_mine(task)
        if stage != from_stage:
            raise WorkflowError(
                f"переход в {to_stage} возможен только из {from_stage}, задача сейчас в {stage}"
            )

        if to == "build":
            if not (spec or "").strip():
                raise WorkflowError("нужен spec: краткое описание подхода перед реализацией")
            self.api.add_comment(task_id, f"[spec]\n{spec.strip()}")
        else:
            if not (worklog or "").strip() or not (evidence or "").strip():
                raise WorkflowError(
                    "для Review нужны worklog (что сделано) и evidence "
                    "(ссылка на коммит/PR или вывод верификации)"
                )
            self.api.add_comment(
                task_id, f"[worklog]\n{worklog.strip()}\n\nEvidence: {evidence.strip()}"
            )
        self._move(task_id, to_stage)
        return {"moved_to": to_stage, "task_id": task_id}

    def call_human(self, task_id: int, question: str) -> dict:
        if not (question or "").strip():
            raise WorkflowError("сформулируй вопрос: что нужно от человека и какие варианты ты рассмотрел")
        task, stage = self._find_task(task_id)
        self._require_mine(task)
        if stage not in ACTIVE_STAGES:
            raise WorkflowError(f"call_human доступен только из Design/Build, задача в {stage}")
        self.api.add_comment(task_id, f"[нужен человек] {question.strip()}")
        self._move(task_id, "Call to Human")
        return {
            "moved_to": "Call to Human", "task_id": task_id,
            "note": "assignee сохранён; человек ответит комментом и вернёт задачу в Design/Build",
        }

    def return_task(self, task_id: int, reason: str) -> dict:
        if not (reason or "").strip():
            raise WorkflowError("укажи причину блокировки — она уйдёт комментом в задачу")
        task, _stage = self._find_task(task_id)
        self._require_mine(task)
        self.api.add_comment(task_id, f"[blocked] {reason.strip()}")
        label = self.api.get_or_create_label(LABEL_BLOCKED)
        self.api.add_label(task_id, label["id"])
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        return {"moved_to": "Backlog", "task_id": task_id, "labeled": LABEL_BLOCKED}

    def decompose(self, task_id: int, subtasks: list[dict]) -> dict:
        if not subtasks or len(subtasks) < 2:
            raise WorkflowError("декомпозиция — это минимум 2 подзадачи")
        if any(not (st.get("title") or "").strip() for st in subtasks):
            raise WorkflowError("у каждой подзадачи должен быть title")
        task, _stage = self._find_task(task_id)
        self._require_mine(task)

        created = []
        for st in subtasks:
            child = self.api.create_task(
                self.project_id, st["title"].strip(),
                description=st.get("description", ""), priority=int(st.get("priority", 0)),
            )
            self.api.add_relation(child["id"], task_id, "parenttask")
            self._move(child["id"], "Queue")
            created.append({"id": child["id"], "title": child["title"]})

        listing = ", ".join(f"#{c['id']} {c['title']}" for c in created)
        self.api.add_comment(task_id, f"[decompose] создано: {listing}")
        label = self.api.get_or_create_label(LABEL_EPIC)
        self.api.add_label(task_id, label["id"])
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        return {"created": created, "parent": {"id": task_id, "moved_to": "Backlog", "labeled": LABEL_EPIC}}

    def comment(self, task_id: int, text: str) -> dict:
        if not (text or "").strip():
            raise WorkflowError("пустой коммент не нужен")
        self._find_task(task_id)
        self.api.add_comment(task_id, text.strip())
        return {"commented": task_id}

    def get_task(self, task_id: int) -> dict:
        task, stage = self._find_task(task_id)
        raw_comments = self.api.comments(task_id)
        return {
            **self._summary(task),
            "stage": stage,
            "assignees": [a.get("username", "?") for a in task.get("assignees") or []],
            "labels": [lb.get("title") for lb in task.get("labels") or []],
            "comments": [
                {"author": c.get("author", {}).get("username", "?"), "text": c.get("comment", "")}
                for c in raw_comments
            ],
        }
