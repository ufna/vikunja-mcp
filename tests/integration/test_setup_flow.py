import uuid

import pytest

from tests.integration.conftest import BASE
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import STAGES

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


def titles(api, pid):
    view = api.kanban_view(pid)
    return [b["title"] for b in api.buckets(pid, view["id"])]


def test_fresh_setup_and_idempotency(boss_jwt):
    api = VikunjaAPI(BASE, boss_jwt)
    name = f"proj-{uuid.uuid4().hex[:8]}"
    pid = reconcile(api, name, shares=[("agent1", 1), ("agent2", 1)])
    assert titles(api, pid) == STAGES     # включая ПОРЯДОК колонок (Done — последняя)
    view = api.kanban_view(pid)
    # если GET вида не отдаёт done_bucket_id — проверять возврат configure_kanban (тогда
    # поправить reconcile, чтобы он возвращал/логировал ответ), но не ослаблять проверку
    assert view["done_bucket_id"] == next(
        b["id"] for b in api.buckets(pid, view["id"]) if b["title"] == "Done"
    )
    assert view.get("bucket_configuration_mode") == "manual"   # канбан не сломан (гоча!)

    before = titles(api, pid)
    pid2 = reconcile(api, name, shares=[("agent1", 1)])
    assert pid2 == pid and titles(api, pid) == before


def test_migration_from_old_buckets(boss_jwt):
    api = VikunjaAPI(BASE, boss_jwt)
    name = f"old-{uuid.uuid4().hex[:8]}"
    project = api.create_project(name)
    pid = project["id"]
    view = api.kanban_view(pid)
    # эмулируем старую раскладку бутстрапа: Todo/Doing/Review/Done.
    # Сначала создаём старые, потом сносим авто-бакеты — Vikunja не даст удалить
    # последний бакет вида, поэтому порядок именно такой.
    auto = api.buckets(pid, view["id"])
    old = {t: api.create_bucket(pid, view["id"], t) for t in ["Todo", "Doing", "Review", "Done"]}
    for b in auto:
        api.delete_bucket(pid, view["id"], b["id"])
    t1 = api.create_task(pid, "waiting")
    api.move_task(pid, view["id"], old["Todo"]["id"], t1["id"])
    t2 = api.create_task(pid, "wip")
    api.move_task(pid, view["id"], old["Doing"]["id"], t2["id"])

    reconcile(api, name, shares=[])
    board = {b["title"]: [t["id"] for t in b.get("tasks") or []]
             for b in api.view_tasks(pid, api.kanban_view(pid)["id"])}
    assert t1["id"] in board["Queue"] and t2["id"] in board["Build"]
    assert "Todo" not in board and "Doing" not in board
