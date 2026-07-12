"""Кросс-проектный file_task против реальной Vikunja 2.3.0 — то, чего фейк не докажет:
реальная форма отказа на нерасшаренном проекте (объектная 403 у скоуп-токена) и что
'related'-связь реально живёт через границу проектов."""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import Workflow, WorkflowError

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def cross(boss_jwt, agent_jwts):
    boss = VikunjaAPI(BASE, boss_jwt)
    suffix = uuid.uuid4().hex[:8]
    pid_home = reconcile(boss, f"xhome-{suffix}", shares=[("agent1", 1)])
    pid_target = reconcile(boss, f"xtarget-{suffix}", shares=[("agent1", 1)])
    pid_private = reconcile(boss, f"xprivate-{suffix}", shares=[])  # agent1 БЕЗ доступа
    jwt1, _ = agent_jwts
    wf = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid_home)
    return boss, wf, pid_home, pid_target, pid_private


def test_file_task_lands_in_target_projects_backlog_with_relation(cross):
    boss, wf, pid_home, pid_target, _ = cross
    src = boss.create_task(pid_home, "работа в A, требующая правки в B")
    res = wf.file_task(
        title="сделать эндпоинт в B для A",
        description="агент A просит агента B",
        related_task_id=src["id"],
        project_id=pid_target,
    )
    new_id = res["filed"]["id"]
    assert res["filed"]["project_id"] == pid_target
    # карточка реально в Backlog ЦЕЛЕВОГО борда (координаты чужого view/bucket сработали)
    view = boss.kanban_view(pid_target)
    board = boss.view_tasks(pid_target, view["id"])
    backlog = next(b for b in board if b["title"] == "Backlog")
    assert any(t["id"] == new_id for t in backlog.get("tasks") or [])
    # 'related' видна с ИСХОДНОЙ стороны границы проектов
    related = boss.get_task(src["id"]).get("related_tasks") or {}
    assert any(rt["id"] == new_id for rt in related.get("related") or [])


def test_file_task_into_unshared_project_refused_nothing_created(cross):
    boss, wf, _home, _target, pid_private = cross
    title = f"never-lands-{uuid.uuid4().hex[:6]}"
    with pytest.raises(WorkflowError, match="can't file into project"):
        wf.file_task(title=title, project_id=pid_private)
    # fail-fast: в закрытом проекте не осиротело НИЧЕГО (проверяет boss — владелец)
    view = boss.kanban_view(pid_private)
    board = boss.view_tasks(pid_private, view["id"])
    assert not any(
        t["title"] == title for b in board for t in (b.get("tasks") or [])
    )
