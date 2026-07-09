"""Интеграция против реальной Vikunja (docker). Скип без VIKUNJA_TEST_URL."""
import os
import time

import httpx
import pytest

BASE = os.environ.get("VIKUNJA_TEST_URL", "").rstrip("/")
PASSWORD = "integr4tion-Pass!"

# срез боевых прав агента (roles/vikunja/files/vikunja-bootstrap.py), но с 2 добавками
# найденными интеграционными тестами против реальной 2.3.0 (тот скрипт несёт тот же
# пробел — см. отчёт T10):
# - "other": ["user"] — без него GET /api/v1/user 401-ит для скоуп-токенов, и
#   Workflow._me() не может себя идентифицировать (next_task/claim/advance/... сломаны).
#   routes["other"]["user"] это GET /api/v1/user — не путать с несуществующей
#   верхнеуровневой группой "user" (PUT /tokens 400 The permission of group user is invalid).
# - "projects": добавлен "views_buckets" (GET .../buckets) — без него api.buckets()
#   (список колонок канбана) 401-ит; "views_buckets_tasks" (move) уже был в списке,
#   но одного move недостаточно — Workflow._bucket() сначала читает список.
# - "tasks_attachments": ["read_one"] (#139) — скачивание вложения это
#   GET /tasks/:task/attachments/:attachment, а в /routes этот эндпоинт висит на op
#   `read_one` (НЕ `read`; `read_all` — это листинг GET .../attachments). Без него
#   Workflow.download_attachment 401-ит. Человек уже добавил его боевым агент-токенам
#   (карточка #139), так что это держит скоуп-токен теста в синхроне с продом.
AGENT_PERMS = {
    "tasks": ["read_all", "read_one", "create", "update", "position"],
    "tasks_assignees": ["create", "delete", "read_all"],
    "tasks_comments": ["create", "read_all", "read_one"],
    "tasks_labels": ["create", "read_all"],
    "tasks_relations": ["create", "delete"],
    "tasks_attachments": ["read_one"],
    "projects": ["read_all", "read_one", "views_buckets", "views_buckets_tasks"],
    "projects_views": ["read_all", "read_one"],
    "projects_views_tasks": ["read_all"],
    "labels": ["read_all", "create"],
    "other": ["user"],
}

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


def _api(path):
    return f"{BASE}/api/v1{path}"


def _with_retry(request):
    """login/register делят один anti-bruteforce лимит (наблюдалось: 10/60s,
    заголовки X-Ratelimit-*), который несколько локальных прогонов подряд легко
    выбивают за пределами обычного одного `pytest tests/integration` (см. отчёт T10).
    Ждём до X-Ratelimit-Reset (с фолбэком на экспоненциальный бэкофф) и повторяем."""
    r = request()
    for _ in range(5):
        if r.status_code != 429:
            return r
        reset_at = r.headers.get("X-Ratelimit-Reset")
        wait = max(float(reset_at) - time.time(), 1.0) if reset_at else 2.0
        time.sleep(min(wait, 30.0) + 0.5)
        r = request()
    return r


def register_and_login(username: str) -> str:
    _with_retry(lambda: httpx.post(_api("/register"), json={
        "username": username, "email": f"{username}@test.local", "password": PASSWORD,
    }))  # 400 если уже есть — ок
    r = _with_retry(lambda: httpx.post(_api("/login"), json={
        "username": username, "password": PASSWORD,
    }))
    r.raise_for_status()
    return r.json()["token"]


def mint_scoped_token(jwt: str) -> str:
    headers = {"Authorization": f"Bearer {jwt}"}
    routes = _with_retry(lambda: httpx.get(_api("/routes"), headers=headers)).json()
    perms = {
        grp: [op for op in ops if op in routes.get(grp, [])]
        for grp, ops in AGENT_PERMS.items()
        if grp in routes
    }
    r = _with_retry(lambda: httpx.put(_api("/tokens"), headers=headers, json={
        "title": "scoped", "permissions": perms, "expires_at": "2099-01-01T00:00:00Z",
    }))
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture(scope="session")
def boss_jwt():
    return register_and_login("boss")


@pytest.fixture(scope="session")
def agent_jwts():
    return register_and_login("agent1"), register_and_login("agent2")
