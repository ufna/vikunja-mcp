import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import Workflow, WorkflowError

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def project(boss_jwt, agent_jwts):
    boss = VikunjaAPI(BASE, boss_jwt)
    pid = reconcile(boss, f"flow-{uuid.uuid4().hex[:8]}", shares=[("agent1", 1), ("agent2", 1)])
    view = boss.kanban_view(pid)
    queue_id = next(b["id"] for b in boss.buckets(pid, view["id"]) if b["title"] == "Queue")

    def enqueue(title, priority=0):
        t = boss.create_task(pid, title, priority=priority)
        boss.move_task(pid, view["id"], queue_id, t["id"])
        return t

    jwt1, jwt2 = agent_jwts
    wf1 = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid)
    wf2 = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt2)), pid)
    return boss, pid, enqueue, wf1, wf2


def test_happy_path_queue_to_review(project):
    boss, pid, enqueue, wf1, _ = project
    t = enqueue("сделать фичу", priority=3)
    picked = wf1.next_task()
    assert picked["task"]["id"] == t["id"]
    wf1.claim(t["id"])
    wf1.advance(t["id"], to="build", spec="подход: X")
    wf1.advance(t["id"], to="review", worklog="сделано X", evidence="commit deadbeef")
    dossier = wf1.get_task(t["id"])
    assert dossier["stage"] == "Review"
    marks = [c["text"].split("\n")[0].split(" ")[0] for c in dossier["comments"]]
    assert "[claim]" in marks and "[spec]" in marks and "[worklog]" in marks


def test_claim_race_second_agent_refused(project):
    _, _, enqueue, wf1, wf2 = project
    t = enqueue("спорная задача")
    wf1.claim(t["id"])
    with pytest.raises(WorkflowError):
        wf2.claim(t["id"])


def test_gates_and_no_done(project):
    _, _, enqueue, wf1, _ = project
    t = enqueue("гейты")
    wf1.claim(t["id"])
    with pytest.raises(WorkflowError):
        wf1.advance(t["id"], to="review", worklog="w", evidence="e")  # мимо Build
    with pytest.raises(WorkflowError):
        wf1.advance(t["id"], to="build", spec="")                     # пустой spec
    with pytest.raises(WorkflowError):
        wf1.advance(t["id"], to="done")                               # запрещено всегда


def test_call_human_return_and_decompose(project):
    boss, pid, enqueue, wf1, _ = project
    t1 = enqueue("вопрос человеку")
    wf1.claim(t1["id"])
    wf1.call_human(t1["id"], question="какой вариант выбрать: A или B?")
    d1 = wf1.get_task(t1["id"])
    assert d1["stage"] == "Call to Human" and d1["assignees"] == ["agent1"]

    t2 = enqueue("заблокированная")
    wf1.claim(t2["id"])
    wf1.return_task(t2["id"], reason="нет доступа к стенду")
    d2 = wf1.get_task(t2["id"])
    assert d2["stage"] == "Backlog" and d2["assignees"] == [] and "blocked" in d2["labels"]

    t3 = enqueue("большая задача")
    wf1.claim(t3["id"])
    res = wf1.decompose(t3["id"], subtasks=[{"title": "часть 1"}, {"title": "часть 2"}])
    for child in res["created"]:
        assert wf1.get_task(child["id"])["stage"] == "Queue"
    d3 = wf1.get_task(t3["id"])
    assert d3["stage"] == "Backlog" and "epic" in d3["labels"]
