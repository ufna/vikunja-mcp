"""Part 1 of the epic lifecycle (#118): next_task and claim skip epic CONTAINERS.

An epic (label `epic`) is a container, not a unit of work — its evidence lives in its
children, each claimed and reviewed on its own. next_task must not OFFER a free epic and
claim must not ACCEPT one. The guard keys STRICTLY off the `epic` label, never off the
presence of subtasks (the migration guard, same principle as the sequence gate): an ordinary
task may have a subtask and MUST stay claimable. The `mine`/`stuck` interaction is deliberate:
an epic a human placed in my ACTIVE lane (Design/Build) is NOT skipped (it's my active task),
while an epic sitting in Queue assigned to me IS skipped (it's unclaimable, so handing it back
would livelock the pump — stuck outranks the free queue)."""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


# --- next_task free-queue: skip epic containers (the #94 bug) ---

def test_next_task_skips_free_epic_and_offers_next_non_epic(env):
    """(a) A free epic in Queue is NOT offered; the next free non-epic task is — even though
    the epic outranks it by priority, so a high-priority epic can't starve claimable work."""
    api, wf = env
    epic = api.add_task("epic parent", "Queue", priority=9, labels=("epic",))
    real = api.add_task("real work", "Queue", priority=1)
    res = wf.next_task()
    assert res["task"]["id"] == real["id"] and res["task"]["id"] != epic["id"]
    assert res["resume"] is False


def test_next_task_returns_none_when_only_a_free_epic(env):
    """A lone free epic in Queue is skipped, not offered — the result is the empty-queue signal
    (task:null), never the epic handed out as work."""
    api, wf = env
    api.add_task("epic parent", "Queue", priority=5, labels=("epic",))
    assert wf.next_task()["task"] is None


def test_next_task_free_queue_unaffected_for_normal_task(env):
    """(g) A normal free card (no epic label) is offered exactly as before."""
    api, wf = env
    normal = api.add_task("plain", "Queue", priority=3)
    assert wf.next_task()["task"]["id"] == normal["id"]


def test_next_task_offers_free_task_that_merely_has_a_subtask(env):
    """The free-queue guard keys off the LABEL, not structure: a normal task that happens to
    have a subtask (a parenttask child) is still offered — only the `epic` label suppresses."""
    api, wf = env
    parent = api.add_task("has a child but not an epic", "Queue", priority=3)
    child = api.add_task("its child", "Backlog")
    api.add_relation(child["id"], parent["id"], "parenttask")  # parent gains subtask via inverse
    assert wf.next_task()["task"]["id"] == parent["id"]


def test_epic_skip_composes_with_blocked_and_assigned_skips(env):
    """epic sits beside a blocked card and a card assigned to someone else; the only offered
    task is the free non-epic one — the three free-queue exclusions compose."""
    api, wf = env
    api.add_task("epic", "Queue", priority=9, labels=("epic",))
    api.add_task("blocked", "Queue", priority=8, labels=("blocked",))
    api.add_task("someone else's", "Queue", priority=7,
                 assignee={"id": 9, "username": "other"})
    free = api.add_task("free", "Queue", priority=1)
    assert wf.next_task()["task"]["id"] == free["id"]


# --- claim: refuse epic containers ---

def test_claim_refuses_epic_container(env):
    """(b) claim on an epic refuses; the card is neither moved nor assigned (hard refusal),
    and the message explains WHY (a container, not a unit of work)."""
    api, wf = env
    epic = api.add_task("epic parent", "Queue", labels=("epic",))
    with pytest.raises(WorkflowError) as exc:
        wf.claim(epic["id"])
    msg = str(exc.value).lower()
    assert "epic" in msg and "container" in msg
    assert api.stage_of(epic["id"]) == "Queue"
    assert api.tasks[epic["id"]]["assignees"] == []


def test_claim_epic_refusal_points_at_children(env):
    """The refusal names the epic's children so the agent knows what to work on instead."""
    api, wf = env
    epic = api.add_task("epic parent", "Queue", labels=("epic",))
    c1 = api.add_task("child one", "Queue")
    c2 = api.add_task("child two", "Queue")
    api.add_relation(c1["id"], epic["id"], "parenttask")
    api.add_relation(c2["id"], epic["id"], "parenttask")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(epic["id"])
    msg = str(exc.value)
    assert c1["identifier"] in msg and c2["identifier"] in msg


def test_claim_refuses_childless_epic_gracefully(env):
    """An epic with no subtasks still refuses cleanly (generic fallback), never crashing on the
    empty child list."""
    api, wf = env
    epic = api.add_task("empty epic", "Queue", labels=("epic",))
    with pytest.raises(WorkflowError, match="container"):
        wf.claim(epic["id"])


def test_claim_allowed_for_task_with_subtask_but_no_epic_label(env):
    """(v) THE guard: a task that HAS a subtask (parenttask child) but is NOT labelled epic
    stays claimable. The refusal keys off the label, never the presence of subtasks."""
    api, wf = env
    parent = api.add_task("ordinary parent", "Queue")
    child = api.add_task("subtask", "Backlog")
    api.add_relation(child["id"], parent["id"], "parenttask")
    res = wf.claim(parent["id"])
    assert res["claimed"] is True
    assert api.stage_of(parent["id"]) == "Design"


def test_claim_normal_task_unaffected(env):
    """A plain task with no relations claims exactly as before — no regression."""
    api, wf = env
    t = api.add_task("plain", "Queue")
    assert wf.claim(t["id"])["claimed"] is True
    assert api.stage_of(t["id"]) == "Design"


# --- mine / stuck interaction (point d) ---

def test_next_task_does_not_skip_epic_that_is_my_active_task(env):
    """(d) An epic a human deliberately moved into my ACTIVE lane (Design/Build) and assigned to
    me is NOT skipped — it's my active task, handed back as resume. The epic skip is scoped to
    the free-queue OFFER and to claim; it must never false-skip a genuinely active card (that
    would strand it invisibly). This also preserves a human-assisted epic→Review path."""
    api, wf = env
    epic = api.add_task("epic in my build lane", "Build", assignee=api.me_user, labels=("epic",))
    res = wf.next_task()
    assert res["resume"] is True
    assert res["task"]["id"] == epic["id"]
    assert res["stage"] == "Build"


def test_next_task_skips_stuck_epic_assigned_to_me_and_offers_free(env):
    """(d, interaction) An epic sitting in QUEUE assigned to me is unclaimable (claim refuses
    epics), and the stuck branch outranks the free queue — so if next_task handed it back with a
    "call claim" note the pump would livelock on it and never drain real work. The stuck branch
    therefore skips epics, letting next_task fall through to the free non-epic task."""
    api, wf = env
    api.add_task("epic stuck on me", "Queue", assignee=api.me_user, priority=9, labels=("epic",))
    free = api.add_task("real free work", "Queue", priority=1)
    res = wf.next_task()
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False


def test_stuck_non_epic_assigned_to_me_still_resumes(env):
    """The stuck-epic skip must not swallow a genuine stuck claim: a NON-epic task in Queue
    assigned to me is still returned as a resume (call claim to finish moving it)."""
    api, wf = env
    stuck = api.add_task("half-claimed", "Queue", assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True
    assert res["stage"] == "Queue"
    assert res["task"]["id"] == stuck["id"]
