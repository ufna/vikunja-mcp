"""Stages and gates of the agent flow. The rules are baked in here, not in prompts."""
import mimetypes
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
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
LABEL_EPIC_READY = "epic-ready"        # маркер: все дети эпика в Review/Done — контейнер собран, ждёт Done человека
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

# --- вложения: временные файлы (download_attachment, #139) ---
# Скачанные вложения кладём в один выделенный temp-каталог, КАЖДОЕ скачивание — в свой
# mkdtemp-подкаталог, чтобы файл сохранял ТОЧНОЕ исходное имя (рендерер образов у агента
# ключуется на расширении .png/.jpg), и два файла с одним именем из разных задач не затирали
# друг друга. Никто не удаляет файл сразу после записи — агент читает его Read-ом секундами
# позже, — поэтому чистка это best-effort TTL-подметание на КАЖДОМ вызове: подкаталоги старше
# _ATTACHMENT_TTL сносятся (только что записанный всегда свежий, под нож не попадёт). Так течь
# ограничена ~одним TTL скачиваний БЕЗ фонового потока и БЕЗ atexit (который на долгоживущем
# stdio-сервере не срабатывает до его остановки). Размер режем ДО скачивания по метаданным.
_ATTACHMENT_ROOT = os.path.join(tempfile.gettempdir(), "vikunja-mcp-attachments")
_ATTACHMENT_TTL = 3600  # сек: подкаталоги скачиваний старше этого best-effort сносятся
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 МБ: щедро для скринов/доков, отсекает рантаймы


def _sweep_old_attachments(now: float) -> None:
    """Best-effort: снести подкаталоги скачиваний старше _ATTACHMENT_TTL. Полностью
    защищено — чистка временных файлов не имеет права уронить вызов тулзы."""
    try:
        entries = os.listdir(_ATTACHMENT_ROOT)
    except OSError:
        return
    for entry in entries:
        path = os.path.join(_ATTACHMENT_ROOT, entry)
        try:
            if now - os.path.getmtime(path) > _ATTACHMENT_TTL:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _safe_attachment_name(name: str, fallback: str) -> str:
    """Имя файла от сервера НЕ должно уводить запись за пределы temp-каталога (path
    traversal). Оставляем только basename (нормализовав и обратные слэши — на POSIX
    os.path.basename их не режет); пустое или всё из точек ('', '.', '..') -> fallback."""
    base = os.path.basename((name or "").replace("\\", "/").strip().rstrip("/"))
    if not base or set(base) <= {"."}:
        return fallback
    return base


def _write_attachment_to_temp(name: str, data: bytes, fallback: str) -> str:
    """Записать байты вложения во СВЕЖИЙ per-download подкаталог под _ATTACHMENT_ROOT,
    сохранив исходное имя, и вернуть путь. Попутно best-effort подметает старые скачивания."""
    os.makedirs(_ATTACHMENT_ROOT, exist_ok=True)
    _sweep_old_attachments(time.time())
    dest_dir = tempfile.mkdtemp(dir=_ATTACHMENT_ROOT)
    path = os.path.join(dest_dir, _safe_attachment_name(name, fallback))
    with open(path, "wb") as fh:
        fh.write(data)
    return path


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
        self, task_id: int, board: list[dict] | None = None,
        resolve_full: Callable[[], list[dict]] | None = None,
    ) -> list[dict]:
        """Predecessors of `task_id` that are NOT yet ready (still below Review) and so must
        reach Review/Done before this task may be started. A predecessor is any task linked from
        this one by a `follows` (this follows P) or `blocked` (this blocked-by P) relation;
        parenttask is deliberately excluded, so an old epic whose children carry only a parenttask
        link yields [] and stays claimable (the migration guard). Each entry: {id, ref, title,
        stage}, deduped by id. A task with no follows/blocked relation returns [] without arming
        the gate. Pass a pre-fetched board (raw _board()) to reuse one snapshot for stages.

        resolve_full (#126): a memoised getter for the EXHAUSTIVE board, supplied by next_task,
        which resolves stages against its LIGHT board (require_titles=NEXT_TASK_STAGES — Backlog/
        Your Call/Done are not exhaustively paged, #43). On that light board a predecessor that is
        simply absent is NOT provably deleted: it may sit in an unpaged Backlog/Your Call/Done
        bucket. So before ruling "gone -> not a blocker" we consult resolve_full() — the same full
        board claim/advance read — and treat the predecessor as gone only if it is missing there
        too. resolve_full is memoised by the caller, so the full board is fetched AT MOST ONCE per
        next_task (a 1->2 view_tasks escalation, and only when a predecessor is genuinely off the
        light board — never per candidate); the common no-off-board-predecessor path never calls
        it, preserving the #43/#105 single fetch. claim/advance pass the full board and OMIT
        resolve_full, so their verdict is unchanged — this makes next_task agree with them by
        construction instead of by keeping three bucket-sets in sync by hand."""
        base = self._board() if board is None else board
        stage_by_id = {
            t["id"]: (t, bucket["title"])
            for bucket in base for t in (bucket.get("tasks") or [])
        }
        full_stage_by_id: dict[int, tuple[dict, str]] | None = None
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
                if found is None and resolve_full is not None:
                    # light-board absence is NOT deletion — disambiguate against the exhaustive
                    # board (fetched at most once via the memoised resolve_full) before ruling gone
                    if full_stage_by_id is None:
                        full_stage_by_id = {
                            t["id"]: (t, bucket["title"])
                            for bucket in resolve_full() for t in (bucket.get("tasks") or [])
                        }
                    found = full_stage_by_id.get(pid)
                if found is None or found[1] in READY_STAGES:
                    continue  # genuinely gone (absent even from the full board) or already ready
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

    def _clear_verdict_labels(self, task: dict) -> None:
        """Снять ОБЕ взаимоисключающие вердикт-метки (`reviewed` / `review-failed`). Задача,
        (пере)входящая в активный пайплайн — агент начинает (пере)сборку или ресабмитит в
        Review, — НЕ несёт действующего вердикта: любой прошлый инвалидируется в момент
        возобновления работы. #119: когда человек РУКАМИ вытаскивает одобренную карточку из
        Review на доработку, ни одна тулза не срабатывает, поэтому `reviewed` переживает
        возврат; снятие здесь, на следующем forward-переходе агента, не даёт несвежему APPROVE
        уехать обратно в свежий Review (ложь на доске). Оффер ревью в next_task при этом
        цепляется за свежесть коммента [worklog]/[review], а НЕ за эту метку, так что стале-
        `reviewed` не подавлял бы re-ревью — но ложный бейдж всё равно не должен оставаться.
        Идемпотентно по каждой метке — _remove_label шлёт DELETE только по реально висящей на
        снапшоте связи, поэтому на задаче без вердикт-меток (свежий клейм) это no-op."""
        self._remove_label(task, LABEL_REVIEW_FAILED)
        self._remove_label(task, LABEL_REVIEWED)

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
            # rework-first ordering (option C, epic #94, mechanism 3): when I hold TWO+ active
            # tasks from one chain, hand back the one that is a PREDECESSOR of another of my
            # active tasks BEFORE its successor — even when the successor outranks it by priority
            # — so I finish the unblocking rework, not the shinier successor (whose advance→review
            # is latched anyway, mechanism 2). Both tasks being active ⇒ both below Review ⇒ the
            # predecessor surfaces in _unfinished_predecessors; keys off follows/blocked only,
            # never parenttask. Computed only for 2+ active tasks — the common 0/1-active path
            # keeps a plain -priority sort and makes zero extra get_task calls.
            rework_first: set[int] = set()
            if len(mine) > 1:
                active_ids = {t["id"] for _s, t in mine}
                for _s, t in mine:
                    for pred in self._unfinished_predecessors(t["id"], board=raw):
                        if pred["id"] in active_ids:
                            rework_first.add(pred["id"])
            mine.sort(key=lambda st: (
                0 if st[1]["id"] in rework_first else 1, -st[1].get("priority", 0)
            ))
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

        # skip an epic here too: an epic container assigned to me in Queue (only ever a human's
        # doing — decompose parks epics in Backlog with the assignee cleared) is NOT claimable
        # (claim refuses epics below), and this stuck branch outranks the free queue, so handing
        # it back as a "call claim to finish" instruction would LIVELOCK the pump on an
        # unclaimable card and starve real work. Keys off the epic LABEL, never subtask structure;
        # this is not a false-skip of "really my active work" — an epic container is never one.
        stuck = [
            t for t in board.get("Queue", [])
            if my_id in self._assignee_ids(t) and not self._has_label(t, LABEL_EPIC)
        ]
        if stuck:
            stuck.sort(key=lambda t: -t.get("priority", 0))
            return {
                "resume": True, "stage": "Queue", "task": self._summary(stuck[0]),
                "note": (
                    "this task in Queue is assigned to you (by a human or an unfinished "
                    "claim) — call claim(task_id) to finish moving it into Design"
                ),
            }

        # independent-review pull path (#117): offer ANY task in Review awaiting review —
        # not just bug fixes — EXCEPT an epic container (label epic), whose code lives in its
        # children (each reviewed on its own advance), so there is nothing to review here. The
        # epic skip keys off the LABEL, never the presence of subtasks (same migration-guard
        # principle as the sequence gate). Two guards keep the pump safe: skip a task assigned
        # to the caller (never review your own work) and skip one whose verdict is fresher than
        # its last report (else an already-reviewed card is handed back forever and the queue
        # never advances — the freshness check just below).
        for t in sorted(board.get("Review", []), key=lambda t: -t.get("priority", 0)):
            if self._has_label(t, LABEL_EPIC) or my_id in self._assignee_ids(t):
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
            # nothing to review until a work report exists: advance→review always posts a
            # [worklog], so a Review card WITHOUT one was placed there by hand — not a review
            # candidate. This also keeps the sequence gate's bare "predecessor ready at Review"
            # tasks (and any hand-parked card) out of the widened #117 net.
            if not last_worklog:
                continue
            if last_review is not None and last_review >= last_worklog:
                continue
            review_kind = "bug" if self._has_label(t, LABEL_BUG) else "change"
            return {
                "review": True, "review_kind": review_kind, "task": self._summary(t),
                "note": (
                    "this task is waiting for independent review — run it and cast a verdict "
                    "via review_task(task_id, verdict=..., report=...). review_kind='bug': "
                    "reproduce it and confirm the fix closes the CAUSE (not the symptom); "
                    "review_kind='change' (feat/chore/docs/refactor): confirm it does what "
                    "the spec/description said, the tests are real, it stayed in its slice, "
                    "and look for obvious regressions nearby. Do NOT review it if you wrote "
                    "this code in this session"
                ),
            }

        # #126: exhaustive-board escalation for the sequence gate, memoised to AT MOST ONE fetch
        # per next_task. The board above is LIGHT (NEXT_TASK_STAGES omits Backlog/Your Call/Done,
        # #43), so a predecessor absent from it is not provably gone — it may sit in an unpaged
        # bucket. resolve_full lets _unfinished_predecessors consult the full board (the same one
        # claim/advance read) before ruling "not a blocker", so next_task's verdict matches claim's
        # BY CONSTRUCTION, not by keeping bucket-sets in sync by hand. Fetched lazily: when every
        # predecessor is already on the light board (the common case — a ready head sits at Review,
        # which IS in NEXT_TASK_STAGES) it is never called, so next_task still issues exactly one
        # view_tasks (the #43 latency win and the #105 single-fetch measurement both hold).
        full_board: dict[str, list[dict]] = {}

        def resolve_full() -> list[dict]:
            if "board" not in full_board:
                full_board["board"] = self._board()  # exhaustive: all buckets, incl Backlog/YC/Done
            return full_board["board"]

        # Queue-контракт: свободные берём, назначенные на другого НЕ трогаем — это «для людей».
        # epic-контейнер тоже пропускаем (по аналогии с blocked): родитель с меткой epic и живыми
        # детьми — это контейнер, а не работа, клеймить его бессмысленно (ровно баг из #94, где
        # next_task предложил epic-родителя как свободную задачу Queue). Скип цепляется за метку
        # epic, НИКОГДА за наличие подзадач (тот же миграционный принцип, что у гейта
        # последовательности): у обычной задачи тоже может быть подзадача, и она обязана остаться
        # клеймабельной.
        queue = [
            t for t in board.get("Queue", [])
            if not self._assignee_ids(t)
            and not self._has_label(t, LABEL_BLOCKED)
            and not self._has_label(t, LABEL_EPIC)
        ]
        queue.sort(key=lambda t: -t.get("priority", 0))
        # hard sequence gate (option C, epic #94) — free-queue half: a free task whose
        # predecessor is still unfinished (below Review) is NOT yet claimable; skip it and offer
        # the next one. Keys off follows/blocked only (never parenttask), so an old unordered
        # epic's child stays offered (migration guard, C1). Reuse the ONE board snapshot (raw)
        # already fetched above — never refetch it per candidate (the board fetch isn't cheap).
        # A head returned to Backlog sits on the light board's page-1, so it's seen here; claim's
        # full-board gate backstops the rare Backlog-beyond-page-1 case (never a silent pass).
        gated: list[tuple[dict, list[dict]]] = []
        for t in queue:
            blockers = self._unfinished_predecessors(t["id"], board=raw, resolve_full=resolve_full)
            if not blockers:
                return {
                    "resume": False, "task": self._summary(t),
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
            gated.append((t, blockers))
        # Queue non-empty but EVERY free candidate gated -> starving tail. This MUST be
        # distinguishable from the empty queue below (the pump idles on task:null), else a
        # stalled chain sleeps forever unseen.
        if gated:
            # cycle safety valve (option C, epic #94, C5/#105): before reporting a generic
            # starving tail, DFS the unfinished-predecessor edges from these gated candidates.
            # A back-edge = a predecessor CYCLE (only ever hand-created in the web UI: A follows
            # B, B follows A) in which nothing is claimable AND which can't self-unblock, so it
            # earns its own distinct signal instead of masquerading as an ordinary stalled tail.
            # Reuse the ONE board snapshot (raw); the walk is bounded and provably terminating
            # (see _find_predecessor_cycle). A cycle anywhere on the board can NOT suppress a
            # genuinely claimable free task — the loop above already RETURNED it before here.
            cycle = self._find_predecessor_cycle(gated, raw, resolve_full=resolve_full)
            if cycle is not None:
                return self._cycle_signal(cycle, full_board.get("board", raw))
            return self._starving_tail(gated)
        return {"task": None, "message": "the queue is empty — no work for the agent"}

    def _starving_tail(self, gated: list[tuple[dict, list[dict]]]) -> dict:
        """The distinguishable "everything is blocked" signal — NOT the empty queue.

        Returned only when the free Queue is NON-empty yet EVERY candidate is gated by an
        unfinished predecessor. It must NOT look like the empty-queue result ({task:None +
        "the queue is empty"}): the pump's /loop treats a bare empty queue as "ScheduleWakeup
        and idle", so a starved tail reported as empty would sleep forever and nobody would
        learn the chain stalled. `task` stays None (nothing to claim), but the additive
        discriminators — starving/waiting_count/needs_retriage — let a caller BRANCH, and
        `waiting` names each blocked task with the predecessor holding it. Special case: a
        predecessor sitting in Backlog is a chain HEAD sent back by return_task (label blocked,
        assignee cleared); its whole tail stalls until a human re-triages it — flagged
        needs_retriage and spelled out in the message, never left a mystery. (A predecessor
        CYCLE among these same gated candidates is caught earlier, by _find_predecessor_cycle
        (C5/#105), which returns its own distinct signal — so reaching here means the gate is
        acyclic: an honest starving tail, not a loop.)"""
        waiting = [
            {
                "task": self._summary(task),
                "blocked_by": blockers,
                "needs_retriage": any(b["stage"] == "Backlog" for b in blockers),
            }
            for task, blockers in gated
        ]
        retriage = [w for w in waiting if w["needs_retriage"]]
        lines = [
            f"{w['task']['ref']} ← "
            + "; ".join(
                f"{b['ref']} in '{b['stage']}'"
                + (" [sent back to Backlog via return_task — needs human re-triage]"
                   if b["stage"] == "Backlog" else "")
                for b in w["blocked_by"]
            )
            for w in waiting
        ]
        message = (
            f"{len(waiting)} queued task(s) can't be claimed — each waits on an unfinished "
            f"predecessor (a predecessor is 'ready' only at Review or Done). This is NOT an "
            f"empty queue. Waiting: " + " | ".join(lines)
        )
        if retriage:
            message += (
                f". {len(retriage)} of these are stalled behind a chain HEAD returned to "
                f"Backlog (return_task) — a human must re-triage the head before the tail "
                f"can resume."
            )
        return {
            "task": None,
            "starving": True,
            "waiting_count": len(waiting),
            "needs_retriage": bool(retriage),
            "waiting": waiting,
            "message": message,
            "note": (
                "NOT an empty queue: the free Queue is non-empty but every task is gated by an "
                "unfinished predecessor, so nothing is claimable right now. Do NOT treat this "
                "as 'nothing to do' — surface it so a human sees the stalled chain, then "
                "ScheduleWakeup and re-check later. When needs_retriage is set, a chain head "
                "was returned to Backlog and a human must re-triage it before the tail resumes."
            ),
        }

    def _find_predecessor_cycle(
        self, gated: list[tuple[dict, list[dict]]], board: list[dict],
        resolve_full: Callable[[], list[dict]] | None = None,
    ) -> list[int] | None:
        """DFS over UNFINISHED-predecessor edges from the gated Queue candidates; return the ids
        on the first cycle found (a back-edge into the current path), else None. A cycle can only
        be introduced by a human hand-editing follows/blocked relations in the web UI (an ordered
        decompose builds a linear, acyclic chain), and when it happens every task in the loop has
        an unfinished predecessor, so nothing is claimable — otherwise indistinguishable from a
        plain starving tail. This runs inside next_task, the pump's own tool, on every idle tick,
        so it MUST terminate and MUST NOT hang: the walk is ITERATIVE (no recursion limit) and
        each node enters the path at most once (guarded by `visited`/`on_path`), so it is bounded
        by the reachable unfinished subgraph. A malformed self-referential relation (A follows A)
        surfaces the node as its own predecessor and is reported as a 1-cycle, never an infinite
        loop. `visited` and `on_path` are SEPARATE sets — a node re-reached off the current path
        (a diamond/converging DAG) is pruned, NOT mistaken for a cycle (the false-positive guard).
        Bounded to unfinished (below-Review) predecessors — the exact edges the gate reads, never
        the whole board. The blockers next_task already computed for the roots seed the edge
        cache, so their get_task calls aren't repeated; deeper nodes are fetched lazily and
        memoized (each expanded at most once). Reuses the ONE board snapshot passed in."""
        preds_cache: dict[int, list[int]] = {
            t["id"]: [b["id"] for b in blockers] for t, blockers in gated
        }

        def preds(tid: int) -> list[int]:
            if tid not in preds_cache:
                preds_cache[tid] = [
                    p["id"] for p in self._unfinished_predecessors(
                        tid, board=board, resolve_full=resolve_full
                    )
                ]
            return preds_cache[tid]

        visited: set[int] = set()  # fully explored, proven not to reach a cycle -> never re-walked
        for root, _blockers in gated:
            if root["id"] in visited:
                continue
            path: list[int] = []       # the CURRENT dfs path, in order
            on_path: set[int] = set()  # its membership -> a hit here is a back-edge (a cycle)
            # explicit stack of (node, iterator-over-its-unfinished-predecessors)
            stack: list[tuple[int, Any]] = [(root["id"], iter(preds(root["id"])))]
            path.append(root["id"])
            on_path.add(root["id"])
            while stack:
                node, it = stack[-1]
                descended = False
                for child in it:
                    if child in on_path:
                        return path[path.index(child):]  # back-edge -> the loop is this slice
                    if child in visited:
                        continue  # already proven cycle-free -> prune, do NOT flag (diamond guard)
                    stack.append((child, iter(preds(child))))
                    path.append(child)
                    on_path.add(child)
                    descended = True
                    break
                if not descended:  # node's predecessors exhausted with no back-edge -> finish it
                    stack.pop()
                    path.pop()
                    on_path.discard(node)
                    visited.add(node)
        return None

    def _cycle_signal(self, cycle_ids: list[int], board: list[dict]) -> dict:
        """The distinguishable "a predecessor CYCLE makes everything unclaimable" signal — a THIRD
        state beside the empty queue and the plain starving tail. A cycle (A follows B, B follows
        A — only ever hand-created in the web UI) can't self-unblock: every task in it waits on
        another, so unlike a starving tail (which clears once a head reaches Review) ONLY a human
        can break it, by removing one follows/blocked link. `task` stays None (nothing to claim);
        `cycle`/`cycle_tasks` are the additive discriminators; the message and note NAME the
        looping tasks and tell the caller to surface it to a human, NOT to read it as 'nothing to
        do' and just sleep. Reuses the passed board snapshot to resolve each id to ref/title/stage
        (a member gone from the board falls back to '#<id>', never crashing)."""
        task_by_id = {
            t["id"]: (t, bucket["title"])
            for bucket in board for t in (bucket.get("tasks") or [])
        }
        nodes: list[dict] = []
        for tid in cycle_ids:
            found = task_by_id.get(tid)
            if found is None:
                nodes.append({"id": tid, "ref": f"#{tid}", "title": "?", "stage": "?"})
            else:
                task, stage = found
                nodes.append(
                    {"id": tid, "ref": self._ref(task), "title": task["title"], "stage": stage}
                )
        # render the loop CLOSED (A → B → A) so a 2-cycle and a self-loop both read unambiguously
        loop = " → ".join([n["ref"] for n in nodes] + [nodes[0]["ref"]])
        detail = "; ".join(f"{n['ref']} in '{n['stage']}'" for n in nodes)
        message = (
            f"ЦИКЛ предшественников — {loop}: {len(nodes)} задач(и) взаимно ждут друг друга "
            f"(follows/blocked-связи образуют петлю), поэтому НИЧЕГО в цикле не клеймабельно и "
            f"цепочка НЕ разблокируется сама. Это НЕ пустая очередь и НЕ обычное голодание "
            f"хвоста: разорвать цикл может только человек, убрав одну follows/blocked-связь в "
            f"вебе. Задачи в цикле: {detail}"
        )
        return {
            "task": None,
            "cycle": True,
            "cycle_tasks": nodes,
            "message": message,
            "note": (
                "a predecessor CYCLE (hand-edited follows/blocked relations form a loop) makes "
                "every task in it unclaimable and it can NOT self-unblock — distinct from a plain "
                "starving tail. Do NOT treat this as 'nothing to do' and just ScheduleWakeup: "
                "surface it to a human (call_human) to break the cycle by removing one "
                "follows/blocked link in the web UI. Nothing in the loop moves until they do."
            ),
        }

    def claim(self, task_id: int) -> dict:
        board = self._board()
        task, stage = self._find_task(task_id, board=board)
        if stage != "Queue":
            raise WorkflowError(f"task is in '{stage}', you can only claim from Queue")
        # epic containers are not claimable (epic #94 / #118): a card labelled epic is a
        # CONTAINER, not a unit of work — its evidence lives in its children, each claimed and
        # reviewed on its own. Refuse it here (next_task already skips it, but claim must gate too:
        # it otherwise checks only stage==Queue and would take an epic handed in directly), and
        # point the agent at the children. Keys off the epic LABEL, never the presence of subtasks
        # — an ordinary task may have subtasks and MUST stay claimable (the migration guard, same
        # principle as the sequence gate).
        if self._has_label(task, LABEL_EPIC):
            related = self.api.get_task(task_id).get("related_tasks") or {}
            subtasks = related.get("subtask") or []
            kids = ", ".join(self._ref(s) for s in subtasks) or "его подзадачами"
            raise WorkflowError(
                f"{self._ref(task)} is an epic CONTAINER (label epic), not a unit of work — "
                f"there is nothing to claim on the container itself. Its code/evidence lives in "
                f"its children, each claimed and reviewed on its own; work on those instead: "
                f"{kids}"
            )
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

    def _mark_epic_if_children_complete(self, child: dict, board: list[dict]) -> None:
        """Best-effort epic-complete marker (#118 Part 2). When THIS child's advance→review makes
        EVERY child of an epic parent ready (Review or Done — READY_STAGES, the same readiness the
        sequence gate uses; NOT a second definition), leave a VISIBLE marker on the EPIC so the
        human sees the container is assembled and can close the set: the LABEL_EPIC_READY label
        (at-a-glance on the board) plus an explanatory comment. It does NOT move the epic — agents
        can't and mustn't (Part 1 made epics unclaimable; only a human moves anything to Done). This
        is deliberately the ADDITIVE form of the cross-task write #103 rejected in its STRUCTURAL
        form: it reaches out of the child's transition to touch a DIFFERENT card, but adds only a
        label + comment — no stage move, no lost work, no gate effect. It MUST therefore be called
        strictly best-effort (the caller swallows every exception): a cosmetic marker on someone
        else's card must never strand the child's own advance, and it adds nothing to the child's
        result. Idempotent — skips if the epic already carries LABEL_EPIC_READY, so a bounced-and-
        re-advanced child never double-marks. Keys off the epic LABEL and the parenttask relation,
        never structure alone. `board` is the full snapshot advance already fetched; the current
        child moved to Review AFTER it was taken, so the child is scored as Review explicitly while
        every other sibling is read from the snapshot."""
        child_id = child["id"]
        related = self.api.get_task(child_id).get("related_tasks") or {}
        parents = related.get("parenttask") or []
        if not parents:
            return  # not a subtask of anything — nothing to mark
        stage_by_id = {
            t["id"]: bucket["title"]
            for bucket in board for t in (bucket.get("tasks") or [])
        }
        for parent in parents:
            # `parent` here is a related_tasks SUB-DICT, and the real server HOLLOWS those — labels/
            # assignees/nested related_tasks come back as None even when the task carries them (only
            # scalars survive; verified on real 2.3.0, #118 rework). So its labels can NOT be read
            # here — doing so silently no-op'd the marker in production while the too-generous fake
            # stayed green (#125). Re-fetch the FULL parent and read labels (both epic and the
            # idempotency marker) off IT. This is the same get_task the sibling read already needs,
            # so it is ZERO extra calls in the epic case (one hoisted, not added); for a non-epic
            # parent it costs +1 get_task, which is fine (best-effort, off next_task's hot path).
            full_parent = self.api.get_task(parent["id"])
            if not self._has_label(full_parent, LABEL_EPIC):
                continue  # parent isn't an epic container — not ours to mark
            if self._has_label(full_parent, LABEL_EPIC_READY):
                continue  # already marked — idempotent (a bounced+re-advanced child won't re-fire)
            siblings = (full_parent.get("related_tasks") or {}).get("subtask") or []
            if not siblings:
                continue
            all_ready = all(
                ("Review" if s["id"] == child_id else stage_by_id.get(s["id"])) in READY_STAGES
                for s in siblings
            )
            if not all_ready:
                continue
            # label FIRST (the idempotency key AND the board marker), THEN the comment: a partial
            # failure (label lands, comment doesn't) still leaves the epic consistently "marked", so
            # a later advance won't double-fire.
            self._add_label(parent["id"], LABEL_EPIC_READY)
            self.api.add_comment(
                parent["id"],
                f"[эпик собран] все {len(siblings)} дет(и) эпика достигли Review-или-Done — "
                f"контейнер собран и готов к твоему Done (в Done двигает только человек). Если "
                f"позже отобьёшь ребёнка из Review — увидишь его в Build и придержишь закрытие."
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

        board = self._board()
        task, stage = self._find_task(task_id, board=board)
        self._require_mine(task)
        if stage != from_stage:
            raise WorkflowError(
                f"moving to {to_stage} is only possible from {from_stage}; task is now in {stage}"
            )

        if to == "build":
            if not (spec or "").strip():
                raise WorkflowError("a spec is required: describe your approach before implementing")
            self.api.add_comment(task_id, f"[spec]\n{spec.strip()}")
            # (пере)сборка тоже инвалидирует любой прошлый вердикт: человек мог руками
            # вернуть одобренную/отбитую карточку сюда (#119). На свежем клейме меток нет —
            # это no-op; needs_work-цикл идёт через Build (не Design), сюда не заходит.
            self._clear_verdict_labels(task)
        else:
            # hard sequence gate (option C, epic #94, mechanism 2): the advance→review LATCH on
            # an in-flight successor — the case the human asked about. Refuse to land THIS task in
            # Review while any of its predecessors is below Review: a predecessor P that had
            # reached Review (so this successor got claimed) but was then bounced Review→Build
            # must be reworked back to Review before this one may advance. Applies ONLY to
            # to='review' (to='build' and every other transition are untouched); keys off
            # follows/blocked only, never parenttask (migration guard); reuses the full board
            # already fetched (must be full, not light — a predecessor may sit in Your Call/Done).
            # Known residual gap accepted by design: if THIS task was ALREADY in Review when P
            # bounced, the latch doesn't apply retroactively — the human-only Done move backstops.
            blockers = self._unfinished_predecessors(task_id, board=board)
            if blockers:
                joined = "; ".join(f"{b['ref']} in '{b['stage']}'" for b in blockers)
                raise WorkflowError(
                    f"can't move {self._ref(task)} to Review yet — its predecessor is being "
                    f"reworked below Review: {joined}. Finish that predecessor's rework and get "
                    f"it back to Review first, then advance this one (a predecessor is 'ready' "
                    f"only at Review or Done)."
                )
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
            # resubmit-reset: ресабмит инвалидирует ЛЮБОЙ прошлый вердикт — снимаем ОБЕ
            # вердикт-метки, и review-failed, и reviewed (#119: человек мог руками вытащить
            # одобренную карточку из Review на доработку — reviewed не должен уехать на новое
            # ревью). No-op на первом сабмите (меток ещё нет).
            self._clear_verdict_labels(task)
        self._move(task_id, to_stage)
        result = {"moved_to": to_stage, "task_id": task_id}
        if to == "review":
            # best-effort epic-complete marker (#118 Part 2): if THIS child was the LAST of an epic
            # parent to reach Review-or-Done, mark the epic (label + comment) so the human sees it's
            # ready to close. It writes to a DIFFERENT card, so it is wrapped so NOTHING it does can
            # fail the child's advance or change this result's shape (it adds no keys) — see the
            # helper's docstring. Any exception (epic lookup, comment, or label) is swallowed after a
            # one-line stderr note (#134).
            try:
                self._mark_epic_if_children_complete(task, board)
            except Exception as exc:
                # strictly best-effort — a marker on another card never fails the child's advance, so
                # the exception is still swallowed; but NO LONGER silently (#134). A bare
                # `except Exception: pass` hid a marker broken by a refactor: `except Exception`
                # catches TypeError/AttributeError (programmer errors), not just network blips, and
                # the marker IS the human's visibility mechanism for an assembled epic, so a
                # silently-dead indicator is worse than none. Leave one line on STDERR only (never
                # stdout — a stray byte corrupts the MCP stdio protocol), naming the advancing child
                # and the exception class so the failure is actionable (the epic's own id isn't
                # reliably known here — the helper can raise before resolving a parent — and the
                # helper is out of this card's slice; the child is one get_task from the epic). Same
                # best-effort-with-a-stderr-trace contract as sync_installed_artifacts (#88).
                #
                # #135: the LOG path must be as guarded as the marker it reports on. `{exc}`
                # calls str(exc) INSIDE this handler, so an exception whose __str__ itself
                # raises would escape advance(). By now the child has ALREADY reached Review
                # and written its [worklog], so a leaked exception makes advance raise for work
                # that genuinely succeeded — a state/report divergence, worse than a lost log.
                # So format the always-safe parts (exception CLASS + child id) unconditionally,
                # fall back to "<unprintable>" when str(exc) blows up so the diagnostic survives
                # the pathological case (a silent swallow would undo #134), then wrap the write
                # itself so nothing on this best-effort path can propagate. For ordinary
                # exceptions detail == str(exc), so the line is byte-for-byte the #134 one.
                try:
                    detail = str(exc)
                except Exception:
                    detail = "<unprintable>"
                try:
                    print(
                        f"vikunja-mcp: epic-complete marker skipped for child #{task_id}: "
                        f"{exc.__class__.__name__}: {detail}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass
        # push-нудж (#117): ЛЮБАЯ задача, доведённая до Review, требует независимого ревью —
        # не только багфикс. Исключение — epic-контейнер (label epic): его код лежит в детях
        # (каждый отревьюен на своём advance), ревьюить нечего. Скип цепляется за метку epic,
        # НИКОГДА за наличие подзадач (тот же миграционный принцип, что у гейта
        # последовательности). Пер-таск-агент вернёт review_needed оркестратору, тот задиспатчит
        # свежего ревьюера (author != reviewer); review_kind задаёт рубрику: 'bug' —
        # воспроизвести и закрыть причину; 'change' — соответствие spec, реальные тесты, слайс.
        if to == "review" and not self._has_label(task, LABEL_EPIC):
            result["review_needed"] = True
            result["review_kind"] = "bug" if self._has_label(task, LABEL_BUG) else "change"
            result["note"] = (
                "this task needs independent review — return the review_needed flag to the "
                "orchestrator in your result: it will dispatch a fresh reviewer in the "
                "background (author ≠ reviewer). review_kind tells it the rubric: 'bug' — "
                "reproduce and confirm the cause is closed; 'change' — conforms to spec, real "
                "tests, stayed in slice, obvious regressions nearby"
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

    def decompose(self, task_id: int, subtasks: list[dict], ordered: bool = False) -> dict:
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
            # ordered chain (option C, epic #94): link adjacent children so each precedes the
            # next, in ARRAY ORDER — child[i] `precedes` child[i+1]. Vikunja auto-creates the
            # inverse `follows` on the SUCCESSOR (empirically verified on real 2.3.0), which is
            # exactly the kind the sequence gate reads (PREDECESSOR_RELATION_KINDS). So the head
            # keeps only an outgoing `precedes` (no follows -> claimable now) while every later
            # child gains `follows`→its predecessor the instant the chain is built (gated until
            # that predecessor reaches Review). The direction is load-bearing: a flipped chain
            # would gate the head and free the tail — the exact silent corruption to prevent.
            # Kept INSIDE the try so a chaining failure (children already exist) is surfaced by
            # the same partial-failure handler, never blind-retried. range(len(created) - 1) is a
            # no-op for 0/1 children. No cycle detection — a linear chain is acyclic by
            # construction (that's #105, deliberately out of scope).
            if ordered:
                for i in range(len(created) - 1):
                    self.api.add_relation(created[i]["id"], created[i + 1]["id"], "precedes")
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
        comment = f"[decompose] создано: {listing}"
        if ordered:
            comment += " (упорядочено: цепочка precedes — клеймабельна только голова)"
        self.api.add_comment(task_id, comment)
        label = self.api.get_or_create_label(LABEL_EPIC)
        self.api.add_label(task_id, label["id"])
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        result = {
            "created": created,
            "parent": {"id": task_id, "moved_to": "Backlog", "labeled": LABEL_EPIC},
        }
        if ordered:
            result["ordered"] = True
            result["note"] = (
                "children are chained head→tail (precedes/follows); only the head is claimable "
                "now — each successor unlocks when its predecessor reaches Review"
            )
        return result

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
        truncated and related is added — a compact dict {relation_kind: [{"id", "title"}, ...]}.
        attachments lists each file's METADATA only ({id, name, mime, size}) — no bytes, so a
        card that is nothing but a screenshot is SEEN, not guessed at; fetch the bytes with
        download_attachment(task_id, attachment_id) using the `id` here."""
        _, stage = self._find_task(task_id)
        task = self.api.get_task(task_id)
        raw_comments = self.api.comments(task_id)
        related_raw = task.get("related_tasks") or {}
        related = {
            kind: [{"id": rt["id"], "title": rt["title"]} for rt in items]
            for kind, items in related_raw.items()
        }
        # attachments come INSIDE the task JSON (tasks:read_one, no extra scope), each
        # {id, task_id, file:{name,mime,size}}; the server sends None (not []) when there are
        # none. Surface METADATA ONLY — the bytes would bloat every dossier (the point is the
        # agent SEES "shot.png (image/png)" and chooses whether to download_attachment it). `id`
        # is the attachment id download_attachment keys off (NOT file.id), so it is load-bearing.
        attachments = [
            {
                "id": a.get("id"),
                "name": (a.get("file") or {}).get("name"),
                "mime": (a.get("file") or {}).get("mime"),
                "size": (a.get("file") or {}).get("size"),
            }
            for a in task.get("attachments") or []
        ]
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
            "attachments": attachments,
            # comments are stored as HTML (#85); render back to plain text so the agent
            # reads clean multiline text (the human reads the formatted HTML in the UI).
            "comments": [
                {"author": c.get("author", {}).get("username", "?"),
                 "text": html_to_text(c.get("comment", ""))}
                for c in raw_comments
            ],
        }

    def download_attachment(self, task_id: int, attachment_id: int) -> dict:
        """Download a task attachment's bytes to a TEMP FILE and return its path (an agent then
        Reads the path — a PNG/JPG renders visually — instead of a base64 blob that bloats the
        context). `attachment_id` is the id from get_task's attachments[] (NOT the filename).
        Fails in agent-actionable ways: a wrong/absent id lists the task's real attachments; an
        oversized file (metadata size > cap) is refused BEFORE downloading, naming the size."""
        self._find_task(task_id)  # same board membership check as get_task/comment
        task = self.api.get_task(task_id)
        attachments = task.get("attachments") or []
        match = next((a for a in attachments if a.get("id") == attachment_id), None)
        if match is None:
            available = ", ".join(
                f"#{a.get('id')} {(a.get('file') or {}).get('name')}" for a in attachments
            ) or "none"
            raise WorkflowError(
                f"task {task_id} has no attachment #{attachment_id} — its attachments are: "
                f"{available}. Use the `id` from get_task's attachments[]"
            )
        file_meta = match.get("file") or {}
        name = file_meta.get("name") or f"attachment-{attachment_id}"
        # size cap read from METADATA, BEFORE downloading — so a runaway file fails fast and
        # actionably instead of pulling GBs into a temp file / the agent's context.
        size = file_meta.get("size")
        if isinstance(size, int) and size > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"attachment #{attachment_id} ({name}) is {size} bytes — over the "
                f"{_MAX_ATTACHMENT_BYTES}-byte download cap. Fetch it directly from the tracker "
                f"UI instead of pulling it into the agent context"
            )
        data = self.api.download_attachment(task_id, attachment_id)
        path = _write_attachment_to_temp(name, data, fallback=f"attachment-{attachment_id}")
        return {
            "path": path,
            "name": name,
            "mime": file_meta.get("mime"),
            "size": len(data),
            "note": (
                "Read this path to view the file — an image (PNG/JPG) renders visually, a "
                "text/PDF opens as text. It sits in a temp dir and is cleaned up automatically; "
                "Read it now rather than saving the path for later"
            ),
        }

    def attach_file(self, task_id: int, path: str) -> dict:
        """Upload a LOCAL file — typically a SCREENSHOT of finished, visually-verifiable work — as
        an attachment on the task, so a human and the independent reviewer SEE the result instead
        of taking 'done' on faith. The UPLOAD twin of download_attachment; deliberately a STANDALONE
        tool, NOT an argument to advance: a failed upload is its own actionable error, never a
        half-finished stage transition (the #118/#134/#135 lesson — keep cross-cutting side effects
        out of advance), and both the implementer (own task) and the reviewer (a task in Review)
        can attach. No ownership is required (same as download_attachment) — only board membership.

        Validated BEFORE any bytes hit the wire: `path` must resolve (realpath, so a symlink to a
        real file is followed) to an existing REGULAR file — a symlink to a dir/socket, a missing
        path, or a directory is refused with an actionable message — within the _MAX_ATTACHMENT_BYTES
        cap (checked via getsize, so a runaway file fails fast, never loaded). The path is NOT
        confined to the workspace: screenshots routinely land in a temp/Downloads dir outside the
        repo (a browser tool, an OS screenshot), so confining it would break the primary use case;
        the size cap + regular-file check are the guardrails. The basename becomes the attachment
        name (never the full path) and the MIME is guessed from the extension. Needs the
        tasks_attachments:create token scope — a 401 means the token is read-only for attachments
        and a human must add the `create` op (verified on real 2.3.0: create governs the upload)."""
        self._find_task(task_id)  # same board-membership check as comment/download_attachment
        real = os.path.realpath(path)
        if not os.path.isfile(real):
            raise WorkflowError(
                f"no file to attach at {path!r} — it doesn't exist or isn't a regular file. "
                f"Pass the path to a screenshot/render you already produced while verifying the "
                f"work (a directory, a broken symlink, or a missing path is refused here)"
            )
        size = os.path.getsize(real)
        if size > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"{path} is {size} bytes — over the {_MAX_ATTACHMENT_BYTES}-byte upload cap. "
                f"Attach a screenshot/thumbnail, not a large asset or a runtime artifact"
            )
        name = _safe_attachment_name(os.path.basename(real), fallback=f"attachment-{task_id}")
        with open(real, "rb") as fh:
            data = fh.read()
        mime, _ = mimetypes.guess_type(name)
        resp = self.api.upload_attachment(task_id, name, data, mime=mime)
        created = (resp or {}).get("success") or []
        new_id = created[0].get("id") if created and isinstance(created[0], dict) else None
        return {
            "attached": True,
            "task_id": task_id,
            "attachment_id": new_id,
            "name": name,
            "mime": mime,
            "size": size,
            "note": (
                "the file is on the card now — a human and the reviewer can view it in the "
                "tracker. For a visually-verifiable change, cite it in your advance(to='review') "
                "worklog as evidence alongside the commit sha"
            ),
        }
