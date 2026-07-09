import io
import os
import uuid

import httpx
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
    # #82: ref = human-searchable identifier + global id, sourced from the real task's
    # `identifier` field as it comes back on the board (verified end-to-end vs 2.3.0;
    # a no-prefix project yields identifier "#<index>")
    assert picked["task"]["ref"] == f"{boss.get_task(t['id'])['identifier']} ({t['id']})"
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
    assert d1["stage"] == "Your Call" and d1["assignees"] == ["agent1"]

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


def test_attachment_metadata_and_scoped_download(project, boss_jwt):
    """#139 end-to-end vs real 2.3.0: the boss uploads a PNG to a task; the agent (SCOPED token,
    tasks_attachments:read_one) SEES it in get_task's attachments (metadata only) and downloads
    the EXACT bytes to a temp file with the original name. Guards the #125/#118 fake-agrees-with-
    fake trap — a unit test against FakeAPI proves neither the real endpoint shape nor the scope."""
    boss, _, enqueue, wf1, _ = project
    t = enqueue("карточка со скриншотом")
    png = bytes.fromhex("89504e470d0a1a0a") + b"real-png-body"
    up = httpx.put(
        f"{BASE}/api/v1/tasks/{t['id']}/attachments",
        headers={"Authorization": f"Bearer {boss_jwt}"},
        files={"files": ("shot.png", io.BytesIO(png), "image/png")},
    )
    up.raise_for_status()

    # Part 1: the agent sees the metadata (no bytes) via the dossier
    dossier = wf1.get_task(t["id"])
    att = next(a for a in dossier["attachments"] if a["name"] == "shot.png")
    assert att["mime"] == "image/png" and att["size"] == len(png)

    # Part 2: the agent downloads the exact bytes to a temp file keeping the original name
    res = wf1.download_attachment(t["id"], att["id"])
    assert os.path.basename(res["path"]) == "shot.png"          # extension preserved
    with open(res["path"], "rb") as fh:
        assert fh.read() == png                                  # exact bytes over the real wire
    assert res["size"] == len(png) and res["mime"] == "image/png"

    # a wrong attachment id fails actionably (lists the real ones), not a bare 404
    with pytest.raises(WorkflowError, match="no attachment"):
        wf1.download_attachment(t["id"], 999999)


def test_attachment_upload_scoped(project, tmp_path):
    """#137 end-to-end vs real 2.3.0: the agent (SCOPED token, tasks_attachments:create) uploads a
    LOCAL screenshot via attach_file and it lands on the card — get_task then surfaces its metadata
    with the exact size, and the agent downloads the exact bytes back. Proves the real multipart
    PUT endpoint AND the `create` scope, which no unit test against FakeAPI can (the #125/#118
    fake-agrees-with-fake trap). A missing path is refused locally, before any wire call."""
    _, _, enqueue, wf1, _ = project
    t = enqueue("карточка визуального фикса")
    png = bytes.fromhex("89504e470d0a1a0a") + b"agent-uploaded-shot"
    src = tmp_path / "fix.png"
    src.write_bytes(png)

    res = wf1.attach_file(t["id"], str(src))
    assert res["attached"] is True and res["name"] == "fix.png"
    assert res["size"] == len(png) and res["mime"] == "image/png"
    assert res["attachment_id"] is not None

    # it round-trips: the dossier now shows the uploaded file's metadata
    att = next(a for a in wf1.get_task(t["id"])["attachments"] if a["id"] == res["attachment_id"])
    assert att["name"] == "fix.png" and att["size"] == len(png) and att["mime"] == "image/png"

    # and the exact bytes come back down (create + read_one both in the token scope)
    back = wf1.download_attachment(t["id"], res["attachment_id"])
    with open(back["path"], "rb") as fh:
        assert fh.read() == png

    # a missing path is refused locally — no wire call, an actionable message
    with pytest.raises(WorkflowError, match="no file to attach"):
        wf1.attach_file(t["id"], str(tmp_path / "nope.png"))


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
