"""Vikunja REST client. Gotchas baked in: PUT=create, POST=full-replace update -> RMW."""
import time
from typing import Any

import httpx

from .formatting import text_to_html


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
        self._page_size_cache: int | None = None

    # --- транзиентные ретраи (#86 «восстановление работы на ошибках апи») ---
    # Раньше _req падал с ПЕРВОЙ же 429/5xx/обрыва связи, и работа агента вставала на
    # ровном месте. Ретраим с backoff, но безопасно к семантике PUT=create/POST=replace:
    #   - 429: сервер ОТКЛОНИЛ запрос ДО применения -> ретраим ЛЮБОЙ метод (чтим Retry-After);
    #   - 5xx и обрыв/таймаут связи: исход неоднозначен (могло примениться) -> ретраим только
    #     идемпотентные GET и POST (POST = полная перезапись, повтор даёт то же состояние).
    #     PUT (create) и DELETE на этих ошибках НЕ ретраим — иначе дубль или ложная 404.
    # Постоянные ошибки (4xx кроме 429) поднимаются сразу, как и прежде.
    _MAX_RETRIES = 3
    _RETRY_STATUSES = frozenset({500, 502, 503, 504})
    _IDEMPOTENT_METHODS = frozenset({"GET", "POST"})
    _BACKOFF_BASE = 0.5
    _BACKOFF_CAP = 8.0

    def _req(
        self, method: str, path: str, json: Any = None, params: dict | None = None,
        raw: bool = False, files: Any = None,
    ) -> Any:
        # files (#137): a MULTIPART form upload (e.g. attach a screenshot) — httpx encodes it as
        # multipart/form-data instead of a JSON body, so it and `json` are mutually exclusive (the
        # upload path always passes json=None). Callers pass file CONTENT as bytes, not a file
        # handle, so a 429 retry below re-encodes the SAME body cleanly (a consumed stream would
        # re-send empty). Only PUT uploads use it, and PUT=create is not retried on 5xx (no dup).
        method = method.upper()
        for attempt in range(self._MAX_RETRIES + 1):
            final = attempt == self._MAX_RETRIES
            try:
                r = self._client.request(method, path, json=json, params=params, files=files)
            except httpx.TransportError:
                # обрыв/таймаут: могло примениться -> ретраим только идемпотентные методы
                if final or method not in self._IDEMPOTENT_METHODS:
                    raise
                time.sleep(self._backoff(attempt))
                continue
            if not final and self._should_retry(method, r.status_code):
                time.sleep(self._backoff(attempt, r.headers.get("Retry-After")))
                continue
            if r.status_code >= 400:
                raise VikunjaError(r.status_code, r.text[:300])
            # raw=True: тело — НЕ JSON (эндпоинт скачивания вложения отдаёт сырые байты
            # файла с content-type/content-disposition), поэтому возвращаем r.content как
            # есть, минуя r.json() (который бы упал на бинарнике). См. download_attachment.
            if raw:
                return r.content
            return r.json() if r.content else None
        raise AssertionError("unreachable: последняя попытка всегда вернёт или поднимет")

    def _should_retry(self, method: str, status: int) -> bool:
        if status == 429:
            return True  # отклонён до применения — безопасно ретраить любой метод
        return status in self._RETRY_STATUSES and method in self._IDEMPOTENT_METHODS

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self._BACKOFF_CAP)
            except ValueError:
                pass
        return min(self._BACKOFF_BASE * (2**attempt), self._BACKOFF_CAP)

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

    # --- attachments ---
    # Task attachments arrive INSIDE the task JSON under the existing tasks:read_one scope
    # (task["attachments"] = [{id, task_id, file:{id,name,mime,size}, ...}], or None when the
    # task has none — verified on real 2.3.0), so listing metadata needs no extra call. Only
    # DOWNLOADING the bytes hits a separate endpoint and needs the tasks_attachments:read scope.
    def download_attachment(self, task_id: int, attachment_id: int) -> bytes:
        """Raw bytes of a task attachment. `attachment_id` is the attachment's OWN id
        (task["attachments"][].id, surfaced by workflow.get_task), NOT the nested file.id.
        GET /tasks/{id}/attachments/{attachment_id} streams the file itself, not JSON, so it
        goes through _req(raw=True) — same GET retry/backoff, but the body is returned
        verbatim. Needs the tasks_attachments:read token scope; a wrong task or attachment id
        surfaces as VikunjaError(404)."""
        return self._req("GET", f"/tasks/{task_id}/attachments/{attachment_id}", raw=True)

    def upload_attachment(
        self, task_id: int, filename: str, data: bytes, mime: str | None = None
    ) -> dict:
        """Upload bytes as a task attachment (e.g. a screenshot of finished work). The endpoint is
        PUT /tasks/{id}/attachments and takes a MULTIPART form — file field `files` — NOT a JSON
        body, so it goes through _req(files=...): the upload-side twin of download_attachment's
        raw=True on the response side (api.py's JSON helpers don't fit either end). Verified on
        real 2.3.0: the governing scope is `tasks_attachments:create` (401 without it), the method
        is PUT (POST -> 405), and the response is
        {"errors": ..., "success": [{id, task_id, file:{id,name,mime,size,...}, ...}]}. `data` is
        bytes (not a stream) so a 429 retry re-encodes the same body; PUT=create is not retried on
        5xx, so an ambiguous failure can't duplicate the upload."""
        file_part = (filename, data, mime) if mime else (filename, data)
        return self._req("PUT", f"/tasks/{task_id}/attachments", files={"files": file_part})

    # --- comments ---
    def comments(self, task_id: int) -> list[dict]:
        return self._req("GET", f"/tasks/{task_id}/comments") or []

    def add_comment(self, task_id: int, text: str) -> dict:
        # Vikunja's comment field is HTML (#85): agents author plain text with newlines,
        # so convert to structure-preserving, HTML-escaped HTML at this single chokepoint
        # — every agent comment body (comment/spec/worklog/review/call_human/claim/...)
        # passes through here.
        return self._req(
            "PUT", f"/tasks/{task_id}/comments", json={"comment": text_to_html(text)}
        )

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
        raise VikunjaError(404, "project has no kanban view — run `vikunja-mcp setup`")

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
    # page size = max_items_per_page сервера (per_page на эту вложенную пагинацию не влияет).
    # Порог «полной страницы» читаем из /info (_page_size, кэш на клиенте); 50 — лишь fallback:
    # на инстансе с max_items_per_page<50 хардкод 50 молча обрезал бы доску после page=1.
    # Страницы могут перекрываться на 1-2 задачи из-за нестабильной сортировки при равных
    # ключах (без ORDER BY тайбрейкера) — наблюдался дубль, ни разу не пропуск. Мёржим по
    # (bucket_id, task_id), останавливаемся когда ни один бакет не отдал полную страницу
    # (значит дальше для всех пусто) ИЛИ страница не принесла ни одной новой задачи (защита
    # от зацикливания на нестабильной сортировке).
    _PAGE_SIZE_FALLBACK = 50

    def _page_size(self) -> int:
        if self._page_size_cache is None:
            self._page_size_cache = self._fetch_page_size()
        return self._page_size_cache

    def _fetch_page_size(self) -> int:
        # /info — публичный, неаутентифицированный эндпоинт; Bearer на нём безвреден.
        try:
            info = self._req("GET", "/info")
        except (VikunjaError, httpx.HTTPError):
            return self._PAGE_SIZE_FALLBACK
        size = info.get("max_items_per_page") if isinstance(info, dict) else None
        return size if isinstance(size, int) and size > 0 else self._PAGE_SIZE_FALLBACK

    def view_tasks(
        self, project_id: int, view_id: int, require_titles: set[str] | None = None
    ) -> list[dict]:
        # require_titles (#43): the set of bucket TITLES whose "full page" should keep the
        # pagination loop going. None (default) = every bucket counts -> exhaustive read, kept
        # for _find_task/claim/setup which must see the complete board (incl. a Done task).
        # When given, only those buckets drive paging: an unbounded Done/Backlog that still
        # returns full pages no longer forces extra fetches once the required buckets are
        # exhausted. next_task passes its working stages here so it stops after them instead of
        # rescanning the ever-growing Done on every call (the named next_task-latency fix).
        page_size = self._page_size()
        merged: dict[int, dict] = {}
        seen: dict[int, set] = {}
        owner: dict[int, int] = {}          # task_id -> последний бакет, где её видели (см. дедуп ниже)
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
                if len(tasks) >= page_size and (
                    require_titles is None or bucket.get("title") in require_titles
                ):
                    saw_full_page = True
                for task in tasks:
                    owner[task["id"]] = bid          # последнее вхождение выигрывает (см. дедуп ниже)
                    if task["id"] not in ids:
                        ids.add(task["id"])
                        dest["tasks"].append(task)
                        added_new = True
            if not saw_full_page or not added_new:
                break
            page += 1
        # #41 глобальный дедуп по task id: задачу, переезжающую между колонками ВО ВРЕМЯ
        # постраничного чтения, мы видим в старом бакете на ранней странице и в новом — на поздней,
        # т.е. дважды. Покомпонентный (bucket_id, task_id) merge выше оба вхождения сохранял, и
        # _find_task (берёт первое) залипал на устаревшей колонке. Оставляем задачу ТОЛЬКО в её
        # последнем бакете: страницы читаются последовательно во времени, поздняя = более свежее
        # наблюдение доски, куда бы задачу ни двигали. После этого прохода каждый task id встречается
        # ровно один раз, поэтому дедуп и _find_task (первое вхождение) согласованы по определению.
        for bid, dest in merged.items():
            dest["tasks"] = [t for t in dest["tasks"] if owner.get(t["id"]) == bid]
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

    def remove_label(self, task_id: int, label_id: int) -> None:
        self._req("DELETE", f"/tasks/{task_id}/labels/{label_id}")

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
