import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def _chain(api, pred_stage, succ_stage="Queue", kind="follows"):
    """Predecessor P (in pred_stage) + successor S (in succ_stage), S linked to P by `kind`
    (S follows / blocked-by P) — mirrors Vikunja's auto-inverse landing on S."""
    pred = api.add_task("predecessor", pred_stage)
    succ = api.add_task("successor", succ_stage)
    api.add_relation(succ["id"], pred["id"], kind)
    return pred, succ


def test_claim_refused_while_predecessor_unfinished(env):
    """Predecessor below Review (Build) -> successor can't be claimed; the refusal NAMES the
    predecessor and its stage, and the successor is neither moved nor assigned (hard refusal)."""
    api, wf = env
    pred, succ = _chain(api, pred_stage="Build")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert pred["identifier"] in msg
    assert "Build" in msg
    assert api.stage_of(succ["id"]) == "Queue"
    assert api.tasks[succ["id"]]["assignees"] == []


def test_claim_allowed_when_predecessor_at_review(env):
    """Human chose Review (not Done) as 'ready' so a chain drains autonomously: predecessor at
    Review -> successor claimable."""
    api, wf = env
    _pred, succ = _chain(api, pred_stage="Review")
    res = wf.claim(succ["id"])
    assert res["claimed"] is True
    assert api.stage_of(succ["id"]) == "Design"


def test_claim_allowed_when_predecessor_done(env):
    api, wf = env
    _pred, succ = _chain(api, pred_stage="Done")
    res = wf.claim(succ["id"])
    assert res["claimed"] is True
    assert api.stage_of(succ["id"]) == "Design"


def test_claim_not_gated_by_parenttask_only_migration_guard(env):
    """THE migration guard: an old epic's child carries only a `parenttask` link (parent in
    Backlog, below Review). The gate keys off follows/blocked exclusively, so the child stays
    claimable — existing subtasks must never silently lock."""
    api, wf = env
    parent = api.add_task("old epic", "Backlog", labels=("epic",))
    child = api.add_task("old subtask", "Queue")
    api.add_relation(child["id"], parent["id"], "parenttask")
    res = wf.claim(child["id"])
    assert res["claimed"] is True
    assert api.stage_of(child["id"]) == "Design"


def test_claim_gate_applies_to_blocked_relation(env):
    """`blocked` (S blocked-by P) is a predecessor kind like `follows`: an unfinished blocker
    below Review refuses the claim too, naming it."""
    api, wf = env
    pred, succ = _chain(api, pred_stage="Design", kind="blocked")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    assert pred["identifier"] in str(exc.value)
    assert api.stage_of(succ["id"]) == "Queue"


def test_claim_blocked_predecessor_ready_allows(env):
    """A `blocked` predecessor that reached Review no longer blocks."""
    api, wf = env
    _pred, succ = _chain(api, pred_stage="Review", kind="blocked")
    assert wf.claim(succ["id"])["claimed"] is True


def test_claim_refused_when_any_of_multiple_predecessors_unfinished(env):
    """Two predecessors, one ready (Review) one not (Build): one unfinished is enough to refuse,
    and the message names the UNFINISHED one, not the ready one."""
    api, wf = env
    ready = api.add_task("done-part", "Review")
    pending = api.add_task("still-going", "Build")
    succ = api.add_task("successor", "Queue")
    api.add_relation(succ["id"], ready["id"], "follows")
    api.add_relation(succ["id"], pending["id"], "blocked")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert pending["identifier"] in msg
    assert ready["identifier"] not in msg
    assert api.stage_of(succ["id"]) == "Queue"


def test_claim_allowed_when_all_predecessors_ready(env):
    api, wf = env
    p1 = api.add_task("p1", "Review")
    p2 = api.add_task("p2", "Done")
    succ = api.add_task("successor", "Queue")
    api.add_relation(succ["id"], p1["id"], "follows")
    api.add_relation(succ["id"], p2["id"], "blocked")
    assert wf.claim(succ["id"])["claimed"] is True


def test_your_call_predecessor_is_not_ready(env):
    """'Your Call' sorts AFTER Review in STAGES but is a parked question, NOT ready. Readiness
    must be an explicit set, not a positional 'at or past Review' check (which would wrongly
    pass Your Call)."""
    api, wf = env
    pred, succ = _chain(api, pred_stage="Your Call")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    assert pred["identifier"] in str(exc.value)
    assert api.stage_of(succ["id"]) == "Queue"


def test_claim_head_of_chain_with_only_outgoing_precedes_is_claimable(env):
    """The chain HEAD has an outgoing `precedes` relation (it precedes its successor) but no
    `follows`/`blocked` — no predecessor, so claimable. Guards against reading the wrong
    direction (precedes = successor, not predecessor)."""
    api, wf = env
    head = api.add_task("head", "Queue")
    tail = api.add_task("tail", "Queue")
    api.add_relation(head["id"], tail["id"], "precedes")
    res = wf.claim(head["id"])
    assert res["claimed"] is True
    assert api.stage_of(head["id"]) == "Design"


def test_unfinished_predecessors_helper_shape(env):
    """Helper returns unfinished predecessors with id/ref/title/stage; empty when none or only
    a parenttask link."""
    api, wf = env
    pred = api.add_task("p", "Build")
    succ = api.add_task("s", "Queue")
    api.add_relation(succ["id"], pred["id"], "follows")
    out = wf._unfinished_predecessors(succ["id"])
    assert [p["id"] for p in out] == [pred["id"]]
    assert out[0]["stage"] == "Build"
    assert out[0]["ref"].startswith(pred["identifier"])
    lone = api.add_task("lone", "Queue")
    api.add_relation(lone["id"], pred["id"], "parenttask")
    assert wf._unfinished_predecessors(lone["id"]) == []


def test_unfinished_predecessor_deduped_across_kinds(env):
    """A predecessor linked via BOTH follows and blocked is reported once, not twice."""
    api, wf = env
    pred = api.add_task("p", "Design")
    succ = api.add_task("s", "Queue")
    api.add_relation(succ["id"], pred["id"], "follows")
    api.add_relation(succ["id"], pred["id"], "blocked")
    out = wf._unfinished_predecessors(succ["id"])
    assert [p["id"] for p in out] == [pred["id"]]
