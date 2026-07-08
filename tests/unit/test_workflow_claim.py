import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def test_next_task_empty_queue(env):
    api, wf = env
    assert wf.next_task()["task"] is None


def test_next_task_orders_queue_by_priority(env):
    api, wf = env
    api.add_task("low", "Queue", priority=1)
    top = api.add_task("high", "Queue", priority=5)
    api.add_task("backlog-idea", "Backlog", priority=10)   # не показывается
    res = wf.next_task()
    assert res["task"]["id"] == top["id"] and res["resume"] is False


def test_next_task_free_queue_task_carries_claim_note(env):
    """Свободная задача из Queue отдаётся с note: без него resume:false молчал и
    читался оркестратором как «делать нечего» — он стопался вместо claim→dispatch."""
    api, wf = env
    free = api.add_task("free", "Queue", priority=3)
    res = wf.next_task()
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False
    assert res["note"] and "claim" in res["note"]  # инструкция thin-pump потоку, не молчание


def test_next_task_free_queue_note_overrides_steward_default(env):
    """Регресс к косяку в проекте-потребителе (DOGE): под generic-автолупом
    оркестратор счёл свежую задачу «не начинать новое без go-ahead» и остановил
    цикл. Note обязан явно перебить этот дефолт — claim свежей Queue-задачи это
    мандат (а не самовольная инициатива), и цикл под этим предлогом не стопаем."""
    api, wf = env
    api.add_task("free", "Queue", priority=3)
    note = wf.next_task()["note"]
    assert "мандат" in note       # взять свежую — мандат, не «самовольная инициатива»
    assert "останавлив" in note   # ...и НЕ останавливать /loop (собственно косяк DOGE)


def test_next_task_skips_assigned_and_blocked(env):
    api, wf = env
    api.add_task("taken", "Queue", assignee={"id": 9, "username": "other"})
    api.add_task("stuck", "Queue", labels=("blocked",))
    free = api.add_task("free", "Queue")
    assert wf.next_task()["task"]["id"] == free["id"]


def test_next_task_prefers_my_active(env):
    api, wf = env
    api.add_task("queued", "Queue", priority=5)
    mine = api.add_task("in build", "Build", assignee=api.me_user)
    res = wf.next_task()
    assert res["task"]["id"] == mine["id"] and res["resume"] is True
    assert res["stage"] == "Build"
    assert "свер" in res["note"] and "верифицируй" in res["note"]  # resume => сначала перепроверь


def test_next_task_resumes_stuck_claim_in_queue(env):
    """F2: клейм с не доведённым до конца move (assign ok, move failed) — задача моя,
    но всё ещё в Queue. next_task обязан её вернуть, а не молча пропустить."""
    api, wf = env
    stuck = api.add_task("half-claimed", "Queue", assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True
    assert res["stage"] == "Queue"
    assert res["task"]["id"] == stuck["id"]
    assert "claim" in res["note"]


def test_next_task_stuck_claim_outranks_higher_priority_free_task(env):
    """Возврат к своему недоклейменному таску важнее, даже если в очереди есть
    более приоритетная свободная задача — сначала долечи то, что уже на тебе."""
    api, wf = env
    api.add_task("free-and-shiny", "Queue", priority=10)
    stuck = api.add_task("half-claimed", "Queue", priority=1, assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True and res["task"]["id"] == stuck["id"]


def test_next_task_active_stage_still_wins_over_stuck_queue(env):
    """Активная Design/Build задача (обычный resume) приоритетнее недоклейменной в Queue."""
    api, wf = env
    api.add_task("half-claimed", "Queue", assignee=api.me_user)
    active = api.add_task("in build", "Build", assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True and res["stage"] == "Build" and res["task"]["id"] == active["id"]


def test_claim_happy_path(env):
    api, wf = env
    t = api.add_task("job", "Queue")
    res = wf.claim(t["id"])
    assert res["claimed"] is True
    assert api.stage_of(t["id"]) == "Design"
    assert api.tasks[t["id"]]["assignees"][0]["username"] == "agent-infra"
    assert any(c.startswith("[claim]") for c in api.comments_text(t["id"]))


def test_claim_refuses_outside_queue(env):
    api, wf = env
    t = api.add_task("wip", "Build")
    with pytest.raises(WorkflowError, match="Queue"):
        wf.claim(t["id"])


def test_claim_refuses_already_assigned(env):
    api, wf = env
    t = api.add_task("taken", "Queue", assignee={"id": 9, "username": "other"})
    with pytest.raises(WorkflowError, match="other"):
        wf.claim(t["id"])


def test_claim_self_heals_when_sole_assignee_is_already_me(env):
    """F2: партиальный клейм (assign прошёл, move — нет) или человек руками вернул
    заклеймленную задачу в Queue. Повторный claim должен долечить, а не отказывать."""
    api, wf = env
    t = api.add_task("half-claimed", "Queue", assignee=api.me_user)
    res = wf.claim(t["id"])
    assert res["claimed"] is True
    assert api.stage_of(t["id"]) == "Design"
    assert [a["id"] for a in api.tasks[t["id"]]["assignees"]] == [api.me_user["id"]]
    assert any(c.startswith("[claim]") for c in api.comments_text(t["id"]))


def test_claim_does_not_self_heal_outside_queue(env):
    """Сам себе назначен, но задача не в Queue — обычный отказ, self-heal тут не при чём."""
    api, wf = env
    t = api.add_task("half-claimed-elsewhere", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError, match="Queue"):
        wf.claim(t["id"])


def test_claim_race_lost_backs_off(env):
    """Гонка: между нашим assign и verify появился второй assignee -> снять себя, отказ."""
    api, wf = env
    t = api.add_task("contested", "Queue")

    original_add = api.add_assignee

    def racing_add(task_id, user_id):
        original_add(task_id, user_id)
        original_add(task_id, 9)   # конкурент успел между assign и re-read

    api.add_assignee = racing_add
    with pytest.raises(WorkflowError, match="гонк"):
        wf.claim(t["id"])
    assert all(a["id"] != 2 for a in api.tasks[t["id"]]["assignees"])  # себя сняли
    assert api.stage_of(t["id"]) == "Queue"                            # не двигали


def test_claim_raises_when_assignee_vanishes_normal_path(env):
    """Vanish-window: между нашим assign и re-read человек снял назначение — fresh без
    assignees. others пуст, но двигать в Design без ассайни нельзя (невидимое состояние):
    задача осталась бы вне next_task (не моя активная) и вне Queue (никто не заклеймит)."""
    api, wf = env
    t = api.add_task("job", "Queue")

    original_get = api.get_task

    def vanishing_get(task_id):
        api.remove_assignee(task_id, api.me_user["id"])   # человек снял в окно перед re-read
        return original_get(task_id)

    api.get_task = vanishing_get
    with pytest.raises(WorkflowError, match="исчез"):
        wf.claim(t["id"])
    assert api.stage_of(t["id"]) == "Queue"                # не уехала в Design
    assert api.tasks[t["id"]]["assignees"] == []           # без ассайни, как в реальном vanish


def test_claim_raises_when_assignee_vanishes_self_heal_path(env):
    """Тот же vanish, но self-heal путь: задача предзаклеймлена на меня (add_assignee не
    звался). Окно между re-read и move то же — отказ обязан сработать и здесь."""
    api, wf = env
    t = api.add_task("half-claimed", "Queue", assignee=api.me_user)

    original_get = api.get_task

    def vanishing_get(task_id):
        api.remove_assignee(task_id, api.me_user["id"])
        return original_get(task_id)

    api.get_task = vanishing_get
    with pytest.raises(WorkflowError, match="исчез"):
        wf.claim(t["id"])
    assert api.stage_of(t["id"]) == "Queue"
    assert api.tasks[t["id"]]["assignees"] == []
