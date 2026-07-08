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
        child_dossier = wf1.get_task(child["id"])
        assert child_dossier["stage"] == "Queue"
        # F3: get_task теперь возвращает related — компактный дикт по kind'ам родства,
        # построенный из raw related_tasks (проверено против реальной 2.3.0:
        # add_relation(child, parent, "parenttask") кладёт связь на child под ключом
        # "parenttask", значения — полные таск-дикты, отсюда компактим до id/title)
        assert t3["id"] in [p["id"] for p in child_dossier["related"].get("parenttask", [])]
        # тот же факт напрямую через raw API — независимое подтверждение формы related_tasks
        child_raw = boss.get_task(child["id"])
        parents = child_raw.get("related_tasks", {}).get("parenttask") or []
        assert t3["id"] in [p["id"] for p in parents]
    d3 = wf1.get_task(t3["id"])
    assert d3["stage"] == "Backlog" and "epic" in d3["labels"]


def test_remove_label_round_trip(project):
    """remove_label реально дёргает DELETE /tasks/{id}/labels/{label_id} и доска это
    отражает — метка исчезает с задачи (проверяем форму эндпоинта против 2.3.0)."""
    boss, _, enqueue, _, _ = project
    t = enqueue("метка на удаление")
    label = boss.get_or_create_label(f"tmp-{uuid.uuid4().hex[:6]}")
    boss.add_label(t["id"], label["id"])
    assert any(lb["id"] == label["id"] for lb in boss.get_task(t["id"]).get("labels") or [])
    boss.remove_label(t["id"], label["id"])
    assert not any(lb["id"] == label["id"] for lb in boss.get_task(t["id"]).get("labels") or [])


def test_pagination_beyond_first_page(boss_jwt, agent_jwts):
    """F1: >50 задач в одном бакете Queue. GET .../views/{v}/tasks у vikunja 2.3.0
    пагинирует tasks[] ВНУТРИ бакета независимо (params={"page": n}, фиксированный page
    size 50 = max_items_per_page сервера, не зависит от per_page) — без мёржа страниц
    (см. api.view_tasks) next_task/_find_task слепнут на задачах за пределами page 1.

    Изолированный проект (не шарим board с другими тестами модуля), чтобы 56 задач
    не мешали приоритетным сравнениям в остальных тестах файла.

    `top` создаётся ПЕРВЫМ, то есть самой "старой" задачей бакета, а 55 менее
    приоритетных filler'ов — следом: эмпирически (отчёт F1) vikunja отдаёт на page=1
    самые СВЕЖИЕ 50 задач бакета, более старые — только на следующих страницах, так что
    top гарантированно недостижим без пагинации по страницам.
    """
    boss = VikunjaAPI(BASE, boss_jwt)
    pid = reconcile(boss, f"page-{uuid.uuid4().hex[:8]}", shares=[("agent1", 1)])
    view = boss.kanban_view(pid)
    queue_id = next(b["id"] for b in boss.buckets(pid, view["id"]) if b["title"] == "Queue")

    top = boss.create_task(pid, "самый приоритетный", priority=9)
    boss.move_task(pid, view["id"], queue_id, top["id"])
    for i in range(55):
        filler = boss.create_task(pid, f"filler-{i:03d}", priority=1)
        boss.move_task(pid, view["id"], queue_id, filler["id"])

    jwt1, _ = agent_jwts
    wf1 = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid)

    picked = wf1.next_task()
    assert picked["task"]["id"] == top["id"]   # не потерялся среди 56 задач в Queue

    wf1.claim(top["id"])                        # _find_task обязан найти его за page 1
    assert wf1.get_task(top["id"])["stage"] == "Design"
