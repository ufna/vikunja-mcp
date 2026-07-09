"""Stages and gates of the agent flow. The rules are baked in here, not in prompts."""
from typing import Any

import httpx

from .api import VikunjaError
from .formatting import html_to_text

STAGES = ["Backlog", "Queue", "Design", "Build", "Review", "Your Call", "Done"]
ACTIVE_STAGES = ("Design", "Build")
# The only stages next_task ever inspects (Queue for free/stuck tasks, Design/Build for my
# active ones, Review for bug re-review). It never reads Done/Backlog/Your Call, so its board
# fetch passes these as view_tasks(require_titles=...) — the unboundedly-growing Done is no
# longer paged exhaustively on every next_task, which is the #43 latency fix.
NEXT_TASK_STAGES = frozenset({"Queue", *ACTIVE_STAGES, "Review"})
LABEL_BLOCKED = "blocked"
LABEL_EPIC = "epic"
LABEL_BUG = "bug"
LABEL_REVIEWED = "reviewed"            # прошёл независимое агентское ревью
LABEL_REVIEW_FAILED = "review-failed"  # отбит на доработку, сейчас переделывается

# Hard sequence gate (option C, epic #94). A predecessor is "ready" — no longer blocks its
# successor — only at Review or Done. The human chose REVIEW (not Done) as the bar so a chain
# can drain autonomously: only a human moves a task to Done, so gating on Done would wedge a
# human between every step. NB: "Your Call" sorts AFTER Review in STAGES yet is NOT ready (a
# parked question), so readiness is explicit set membership, never a positional comparison.
READY_STAGES = frozenset({"Review", "Done"})
# Relation kinds that make the OTHER task a PREDECESSOR of this one. Vikunja auto-inverts:
# "P precedes S" surfaces as "follows: P" on S; "P blocking S" surfaces as "blocked: P" on S.
# The gate keys off THESE kinds only — never parenttask — so old unordered epics whose children
# carry just a parenttask link stay claimable exactly as before (the migration guard).
PREDECESSOR_RELATION_KINDS = ("follows", "blocked")

# advance: to -> (откуда, куда)
AGENT_ADVANCE = {"build": ("Design", "Build"), "review": ("Build", "Review")}


class WorkflowError(Exception):
    """The message is shown to the agent as the tool result."""


class Workflow:
    def __init__(self, api: Any, project_id: int, enforce_single_wip: bool = False):
        self.api = api
        self.project_id = project_id
        # optional WIP gate: when true, claim() refuses a new task while you already
        # have an active one. Off by default -> the gate does zero extra work.
        self.enforce_single_wip = enforce_single_wip
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
                    f"the project board has no columns {missing} — run `vikunja-mcp setup`"
                )
        return self._buckets_cache[title]

    # --- поиск и проверки ---
    def _board(self, require_titles: set[str] | None = None) -> list[dict]:
        # require_titles is forwarded to view_tasks: None (default) = full exhaustive board
        # (for _find_task/claim which must see every bucket incl. Done); next_task passes
        # NEXT_TASK_STAGES to skip exhaustively paging the unbounded Done (#43 latency fix).
        return self.api.view_tasks(
            self.project_id, self._view()["id"], require_titles=require_titles
        )

    def _my_active_tasks(self, board: list[dict] | None = None) -> list[tuple[str, dict]]:
        """(stage, task) for tasks in an ACTIVE stage (Design/Build) assigned to the
        caller — the 'one task at a time' set. Shared by next_task's resume branch and
        claim's optional WIP gate. Pass a pre-fetched board (the raw _board() list) to
        skip a second fetch; a stuck claim still sitting in Queue is deliberately NOT
        active (finishing it isn't starting a second task)."""
        raw = self._board() if board is None else board
        by_stage = {b["title"]: (b.get("tasks") or []) for b in raw}
        my_id = self._me()["id"]
        return [
            (stage, t)
            for stage in ACTIVE_STAGES
            for t in by_stage.get(stage, [])
            if my_id in self._assignee_ids(t)
        ]

    def _find_task(self, task_id: int, board: list[dict] | None = None) -> tuple[dict, str]:
        for bucket in (board if board is not None else self._board()):
            for task in bucket.get("tasks") or []:
                if task["id"] == task_id:
                    return task, bucket["title"]
        raise WorkflowError(f"task {task_id} not found on the board of project {self.project_id}")

    def _unfinished_predecessors(
        self, task_id: int, board: list[dict] | None = None
    ) -> list[dict]:
        """Predecessors of `task_id` that are NOT yet ready (still below Review) and so must
        reach Review/Done before this task may be started. A predecessor is any task linked from
        this one by a `follows` (this follows P) or `blocked` (this blocked-by P) relation;
        parenttask is deliberately excluded, so an old epic whose children carry only a parenttask
        link yields [] and stays claimable (the migration guard). Each entry: {id, ref, title,
        stage}, deduped by id. A task with no follows/blocked relation returns [] without arming
        the gate. Pass a pre-fetched full board (raw _board()) to reuse one snapshot for stages."""
        full = self._board() if board is None else board
        stage_by_id = {
            t["id"]: (t, bucket["title"])
            for bucket in full for t in (bucket.get("tasks") or [])
        }
        related = self.api.get_task(task_id).get("related_tasks") or {}
        unfinished: list[dict] = []
        seen: set[int] = set()
        for kind in PREDECESSOR_RELATION_KINDS:
            for pred in related.get(kind) or []:
                pid = pred["id"]
                if pid in seen:
                    continue
                seen.add(pid)
                found = stage_by_id.get(pid)
                if found is None or found[1] in READY_STAGES:
                    continue  # gone from the board, or already ready -> not a blocker
                pred_task, pred_stage = found
                unfinished.append({
                    "id": pid, "ref": self._ref(pred_task),
                    "title": pred_task["title"], "stage": pred_stage,
                })
        return unfinished

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
            raise WorkflowError(f"task {task['id']} is not assigned to you — claim it first")

    @staticmethod
    def _ref(task: dict) -> str:
        """Human-searchable task reference for agents to echo: the Vikunja identifier
        (project prefix + per-project index, e.g. 'VMCP-27') plus the global id in
        parens -> 'VMCP-27 (82)'. A human searches the tracker by the identifier; the
        bare global id (#82) is not searchable. Vikunja already returns `identifier` on
        every task read (a project with no prefix yields '#<index>', which we keep);
        falls back to '#<id>' only if it's absent."""
        ident = (task.get("identifier") or "").strip()
        return f"{ident} ({task['id']})" if ident else f"#{task['id']}"

    @staticmethod
    def _summary(task: dict) -> dict:
        return {
            "id": task["id"],
            "ref": Workflow._ref(task),
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": (task.get("description") or "")[:500],
        }

    # --- тулзы ---
    def next_task(self) -> dict:
        # light board: only the stages next_task reads need be complete — don't page the
        # unbounded Done exhaustively on every call (#43). _my_active_tasks(raw) reuses this
        # same fetch (Design/Build are in NEXT_TASK_STAGES, so they're complete).
        raw = self._board(require_titles=NEXT_TASK_STAGES)
        board = {b["title"]: (b.get("tasks") or []) for b in raw}
        my_id = self._me()["id"]

        mine = self._my_active_tasks(raw)
        if mine:
            mine.sort(key=lambda st: -st[1].get("priority", 0))
            stage, task = mine[0]
            return {
                "resume": True, "stage": stage, "task": self._summary(task),
                "note": (
                    "this is your active task — don't claim a new one. First reconcile "
                    "the actual state: read the dossier (get_task) and check the "
                    "code/repo — the work may already be done in full or in part. "
                    "Done — verify it and advance(to='review') with honest evidence; "
                    "not — continue from where it left off"
                ),
            }

        stuck = [t for t in board.get("Queue", []) if my_id in self._assignee_ids(t)]
        if stuck:
            stuck.sort(key=lambda t: -t.get("priority", 0))
            return {
                "resume": True, "stage": "Queue", "task": self._summary(stuck[0]),
                "note": (
                    "this task in Queue is assigned to you (by a human or an unfinished "
                    "claim) — call claim(task_id) to finish moving it into Design"
                ),
            }

        for t in sorted(board.get("Review", []), key=lambda t: -t.get("priority", 0)):
            if not self._has_label(t, LABEL_BUG) or my_id in self._assignee_ids(t):
                continue
            # вердикт актуален, только если он свежее последнего отчёта: после цикла
            # needs_work -> доработка -> Review задача должна снова попасть к ревьюеру
            comments = self.api.comments(t["id"])
            # comments are stored as HTML (#85); render back to plain text before matching
            # the leading marker, else "[review]" hides behind a "<p>" wrapper.
            last_review = max(
                (c.get("created") or "" for c in comments
                 if html_to_text(c.get("comment") or "").startswith("[review]")),
                default=None,
            )
            last_worklog = max(
                (c.get("created") or "" for c in comments
                 if html_to_text(c.get("comment") or "").startswith("[worklog]")),
                default="",
            )
            if last_review is not None and last_review >= last_worklog:
                continue
            return {
                "review": True, "task": self._summary(t),
                "note": (
                    "a bug fix is waiting for independent review: reproduce it, confirm "
                    "the fix closes the cause (not the symptom), run it and cast a "
                    "verdict via review_task(task_id, verdict=..., report=...). Do NOT "
                    "review it if you wrote this code in this session"
                ),
            }

        # Queue-контракт: свободные берём, назначенные на другого НЕ трогаем — это «для людей»
        queue = [
            t for t in board.get("Queue", [])
            if not self._assignee_ids(t) and not self._has_label(t, LABEL_BLOCKED)
        ]
        if queue:
            queue.sort(key=lambda t: -t.get("priority", 0))
            return {
                "resume": False, "task": self._summary(queue[0]),
                "note": (
                    "a free task from the queue — call claim(task_id) (it moves it into "
                    "Design), then dispatch a per-task agent for the whole task. "
                    "resume:false here means 'take a new one', not 'nothing to do' "
                    "(empty is only task:null). A human picked this task into Queue, so "
                    "taking it is your mandate, NOT unbidden initiative: don't defer it "
                    "and don't stop the /loop under the generic autonomous-loop default "
                    "'steward, not initiator: don't start fresh work without a go-ahead' "
                    "— it does not apply to draining the tracker queue"
                ),
            }
        return {"task": None, "message": "the queue is empty — no work for the agent"}

    def claim(self, task_id: int) -> dict:
        board = self._board()
        task, stage = self._find_task(task_id, board=board)
        if stage != "Queue":
            raise WorkflowError(f"task is in '{stage}', you can only claim from Queue")
        # hard sequence gate (option C, epic #94): refuse to START a successor while any of its
        # predecessors is unfinished (below Review). claim otherwise checks only stage==Queue, so
        # without this the gate is trivially bypassed by claiming a successor directly. Keys off
        # follows/blocked only (never parenttask) — old epics stay claimable. Reuses the snapshot.
        blockers = self._unfinished_predecessors(task_id, board=board)
        if blockers:
            joined = "; ".join(f"{b['ref']} in '{b['stage']}'" for b in blockers)
            raise WorkflowError(
                f"can't claim {self._ref(task)} yet — it's waiting on an unfinished "
                f"predecessor: {joined}. A predecessor becomes ready only at Review or Done; "
                f"finish that one first"
            )
        # optional single-WIP gate (opt-in via enforce_single_wip). Off -> no extra
        # board fetch, behavior unchanged. On -> refuse a new task while an active one
        # exists; the discipline answer is "finish it or return_task it first".
        if self.enforce_single_wip:
            active = self._my_active_tasks()
            if active:
                names = ", ".join(f"#{t['id']}" for _stage, t in active)
                raise WorkflowError(
                    f"you already have an active task ({names}) — finish it (advance to "
                    f"Review) or return_task it before claiming another (single-WIP limit "
                    f"is on: enforce_single_wip)"
                )
        existing = task.get("assignees") or []
        me = self._me()
        # self-heal: партиальный клейм (assign прошёл, move — нет) или человек руками
        # вернул заклеймленную задачу в Queue — я тут единственный assignee, долечиваем
        # вместо отказа. Кто-то ДРУГОЙ среди assignees (один или вместе со мной) — отказ как раньше.
        self_heal = len(existing) == 1 and existing[0].get("id") == me["id"]
        if existing and not self_heal:
            names = ", ".join(a.get("username", "?") for a in existing)
            raise WorkflowError(f"already taken ({names}) — grab the next one via next_task")

        if not self_heal:
            self.api.add_assignee(task_id, me["id"])
        fresh = self.api.get_task(task_id)
        fresh_ids = self._assignee_ids(fresh)
        others = [aid for aid in fresh_ids if aid != me["id"]]
        if others:
            self.api.remove_assignee(task_id, me["id"])
            raise WorkflowError("lost the race for this task — grab the next one via next_task")
        # vanish-window: человек мог снять моё назначение в окно между assign и re-read.
        # others пуст — но без меня в assignees move уведёт задачу в Design «ничьей»
        # (невидимо для next_task и незаклеймимо из Queue). Отказ до move закрывает окно
        # и в обычном, и в self-heal пути (там add_assignee не звался — окно то же).
        if me["id"] not in fresh_ids:
            raise WorkflowError(
                "the assignment vanished during the claim (a human removed it) — retry next_task"
            )

        view = self._view()
        self.api.move_task(self.project_id, view["id"], self._bucket("Design")["id"], task_id)
        self.api.add_comment(task_id, f"[claim] {me['username']} взял задачу в работу")
        return {
            "claimed": True, "task": self._summary(fresh),
            "next": "describe your approach and call advance(to='build', spec=...)",
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
            raise WorkflowError("only a human moves a task to Done after review — not you")
        if to not in AGENT_ADVANCE:
            raise WorkflowError(f"invalid transition '{to}'; available: build, review")
        from_stage, to_stage = AGENT_ADVANCE[to]

        task, stage = self._find_task(task_id)
        self._require_mine(task)
        if stage != from_stage:
            raise WorkflowError(
                f"moving to {to_stage} is only possible from {from_stage}; task is now in {stage}"
            )

        if to == "build":
            if not (spec or "").strip():
                raise WorkflowError("a spec is required: describe your approach before implementing")
            self.api.add_comment(task_id, f"[spec]\n{spec.strip()}")
        else:
            if not (worklog or "").strip() or not (evidence or "").strip():
                raise WorkflowError(
                    "Review needs a report: worklog (what was done and how it was "
                    "verified) and evidence (a link to the commit/PR or verification "
                    "output); for bug fixes also root_cause — the cause of the bug, "
                    "not the symptom"
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
        # push-нудж: багфикс требует независимого ревью — пер-таск-агент вернёт
        # review_needed оркестратору, тот задиспатчит ревьюера (author != reviewer)
        if to == "review" and self._has_label(task, LABEL_BUG):
            result["review_needed"] = True
            result["note"] = (
                "this is a bug — return the review_needed flag to the orchestrator in "
                "your result: it will dispatch an independent reviewer in the background "
                "(author ≠ reviewer)"
            )
        return result

    def review_task(self, task_id: int, verdict: str, report: str) -> dict:
        verdict = (verdict or "").strip().lower()
        if verdict not in ("approve", "needs_work"):
            raise WorkflowError("verdict must be 'approve' or 'needs_work'")
        if not (report or "").strip():
            raise WorkflowError(
                "report required: what you reproduced/verified by running and why this verdict"
            )
        task, stage = self._find_task(task_id)
        if stage != "Review":
            raise WorkflowError(f"only tasks in Review can be reviewed; this one is in {stage}")

        if verdict == "approve":
            self.api.add_comment(task_id, f"[review] APPROVE\n{report.strip()}")
            self._add_label(task_id, LABEL_REVIEWED)
            self._remove_label(task, LABEL_REVIEW_FAILED)
            return {
                "verdict": "approve", "task_id": task_id,
                "note": "verdict recorded; a human moves the task to Done",
            }
        self.api.add_comment(task_id, f"[review] NEEDS WORK\n{report.strip()}")
        self._add_label(task_id, LABEL_REVIEW_FAILED)
        self._remove_label(task, LABEL_REVIEWED)
        self._move(task_id, "Build")
        return {
            "verdict": "needs_work", "task_id": task_id, "moved_to": "Build",
            "note": "the task went back to the implementer — they'll see it in next_task",
        }

    def call_human(self, task_id: int, question: str) -> dict:
        if not (question or "").strip():
            raise WorkflowError(
                "state your question: what you need from the human and which options you weighed"
            )
        task, stage = self._find_task(task_id)
        self._require_mine(task)
        if stage not in ACTIVE_STAGES:
            raise WorkflowError(f"call_human works only from Design/Build; task is in {stage}")
        self.api.add_comment(task_id, f"[нужен человек] {question.strip()}")
        self._move(task_id, "Your Call")
        return {
            "moved_to": "Your Call", "task_id": task_id,
            "note": "assignee kept; the human replies and moves the task back to Design/Build",
        }

    def return_task(self, task_id: int, reason: str) -> dict:
        if not (reason or "").strip():
            raise WorkflowError("give the reason for the block — it'll be posted as a comment")
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
            raise WorkflowError("decomposition means at least 2 subtasks")
        if any(not (st.get("title") or "").strip() for st in subtasks):
            raise WorkflowError("every subtask must have a title")
        task, _stage = self._find_task(task_id)
        self._require_mine(task)

        created: list[dict] = []
        try:
            for st in subtasks:
                child = self.api.create_task(
                    self.project_id, st["title"].strip(),
                    description=st.get("description", ""), priority=int(st.get("priority", 0)),
                )
                # record the child the instant it exists on the board — BEFORE add_relation
                # /_move — so a failure anywhere below still reports it. This is the retry-
                # duplication boundary: once create_task returned, a naive re-run doubles it.
                created.append({"id": child["id"], "title": child["title"]})
                self.api.add_relation(child["id"], task_id, "parenttask")
                self._move(child["id"], "Queue")
        except (VikunjaError, httpx.HTTPError) as exc:
            if not created:
                raise  # nothing landed on the board yet — the bare error is safe to retry
            listing = ", ".join(f"#{c['id']} {c['title']}" for c in created)
            raise WorkflowError(
                f"decompose failed after creating {len(created)} of {len(subtasks)} "
                f"subtask(s) ({exc}). Already on the board: {listing}. Do NOT blindly "
                f"retry — you would duplicate these; delete them first, or re-run "
                f"decompose for the remaining subtasks only."
            ) from exc

        listing = ", ".join(f"#{c['id']} {c['title']}" for c in created)
        self.api.add_comment(task_id, f"[decompose] создано: {listing}")
        label = self.api.get_or_create_label(LABEL_EPIC)
        self.api.add_label(task_id, label["id"])
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        return {"created": created, "parent": {"id": task_id, "moved_to": "Backlog", "labeled": LABEL_EPIC}}

    def file_task(
        self, title: str, description: str = "", priority: int = 0,
        related_task_id: int | None = None,
    ) -> dict:
        """File a finding (a bug/tech-debt OUTSIDE the current task) into Backlog for
        human triage — NOT into Queue (a human prioritizes). Optionally: a 'related'
        relation to the task it was found during. No ownership required — this is a new
        card, not an edit of your task (unlike decompose)."""
        if not (title or "").strip():
            raise WorkflowError("a non-empty title is required for the new task")
        created = self.api.create_task(
            self.project_id, title.strip(),
            description=(description or "").strip(), priority=int(priority or 0),
        )
        new_id = created["id"]
        # явно в Backlog: не полагаемся на то, что default-бакет проекта == Backlog
        self._move(new_id, "Backlog")
        if related_task_id is not None:
            self.api.add_relation(new_id, related_task_id, "related")
        marker = "[filed-by-agent] заведено агентом для триажа человеком"
        if related_task_id is not None:
            marker += f" (по ходу работы над #{related_task_id})"
        self.api.add_comment(new_id, marker)
        result = {
            "filed": {"id": new_id, "title": created["title"], "stage": "Backlog"},
            "note": "in Backlog for human triage (not Queue — a human prioritizes)",
        }
        if related_task_id is not None:
            result["related_to"] = related_task_id
        return result

    def comment(self, task_id: int, text: str) -> dict:
        if not (text or "").strip():
            raise WorkflowError("an empty comment is not needed")
        self._find_task(task_id)
        self.api.add_comment(task_id, text.strip())
        return {"commented": task_id}

    def get_task(self, task_id: int) -> dict:
        """Full dossier: unlike _summary (next_task/claim), the description is NOT
        truncated and related is added — a compact dict {relation_kind: [{"id", "title"}, ...]}."""
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
            "ref": self._ref(task),
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": task.get("description") or "",
            "stage": stage,
            "assignees": [a.get("username", "?") for a in task.get("assignees") or []],
            "labels": [lb.get("title") for lb in task.get("labels") or []],
            "related": related,
            # comments are stored as HTML (#85); render back to plain text so the agent
            # reads clean multiline text (the human reads the formatted HTML in the UI).
            "comments": [
                {"author": c.get("author", {}).get("username", "?"),
                 "text": html_to_text(c.get("comment", ""))}
                for c in raw_comments
            ],
        }
