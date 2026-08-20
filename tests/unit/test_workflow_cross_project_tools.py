"""`handoff` and `transfer_task` — the two ways a card crosses a project boundary (#1179).

They are two tools because they answer two different questions, and collapsing them would
lose one of the answers:

  handoff        "this card needs work that belongs to ANOTHER repo before it can continue"
                 -> a NEW card over there, THIS one parked and blocked on it
  transfer_task  "this card was filed on the wrong board"
                 -> THIS card moves, history and all; nothing stays behind

Both land in the target's BACKLOG, never its Queue: that project's human triages their own
board, and an agent from a neighbouring repo must not inject ready-for-pickup work into it.
That is the same rule `file_task` already enforces for its cross-project branch.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

BACKEND = "backend"


@pytest.fixture
def env():
    """Front project (id 3) with a `backend` sibling whose board is tracker-shaped."""
    api = FakeAPI(buckets=STAGES)
    proj = api.add_project("dogiators-backend", buckets=STAGES, identifier="BACK")
    wf = Workflow(api, project_id=3, siblings={BACKEND: proj["id"]})
    return api, wf, proj


def _mine(api, stage="Build", labels=()):
    """A card of mine, claimed and in an active stage."""
    return api.add_task("front card", stage, assignee=api.me_user, labels=labels)


# --------------------------------------------------------------------- handoff

def test_handoff_files_the_new_card_into_the_neighbours_backlog(env):
    api, wf, proj = env
    task = _mine(api)
    res = wf.handoff(task["id"], to=BACKEND, title="add GET /fights/{id}/replay")
    new_id = res["filed"]["id"]
    assert api.tasks[new_id]["project_id"] == proj["id"]
    assert api.stage_of(new_id) == "Backlog"


def test_handoff_blocks_the_current_card_on_the_new_one(env):
    """The link is the point: without it the card would be parked with nothing to wake it."""
    api, wf, _proj = env
    task = _mine(api)
    res = wf.handoff(task["id"], to=BACKEND, title="add the endpoint")
    blockers = api.get_task(task["id"])["related_tasks"].get("blocked") or []
    assert [b["id"] for b in blockers] == [res["filed"]["id"]]


def test_handoff_parks_the_current_card_in_queue_unassigned(env):
    """Queue, not Backlog, and deliberately WITHOUT the `blocked` label. The predecessor gate
    already withholds the card while its blocker is unfinished and releases it the moment the
    blocker reaches Review — so the chain resumes by itself. The `blocked` LABEL would defeat
    exactly that: it means "a human must look" and suppresses the offer permanently."""
    api, wf, _proj = env
    task = _mine(api)
    wf.handoff(task["id"], to=BACKEND, title="add the endpoint")
    assert api.stage_of(task["id"]) == "Queue"
    assert api.tasks[task["id"]]["assignees"] == []
    assert [lb["title"] for lb in api.tasks[task["id"]]["labels"]] == []


def test_the_parked_card_is_withheld_until_the_blocker_is_ready(env):
    """End to end, through the gate this card also fixed: parked -> not offered; blocker at
    Review -> offered again, with no human in between."""
    api, wf, proj = env
    task = _mine(api)
    res = wf.handoff(task["id"], to=BACKEND, title="add the endpoint")
    assert wf.next_task().get("task") is None

    entry = api.other_projects[proj["id"]]
    review = next(b for b in entry["buckets"] if b["title"] == "Review")
    api.move_task(proj["id"], entry["view"]["id"], review["id"], res["filed"]["id"])
    offered = wf.next_task().get("task")
    assert offered and offered["id"] == task["id"]


def test_handoff_refuses_an_unknown_sibling_name_and_changes_nothing(env):
    api, wf, _proj = env
    task = _mine(api)
    before = len(api.tasks)
    with pytest.raises(WorkflowError) as exc:
        wf.handoff(task["id"], to="frontend", title="x")
    assert BACKEND in str(exc.value)          # the refusal lists what IS addressable
    assert len(api.tasks) == before
    assert api.stage_of(task["id"]) == "Build"


def test_handoff_refuses_when_the_registry_is_empty_and_says_where_to_add_one(env):
    api, _wf, _proj = env
    wf = Workflow(api, project_id=3)          # no siblings configured
    task = _mine(api)
    with pytest.raises(WorkflowError) as exc:
        wf.handoff(task["id"], to=BACKEND, title="x")
    assert "siblings" in str(exc.value)


def test_handoff_refuses_from_a_stage_that_is_not_mine_to_pause(env):
    api, wf, _proj = env
    task = api.add_task("someone else's queued card", "Queue")
    with pytest.raises(WorkflowError):
        wf.handoff(task["id"], to=BACKEND, title="x")


def test_handoff_resolves_the_target_board_before_creating_anything(env):
    """Fail-fast, the `file_task` rule: an unreachable target must not leave a half-done
    handoff — no orphan card over there, no parked card over here."""
    api, wf, proj = env
    api._forbidden.add(proj["id"])
    task = _mine(api)
    before = len(api.tasks)
    with pytest.raises(WorkflowError):
        wf.handoff(task["id"], to=BACKEND, title="x")
    assert len(api.tasks) == before
    assert api.stage_of(task["id"]) == "Build"
    assert api.tasks[task["id"]]["assignees"] == [api.me_user]


def test_handoff_marks_both_cards(env):
    api, wf, _proj = env
    task = _mine(api)
    res = wf.handoff(task["id"], to=BACKEND, title="add the endpoint", description="why")
    assert any(c.startswith("[handoff]") for c in api.comments_text(task["id"]))
    assert api.comments_text(res["filed"]["id"])          # provenance on the new card


def test_handoff_requires_a_title(env):
    api, wf, _proj = env
    task = _mine(api)
    with pytest.raises(WorkflowError):
        wf.handoff(task["id"], to=BACKEND, title="   ")


# ---------------------------------------------------------------- transfer_task

def test_transfer_moves_the_card_into_the_target_backlog(env):
    api, wf, proj = env
    task = _mine(api)
    wf.transfer_task(task["id"], to=BACKEND, reason="pure backend work, filed on the wrong board")
    assert api.tasks[task["id"]]["project_id"] == proj["id"]
    assert api.stage_of(task["id"]) == "Backlog"


def test_transfer_hands_back_the_new_ref_because_the_old_one_stops_resolving(env):
    """Measured on real 2.3.0: a card moved into a project is RE-INDEXED by the target's own
    counter (FRNT-2 -> BACK-3), so every ref quoted in an earlier comment or commit is dead.
    The tool has to hand back the new one and say so, or the agent echoes a ref that now
    points at a different card — or at nothing."""
    api, wf, _proj = env
    task = _mine(api)
    old_ref = task["identifier"]
    res = wf.transfer_task(task["id"], to=BACKEND, reason="wrong board")
    assert res["moved"]["ref"] != old_ref
    assert res["moved"]["ref"] == api.get_task(task["id"])["identifier"] + f" ({task['id']})"
    assert "ref" in (res.get("note") or "").lower()


def test_transfer_unassigns_and_clears_stage_labels(env):
    """It lands in someone else's Backlog: the claim is void, and a `reviewed` verdict earned
    on the old board must not travel with it."""
    api, wf, _proj = env
    task = _mine(api, labels=("reviewed", "blocked"))
    wf.transfer_task(task["id"], to=BACKEND, reason="wrong board")
    assert api.tasks[task["id"]]["assignees"] == []
    assert [lb["title"] for lb in api.tasks[task["id"]]["labels"]] == []


def test_transfer_refuses_a_card_in_done(env):
    """Done is the human's territory everywhere else in this module; moving a finished card
    off the board it was finished on is not an agent's call."""
    api, wf, _proj = env
    task = api.add_task("finished", "Done")
    with pytest.raises(WorkflowError):
        wf.transfer_task(task["id"], to=BACKEND, reason="x")


def test_transfer_refuses_an_epic_with_children(env):
    """An epic's code lives in its children. Moving the container alone splits it across two
    boards and leaves the children pointing at a parent nobody there can see."""
    api, wf, _proj = env
    parent = api.add_task("epic", "Queue", labels=("epic",))
    child = api.add_task("child", "Queue")
    api.add_relation(child["id"], parent["id"], "parenttask")
    with pytest.raises(WorkflowError) as exc:
        wf.transfer_task(parent["id"], to=BACKEND, reason="x")
    assert "epic" in str(exc.value).lower()


def test_transfer_resolves_the_target_board_before_touching_the_card(env):
    api, wf, proj = env
    api._forbidden.add(proj["id"])
    task = _mine(api)
    with pytest.raises(WorkflowError):
        wf.transfer_task(task["id"], to=BACKEND, reason="x")
    assert api.tasks[task["id"]]["project_id"] == 3
    assert api.stage_of(task["id"]) == "Build"


def test_transfer_records_where_the_card_came_from(env):
    """Provenance for the humans on the TARGET board, who are about to meet a card with a
    comment history that happened somewhere else."""
    api, wf, _proj = env
    task = _mine(api)
    wf.transfer_task(task["id"], to=BACKEND, reason="pure backend work")
    moved = [c for c in api.comments_text(task["id"]) if c.startswith("[moved]")]
    assert moved and "3" in moved[-1] and "pure backend work" in moved[-1]


def test_transfer_requires_a_reason(env):
    api, wf, _proj = env
    task = _mine(api)
    with pytest.raises(WorkflowError):
        wf.transfer_task(task["id"], to=BACKEND, reason="  ")


# ------------------------------------------------- discoverability of the registry

def test_next_task_carries_the_siblings_registry(env):
    """The whole reason the registry exists. An agent cannot read the repo toml, so without
    this it has no way to learn a neighbour is there at all — which is exactly the state
    dogiators-front was in: its toml named neither `backend` nor 17."""
    api, wf, proj = env
    assert wf.next_task()["siblings"] == {BACKEND: proj["id"]}      # empty queue
    api.add_task("queued", "Queue")
    assert wf.next_task()["siblings"] == {BACKEND: proj["id"]}      # and with an offer


def test_next_task_carries_an_empty_registry_when_none_is_configured():
    api = FakeAPI(buckets=STAGES)
    assert Workflow(api, project_id=3).next_task()["siblings"] == {}


# ------------------------------------------------- stages a card may NOT be carried out of

def test_transfer_refuses_a_card_in_review(env):
    """Out of Review a card is walked by exactly ONE agent tool, review_task(needs_work) —
    an invariant `test_exactly_ONE_agent_tool_walks_a_card_out_of_Review` pins. A card in
    Review has a verdict pending on THIS board; carrying it to another project would strand
    that verdict. A reviewer who thinks the card belongs elsewhere sends it back with
    needs_work, and its implementer transfers it from Build."""
    api, wf, _proj = env
    task = api.add_task("under review", "Review")
    with pytest.raises(WorkflowError) as exc:
        wf.transfer_task(task["id"], to=BACKEND, reason="wrong board")
    assert "Review" in str(exc.value)
    assert api.stage_of(task["id"]) == "Review"
    assert api.tasks[task["id"]]["project_id"] == 3


def test_transfer_refuses_a_card_parked_in_your_call(env):
    """A parked question is addressed to a human on THIS board. Moving the card takes the
    question somewhere they are not looking."""
    api, wf, _proj = env
    task = api.add_task("parked", "Your Call", assignee=api.me_user)
    with pytest.raises(WorkflowError):
        wf.transfer_task(task["id"], to=BACKEND, reason="wrong board")
    assert api.stage_of(task["id"]) == "Your Call"


def test_transfer_works_from_the_stages_before_review(env):
    """The misfile is normally spotted before or during the work, so all four of those
    stages are open — including Backlog and Queue, where nobody has claimed it yet."""
    api, wf, proj = env
    for stage in ("Backlog", "Queue", "Design", "Build"):
        task = api.add_task(f"misfiled in {stage}", stage)
        wf.transfer_task(task["id"], to=BACKEND, reason="wrong board")
        assert api.tasks[task["id"]]["project_id"] == proj["id"], stage


def test_handoff_clears_a_stale_verdict(env):
    """The card leaves the active pipeline for Queue, so any verdict on it is stale — same
    family as return_task and decompose, which both clear (#693). Left standing, the board
    would show a card parked on a dependency and simultaneously labelled `review-failed`,
    and the next reader cannot tell which of the two is the live fact."""
    api, wf, _proj = env
    task = _mine(api, labels=("review-failed",))
    wf.handoff(task["id"], to=BACKEND, title="add the endpoint")
    assert _titles(api, task["id"]) == []


def _titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]
