"""The predecessor gate must see a predecessor that lives in ANOTHER project (#1179).

Vikunja relations are task-to-task and cross project boundaries freely — measured on a real
2.3.0: a task moved from one project to another kept its `blocked` relation to a task left
behind in the source. So a card CAN be blocked by a card on another board, and before this
the gate could not see it: `_unfinished_predecessors` resolved stages against THIS project's
board only, and anything missing from it fell into the "genuinely gone -> not a blocker"
branch together with the actually-deleted.

Measured before the fix, with a same-project control in the same round:

    control (blocker in Build, SAME project):     claim REFUSED, next_task withheld
    round   (blocker in Build, SIBLING project):  claim ALLOWED, next_task OFFERED

The control refusing is what says the probe measured anything at all.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def _sibling_blocker(api, stage="Build", forbidden=False, title="add the endpoint"):
    """A task on ANOTHER project's board, parked in `stage`. Returns the task dict."""
    # built readable, then closed: the board has to be populated before the token loses
    # access to it, exactly as it happens in life (a project is shared, then unshared).
    proj = api.add_project("dogiators-backend", buckets=STAGES, identifier="BACK")
    entry = api.other_projects[proj["id"]]
    task = api.create_task(proj["id"], title)
    bucket = next(b for b in entry["buckets"] if b["title"] == stage)
    api.move_task(proj["id"], entry["view"]["id"], bucket["id"], task["id"])
    if forbidden:
        api._forbidden.add(proj["id"])
    return proj, task


def _blocked_card(api, blocker_id, stage="Queue"):
    succ = api.add_task("front card", stage)
    api.add_relation(succ["id"], blocker_id, "blocked")
    return succ


def test_claim_refused_while_a_sibling_project_predecessor_is_unfinished(env):
    """THE defect. Blocker sits in the sibling's Build — below Review — so the card is not
    claimable, and the refusal names the blocker so a human can go look at it."""
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Build")
    succ = _blocked_card(api, blocker["id"])
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert blocker["identifier"] in msg
    assert "Build" in msg
    # hard refusal: nothing moved, nothing assigned
    assert api.stage_of(succ["id"]) == "Queue"
    assert api.tasks[succ["id"]]["assignees"] == []


def test_next_task_withholds_a_card_blocked_across_projects(env):
    """The same verdict on the offer side — otherwise the pump hands out a card whose
    blocker nobody has touched, and the agent discovers it only by reading the card."""
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Build")
    _blocked_card(api, blocker["id"])
    assert wf.next_task().get("task") is None


def test_a_sibling_predecessor_at_review_releases_the_card(env):
    """READY_STAGES is one definition, not one per project: Review on the neighbour's board
    means ready here too, so a chain drains across repos without a human in the middle."""
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Review")
    succ = _blocked_card(api, blocker["id"])
    assert wf.claim(succ["id"])["claimed"] is True


def test_a_sibling_predecessor_that_is_done_releases_the_card(env):
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Done")
    succ = _blocked_card(api, blocker["id"])
    assert wf.claim(succ["id"])["claimed"] is True


def test_a_predecessor_that_exists_nowhere_still_does_not_block(env):
    """#126's semantics, preserved: absent from every board AND unfetchable means deleted,
    and a deleted predecessor must not lock its successor out forever."""
    api, wf = env
    succ = api.add_task("front card", "Queue")
    api.add_relation(succ["id"], 999999, "blocked")
    assert wf.claim(succ["id"])["claimed"] is True


def test_an_unreadable_sibling_predecessor_blocks_rather_than_vanishes(env):
    """Fail CLOSED. The token cannot see the neighbour's board (403), so the predecessor's
    stage is UNKNOWN — and unknown must not be spelled "gone", which is exactly the silent
    wrong answer this whole card is about. Noisy beats quiet: refuse, and say why."""
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Build", forbidden=True)
    succ = _blocked_card(api, blocker["id"])
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert str(blocker["id"]) in msg
    assert "403" in msg or "access" in msg.lower()


def test_the_waiting_signal_names_a_cross_project_blocker(env):
    """next_task's "everything is blocked" signal must name the off-board blocker too —
    it is the only place a human is told WHY the queue looks empty."""
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Build")
    _blocked_card(api, blocker["id"])
    res = wf.next_task()
    waiting = res.get("waiting") or []
    blockers = [b for w in waiting for b in (w.get("blocked_by") or [])]
    assert any(b.get("id") == blocker["id"] for b in blockers), res
