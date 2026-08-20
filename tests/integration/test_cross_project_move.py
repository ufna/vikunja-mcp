"""`handoff` / `transfer_task` против реальной Vikunja 2.3.0 (#1179).

Здесь проверяется ровно то, чего фейк доказать не может, потому что он это МОДЕЛИРУЕТ:
`FakeAPI.update_task` сам переиндексует карточку при смене `project_id`, сам переносит её в
дефолтный бакет цели и сам считает новый `identifier`. Всё это списано с измерения на живом
контейнере — а модель, списанная с измерения, ровно настолько же может от него отстать. Если
сервер когда-нибудь перестанет переиндексовать (или начнёт допускать коллизию индексов), юниты
останутся зелёными, а `transfer_task` начнёт возвращать реф, которого нет. Красным станет здесь.

Второе, чего фейк не покажет: что связь `blocked`, поставленная `handoff`, реально живёт через
границу проектов НА СЕРВЕРЕ, и что гейт предшественников читает её оттуда.
"""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import Workflow, WorkflowError

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def pair(boss_jwt, agent_jwts):
    boss = VikunjaAPI(BASE, boss_jwt)
    suffix = uuid.uuid4().hex[:8]
    pid_home = reconcile(boss, f"mvhome-{suffix}", shares=[("agent1", 1)])
    pid_far = reconcile(boss, f"mvfar-{suffix}", shares=[("agent1", 1)])
    pid_private = reconcile(boss, f"mvpriv-{suffix}", shares=[])      # agent1 БЕЗ доступа
    jwt1, _ = agent_jwts
    wf = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid_home, siblings={"far": pid_far})
    return boss, wf, pid_home, pid_far, pid_private


def _stage(boss, pid, task_id):
    """Название бакета, в котором лежит карточка на доске проекта, или None если её там нет."""
    view = boss.kanban_view(pid)
    for bucket in boss.view_tasks(pid, view["id"]):
        if any(t["id"] == task_id for t in bucket.get("tasks") or []):
            return bucket["title"]
    return None


def _put(boss, pid, task_id, bucket_title):
    view = boss.kanban_view(pid)
    bucket = next(b for b in boss.buckets(pid, view["id"]) if b["title"] == bucket_title)
    boss.move_task(pid, view["id"], bucket["id"], task_id)


def test_transfer_reindexes_the_card_and_lands_it_in_the_targets_backlog(pair):
    """Измерено вручную на 2.3.0 ДО реализации: FRNT-2 приезжает как BACK-3 в проект, где уже
    жила BACK-2 — счётчик ЦЕЛИ выдаёт следующий свободный индекс, коллизии нет, старый реф
    перестаёт указывать на карточку. Это и есть причина, по которой инструмент обязан вернуть
    новый реф; тест держит и сам переезд, и смену рефа."""
    boss, wf, pid_home, pid_far, _private = pair
    # в цели уже есть своя карточка -> счётчики двух проектов заведомо разъезжаются
    boss.create_task(pid_far, "уже жила в цели")
    card = boss.create_task(pid_home, "чисто дальняя работа, заведена не на той доске")
    old_identifier = boss.get_task(card["id"])["identifier"]

    res = wf.transfer_task(card["id"], to="far", reason="не та доска")

    moved = boss.get_task(card["id"])
    assert moved["project_id"] == pid_far
    assert moved["identifier"] != old_identifier, (
        "сервер больше не переиндексует карточку при переезде — тогда и фейк, который это "
        "моделирует, и note инструмента про смену рефа рассказывают неправду"
    )
    assert res["moved"]["ref"] == f"{moved['identifier']} ({card['id']})"
    assert _stage(boss, pid_far, card["id"]) == "Backlog"
    assert _stage(boss, pid_home, card["id"]) is None, "карточка осталась и на исходной доске"


def test_transfer_into_an_unshared_project_is_refused_and_the_card_stays_put(pair):
    """Граница — сам скоуп-токен, и отказ должен приходить ДО первой записи: карточка обязана
    остаться дома целиком, а не переехать наполовину."""
    boss, wf, pid_home, _pid_far, pid_private = pair
    card = boss.create_task(pid_home, "останется дома")
    _put(boss, pid_home, card["id"], "Queue")

    with pytest.raises(WorkflowError):
        wf.transfer_task(card["id"], to=pid_private, reason="некуда")

    assert boss.get_task(card["id"])["project_id"] == pid_home
    assert _stage(boss, pid_home, card["id"]) == "Queue"


def test_handoff_blocks_across_the_project_boundary_on_the_server(pair):
    """Связь `blocked` ставится на карточку в ОДНОМ проекте и указывает на карточку в ДРУГОМ.
    Фейк это умеет по построению; здесь — что её так же отдаёт сервер и что гейт предшественников,
    читая её ОТТУДА, действительно держит карточку, а когда дальняя доезжает до Review, отпускает.
    Это ровно тот путь, который до #1179 молча не работал: предшественника не было на домашней
    доске, и гейт списывал его как удалённого."""
    boss, wf, pid_home, pid_far, _private = pair
    card = boss.create_task(pid_home, "фронтовая работа, упирается в дальний эндпоинт")
    _put(boss, pid_home, card["id"], "Queue")
    wf.claim(card["id"])

    res = wf.handoff(card["id"], to="far", title="сделать эндпоинт")
    far_id = res["filed"]["id"]

    # связь видна НА СЕРВЕРЕ с домашней стороны и указывает в чужой проект
    blockers = boss.get_task(card["id"]).get("related_tasks", {}).get("blocked") or []
    assert far_id in [b["id"] for b in blockers]
    assert boss.get_task(far_id)["project_id"] == pid_far
    assert _stage(boss, pid_far, far_id) == "Backlog"
    assert _stage(boss, pid_home, card["id"]) == "Queue"

    # Гейт держит. Утверждения АДРЕСНЫЕ, а не про пустоту очереди: доска у модуля общая, в
    # Queue лежат карточки соседних тестов, и `next_task().get("task") is None` здесь ловило бы
    # чужую карточку вместо этой — зелёное или красное по причине, к #1179 отношения не имеющей.
    offered = wf.next_task().get("task")
    assert offered is None or offered["id"] != card["id"], (
        "карточка предложена, хотя её блокер в чужом проекте не тронут"
    )
    with pytest.raises(WorkflowError):
        wf.claim(card["id"])

    # дальняя доезжает до Review -> гейт отпускает, без человека в середине. Проверяется через
    # claim, а не через очередь: порядок выдачи зависит от соседних карточек, право взять — нет.
    _put(boss, pid_far, far_id, "Review")
    assert wf.claim(card["id"])["claimed"] is True
