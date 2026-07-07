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
