"""Стадии и гейты агентского флоу. Правила зашиты здесь, не в промптах."""
from typing import Any

STAGES = ["Backlog", "Queue", "Design", "Build", "Review", "Call to Human", "Done"]
ACTIVE_STAGES = ("Design", "Build")
LABEL_BLOCKED = "blocked"
LABEL_EPIC = "epic"
LABEL_BUG = "bug"
LABEL_REVIEWED = "reviewed"            # прошёл независимое агентское ревью
LABEL_REVIEW_FAILED = "review-failed"  # отбит на доработку, сейчас переделывается

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

    def _add_label(self, task_id: int, title: str) -> None:
        label = self.api.get_or_create_label(title)
        self.api.add_label(task_id, label["id"])

    def _remove_label(self, task: dict, title: str) -> None:
        # снимаем только реально висящую на снапшоте метку — иначе DELETE по
        # несуществующей связи вернул бы 404
        lb = next((x for x in task.get("labels") or [] if x.get("title") == title), None)
        if lb:
            self.api.remove_label(task["id"], lb["id"])

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
                "note": (
                    "это твоя активная задача — новую не клеймить. Сначала сверь "
                    "фактическое состояние: прочитай досье (get_task) и проверь "
                    "код/репо — работа могла быть уже сделана целиком или частично. "
                    "Сделана — верифицируй и advance(to='review') с честным evidence; "
                    "нет — продолжай с того места, где она остановилась"
                ),
            }

        stuck = [t for t in board.get("Queue", []) if my_id in self._assignee_ids(t)]
        if stuck:
            stuck.sort(key=lambda t: -t.get("priority", 0))
            return {
                "resume": True, "stage": "Queue", "task": self._summary(stuck[0]),
                "note": (
                    "задача в Queue назначена на тебя (человеком или недоведённым "
                    "клеймом) — вызови claim(task_id), он доведёт её в Design"
                ),
            }

        for t in sorted(board.get("Review", []), key=lambda t: -t.get("priority", 0)):
            if not self._has_label(t, LABEL_BUG) or my_id in self._assignee_ids(t):
                continue
            # вердикт актуален, только если он свежее последнего отчёта: после цикла
            # needs_work -> доработка -> Review задача должна снова попасть к ревьюеру
            comments = self.api.comments(t["id"])
            last_review = max(
                (c.get("created") or "" for c in comments
                 if (c.get("comment") or "").startswith("[review]")),
                default=None,
            )
            last_worklog = max(
                (c.get("created") or "" for c in comments
                 if (c.get("comment") or "").startswith("[worklog]")),
                default="",
            )
            if last_review is not None and last_review >= last_worklog:
                continue
            return {
                "review": True, "task": self._summary(t),
                "note": (
                    "багфикс ждёт независимого ревью: воспроизведи, проверь что фикс "
                    "закрывает причину (не симптом), прогони запуском и вынеси вердикт "
                    "review_task(task_id, verdict=..., report=...). НЕ ревьюй, если "
                    "писал этот код в этой сессии"
                ),
            }

        # Queue-контракт: свободные берём, назначенные на другого НЕ трогаем — это «для людей»
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
        fresh_ids = self._assignee_ids(fresh)
        others = [aid for aid in fresh_ids if aid != me["id"]]
        if others:
            self.api.remove_assignee(task_id, me["id"])
            raise WorkflowError("проиграна гонка за задачу — возьми следующую через next_task")
        # vanish-window: человек мог снять моё назначение в окно между assign и re-read.
        # others пуст — но без меня в assignees move уведёт задачу в Design «ничьей»
        # (невидимо для next_task и незаклеймимо из Queue). Отказ до move закрывает окно
        # и в обычном, и в self-heal пути (там add_assignee не звался — окно то же).
        if me["id"] not in fresh_ids:
            raise WorkflowError(
                "ассайн исчез во время клейма (человек снял назначение) — повтори next_task"
            )

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
        root_cause: str | None = None,
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
                    "для Review нужен отчёт: worklog (что сделано и как проверено) и "
                    "evidence (ссылка на коммит/PR или вывод верификации); для багфиксов "
                    "дополнительно root_cause — причина бага, а не симптом"
                )
            report = ["[worklog]"]
            if (root_cause or "").strip():
                report.append(f"Причина: {root_cause.strip()}")
            report.append(f"Сделано: {worklog.strip()}")
            report.append(f"\nEvidence: {evidence.strip()}")
            self.api.add_comment(task_id, "\n".join(report))
            # resubmit-reset: снимаем прошлый review-failed (no-op на первом сабмите)
            self._remove_label(task, LABEL_REVIEW_FAILED)
        self._move(task_id, to_stage)
        result = {"moved_to": to_stage, "task_id": task_id}
        # push-нудж: багфикс требует независимого ревью — в стиле next/note-хинтов
        # просим оркестратора сразу задиспатчить свежий review-саб-агент в фоне
        if to == "review" and self._has_label(task, LABEL_BUG):
            result["review_needed"] = True
            result["note"] = (
                "это баг — сразу задиспатчь свежий review-саб-агент в фоне "
                "(он вынесет review_task), и параллельно бери следующую задачу"
            )
        return result

    def review_task(self, task_id: int, verdict: str, report: str) -> dict:
        verdict = (verdict or "").strip().lower()
        if verdict not in ("approve", "needs_work"):
            raise WorkflowError("verdict должен быть 'approve' или 'needs_work'")
        if not (report or "").strip():
            raise WorkflowError(
                "нужен report: что воспроизвёл/проверил запуском и почему такой вердикт"
            )
        task, stage = self._find_task(task_id)
        if stage != "Review":
            raise WorkflowError(f"ревьюить можно только задачи в Review, эта в {stage}")

        if verdict == "approve":
            self.api.add_comment(task_id, f"[review] APPROVE\n{report.strip()}")
            self._add_label(task_id, LABEL_REVIEWED)
            self._remove_label(task, LABEL_REVIEW_FAILED)
            return {
                "verdict": "approve", "task_id": task_id,
                "note": "вердикт записан; в Done задачу переводит человек",
            }
        self.api.add_comment(task_id, f"[review] NEEDS WORK\n{report.strip()}")
        self._add_label(task_id, LABEL_REVIEW_FAILED)
        self._remove_label(task, LABEL_REVIEWED)
        self._move(task_id, "Build")
        return {
            "verdict": "needs_work", "task_id": task_id, "moved_to": "Build",
            "note": "задача вернулась имплементеру — он увидит её в next_task",
        }

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
        """Полное досье: в отличие от _summary (next_task/claim), описание НЕ обрезано
        и добавлены related — компактный dict {relation_kind: [{"id", "title"}, ...]}."""
        _, stage = self._find_task(task_id)
        task = self.api.get_task(task_id)
        raw_comments = self.api.comments(task_id)
        related_raw = task.get("related_tasks") or {}
        related = {
            kind: [{"id": rt["id"], "title": rt["title"]} for rt in items]
            for kind, items in related_raw.items()
        }
        return {
            "id": task["id"],
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": task.get("description") or "",
            "stage": stage,
            "assignees": [a.get("username", "?") for a in task.get("assignees") or []],
            "labels": [lb.get("title") for lb in task.get("labels") or []],
            "related": related,
            "comments": [
                {"author": c.get("author", {}).get("username", "?"), "text": c.get("comment", "")}
                for c in raw_comments
            ],
        }
