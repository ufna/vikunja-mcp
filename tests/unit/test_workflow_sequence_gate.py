import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.config import DEFAULT_LANGUAGE, DEFAULT_WIP_LIMIT
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


# --- C2 (#102): next_task filters gated successors + reports the starving tail ---

# wip is on every next_task() result now (parallel-drain slot accounting, tracker #250); the
# `env` fixture builds an unconfigured Workflow (no wip_limit), which since tracker #524 means
# the DEFAULT limit rather than no limit at all — so it's always this shape here, and with zero
# active tasks every slot is free.
# `language` rides beside `wip` on every next_task result since tracker #1165 — same wrapper,
# same reason (project policy the agent cannot read off the board), and the `env` fixture builds
# an unconfigured Workflow, so it is the default.
EMPTY = {
    "task": None, "message": "the queue is empty — no work for the agent",
    "wip": {"active": 0, "limit": DEFAULT_WIP_LIMIT, "free": DEFAULT_WIP_LIMIT},
    "language": DEFAULT_LANGUAGE,
    # #1179: the neighbour registry rides in EVERY payload beside wip/language, empty one
    # included — a project with no siblings configured says so rather than omitting the key,
    # so an agent never has to tell "no neighbours" apart from "this build has no such key".
    "siblings": {},
}


def test_next_task_skips_gated_offers_ungated_free(env):
    """A gated successor (predecessor in Build) is skipped even though it has HIGHER priority;
    a separate ungated free task is offered instead — the gate beats -priority, not the reverse."""
    api, wf = env
    pred = api.add_task("pred", "Build")
    gated = api.add_task("gated", "Queue", priority=5)          # higher priority, but blocked
    api.add_relation(gated["id"], pred["id"], "follows")
    free = api.add_task("free", "Queue", priority=1)
    res = wf.next_task()
    assert res["resume"] is False
    assert res["task"]["id"] == free["id"]                      # not the gated higher-priority one


def test_next_task_offers_successor_when_predecessor_ready_at_review(env):
    """A successor whose only predecessor reached Review is ungated -> offered for claim (Review
    is 'ready' so the chain drains autonomously)."""
    api, wf = env
    _pred, succ = _chain(api, pred_stage="Review")
    res = wf.next_task()
    assert res["resume"] is False
    assert res["task"]["id"] == succ["id"]


def test_next_task_parenttask_only_offered_migration_guard(env):
    """Migration guard on the queue path: an old epic's child in Queue carries ONLY a parenttask
    link (parent in Backlog, below Review). The gate keys off follows/blocked, so the child stays
    OFFERED — this is what keeps the live dogfood queue (#103-105 carry only parenttask) alive."""
    api, wf = env
    parent = api.add_task("epic", "Backlog", labels=("epic",))
    child = api.add_task("child", "Queue", priority=3)
    api.add_relation(child["id"], parent["id"], "parenttask")
    res = wf.next_task()
    assert res["resume"] is False
    assert res["task"]["id"] == child["id"]


def test_next_task_all_gated_returns_starving_signal_not_empty(env):
    """Free Queue NON-empty but EVERY candidate gated -> the distinguishable starving-tail signal,
    NOT the empty-queue result: task None, starving True, waiting_count = N, and each waiting task
    named with the predecessor blocking it (by ref and stage)."""
    api, wf = env
    p1 = api.add_task("p1", "Build")
    s1 = api.add_task("s1", "Queue", priority=2)
    api.add_relation(s1["id"], p1["id"], "follows")
    p2 = api.add_task("p2", "Design")
    s2 = api.add_task("s2", "Queue", priority=1)
    api.add_relation(s2["id"], p2["id"], "blocked")
    res = wf.next_task()
    assert res["task"] is None
    assert res["starving"] is True
    assert res["needs_retriage"] is False                      # neither blocker is in Backlog
    assert res["waiting_count"] == 2
    assert res != EMPTY and res["message"] != EMPTY["message"]
    assert {w["task"]["id"] for w in res["waiting"]} == {s1["id"], s2["id"]}
    blocker_refs = [b["ref"] for w in res["waiting"] for b in w["blocked_by"]]
    assert any(p1["identifier"] in r for r in blocker_refs)
    assert any(p2["identifier"] in r for r in blocker_refs)
    assert "Build" in res["message"] and "Design" in res["message"]


def test_next_task_genuinely_empty_queue_unchanged(env):
    """Nothing to claim AND nothing gated -> the pre-existing empty signal, byte-for-byte, with
    NO starving discriminators. 'nothing to do' must stay distinct from 'everything blocked'."""
    api, wf = env
    res = wf.next_task()
    assert res == EMPTY
    assert "starving" not in res and "waiting_count" not in res


def test_next_task_only_gated_task_is_starving_not_empty(env):
    """A single gated free task (no ungated alternative) is a starving tail, NOT an empty queue —
    the guard against the silent stall: one blocked successor must not read as 'no work'."""
    api, wf = env
    _pred, succ = _chain(api, pred_stage="Build")
    res = wf.next_task()
    assert res["task"] is None
    assert res["starving"] is True
    assert res["waiting_count"] == 1
    assert res["waiting"][0]["task"]["id"] == succ["id"]


def test_next_task_returned_head_in_backlog_flags_retriage(env):
    """THE special case: the chain HEAD was sent back to Backlog via return_task (label blocked,
    assignee cleared). Its tail (a Queue successor) is the only free candidate and is gated -> a
    starving signal that NAMES the re-triage situation (needs_retriage + message), never a mystery
    stall. The blocker is reported with its id/ref/Backlog stage."""
    api, wf = env
    head = api.add_task("head", "Backlog", labels=("blocked",))   # returned via return_task
    tail = api.add_task("tail", "Queue")
    api.add_relation(tail["id"], head["id"], "follows")
    res = wf.next_task()
    assert res["task"] is None
    assert res["starving"] is True
    assert res["needs_retriage"] is True
    assert res["waiting_count"] == 1
    w = res["waiting"][0]
    assert w["task"]["id"] == tail["id"]
    assert w["needs_retriage"] is True
    blk = w["blocked_by"][0]
    assert blk["id"] == head["id"] and blk["stage"] == "Backlog"
    assert head["identifier"] in blk["ref"]
    assert "re-triage" in res["message"].lower() and "Backlog" in res["message"]


def test_next_task_mine_active_beats_gated_queue(env):
    """Precedence intact: the free-queue sequence gate is the LAST branch, so my active
    Design/Build task still comes first even when the whole free queue is starving."""
    api, wf = env
    mine = api.add_task("my active", "Build", assignee=api.me_user, priority=1)
    _pred, _succ = _chain(api, pred_stage="Build")               # a gated free successor in Queue
    res = wf.next_task()
    assert res["resume"] is True
    assert res["task"]["id"] == mine["id"]


def test_next_task_stuck_assigned_beats_gated_queue(env):
    """Precedence intact: a Queue task assigned to me (partial/human-directed claim) is handled by
    the stuck branch, ahead of the free-queue gate — a starving free queue can't jump it."""
    api, wf = env
    stuck = api.add_task("assigned to me", "Queue", assignee=api.me_user, priority=1)
    _pred, _succ = _chain(api, pred_stage="Build")
    res = wf.next_task()
    assert res["resume"] is True and res["stage"] == "Queue"
    assert res["task"]["id"] == stuck["id"]


def test_next_task_bug_review_beats_gated_queue(env):
    """Precedence intact: a bug fix awaiting independent review (branch 3) outranks the free-queue
    gate (branch 4) — offered first even when the free queue is starving."""
    api, wf = env
    bug = api.add_task("bug fix", "Review", labels=("bug",))
    api.add_comment(bug["id"], "[worklog] fixed")               # report awaiting review, no verdict
    _pred, _succ = _chain(api, pred_stage="Build")               # gated free successor in Queue
    res = wf.next_task()
    assert res.get("review") is True
    assert res["task"]["id"] == bug["id"]


# --- C3 (#103): advance→review latch + rework-first mine ordering ---


def test_advance_review_latched_by_unfinished_predecessor(env):
    """Predecessor below Review (Build) -> advance(successor, 'review') refused; message names
    the predecessor and its stage, the successor is NOT moved, and NO worklog was posted (the
    latch fires before the report is written)."""
    api, wf = env
    pred = api.add_task("pred", "Build")
    succ = api.add_task("succ", "Build", assignee=api.me_user)
    api.add_relation(succ["id"], pred["id"], "follows")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(succ["id"], to="review", worklog="done", evidence="sha")
    msg = str(exc.value)
    assert pred["identifier"] in msg
    assert "Build" in msg
    assert api.stage_of(succ["id"]) == "Build"
    assert not any(c.startswith("[worklog]") for c in api.comments_text(succ["id"]))


def test_advance_review_allowed_when_predecessor_at_review(env):
    """Predecessor at Review is 'ready' -> the successor may advance to Review."""
    api, wf = env
    pred = api.add_task("pred", "Review")
    succ = api.add_task("succ", "Build", assignee=api.me_user)
    api.add_relation(succ["id"], pred["id"], "follows")
    res = wf.advance(succ["id"], to="review", worklog="done", evidence="sha")
    assert res["moved_to"] == "Review"
    assert api.stage_of(succ["id"]) == "Review"


def test_advance_review_allowed_when_predecessor_done(env):
    api, wf = env
    pred = api.add_task("pred", "Done")
    succ = api.add_task("succ", "Build", assignee=api.me_user)
    api.add_relation(succ["id"], pred["id"], "follows")
    assert wf.advance(succ["id"], to="review", worklog="d", evidence="s")["moved_to"] == "Review"


def test_advance_review_not_latched_by_parenttask_only(env):
    """Migration guard on the latch: a parenttask-only link (old epic child) never latches
    advance->review."""
    api, wf = env
    parent = api.add_task("epic", "Backlog", labels=("epic",))
    child = api.add_task("child", "Build", assignee=api.me_user)
    api.add_relation(child["id"], parent["id"], "parenttask")
    assert wf.advance(child["id"], to="review", worklog="d", evidence="s")["moved_to"] == "Review"


def test_advance_review_latched_by_blocked_relation(env):
    """`blocked` predecessor below Review latches advance->review too (parity with follows)."""
    api, wf = env
    pred = api.add_task("pred", "Design")
    succ = api.add_task("succ", "Build", assignee=api.me_user)
    api.add_relation(succ["id"], pred["id"], "blocked")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(succ["id"], to="review", worklog="d", evidence="s")
    assert pred["identifier"] in str(exc.value)
    assert api.stage_of(succ["id"]) == "Build"


def test_advance_to_build_unaffected_by_sequence_latch(env):
    """The latch applies ONLY to to='review': to='build' advances even with an unfinished
    predecessor below Review — you may keep working the successor, you just can't land it."""
    api, wf = env
    pred = api.add_task("pred", "Build")
    succ = api.add_task("succ", "Design", assignee=api.me_user)
    api.add_relation(succ["id"], pred["id"], "follows")
    res = wf.advance(succ["id"], to="build", spec="approach")
    assert res["moved_to"] == "Build"
    assert api.stage_of(succ["id"]) == "Build"


def test_mine_orders_predecessor_before_successor_over_priority(env):
    """rework-first: with two of MY active tasks in a chain, the predecessor is handed back
    first EVEN THOUGH the successor has strictly higher priority (proves the chain rule
    OVERRIDES -priority — it is not passing because priority happens to agree)."""
    api, wf = env
    pred = api.add_task("pred low prio", "Build", assignee=api.me_user, priority=1)
    succ = api.add_task("succ high prio", "Design", assignee=api.me_user, priority=5)
    api.add_relation(succ["id"], pred["id"], "follows")
    res = wf.next_task()
    assert res["resume"] is True
    assert res["task"]["id"] == pred["id"]


def test_mine_two_unrelated_active_order_by_priority(env):
    """No chain link between my two active tasks -> plain -priority order is preserved."""
    api, wf = env
    _a = api.add_task("a", "Build", assignee=api.me_user, priority=2)
    b = api.add_task("b", "Build", assignee=api.me_user, priority=5)
    res = wf.next_task()
    assert res["resume"] is True
    assert res["task"]["id"] == b["id"]


def test_bounce_scenario_end_to_end(env):
    """The exact case the human asked about. P reaches Review -> S unlocks and is claimed ->
    P is bounced Review->Build -> advance(S,'review') is LATCHED, next_task hands back P
    (predecessor) before S despite S's higher priority, P is reworked back to Review, and only
    THEN advance(S,'review') succeeds."""
    api, wf = env
    p = api.add_task("P predecessor", "Build", assignee=api.me_user, priority=1)
    s = api.add_task("S successor", "Queue", priority=5)
    api.add_relation(s["id"], p["id"], "follows")
    # 1. P -> Review; S unlocks
    wf.advance(p["id"], to="review", worklog="did P", evidence="sha-p")
    assert api.stage_of(p["id"]) == "Review"
    # 2. claim S (predecessor ready at Review) and move it into Build
    wf.claim(s["id"])
    assert api.stage_of(s["id"]) == "Design"
    wf.advance(s["id"], to="build", spec="approach S")
    assert api.stage_of(s["id"]) == "Build"
    # 3. P bounced Review -> Build (simulate a human/review return)
    api.move_task(3, api.view["id"], api.bucket_id("Build"), p["id"])
    assert api.stage_of(p["id"]) == "Build"
    # 4. advance(S,'review') is now latched
    with pytest.raises(WorkflowError) as exc:
        wf.advance(s["id"], to="review", worklog="did S", evidence="sha-s")
    assert p["identifier"] in str(exc.value)
    assert "Build" in str(exc.value)
    assert api.stage_of(s["id"]) == "Build"
    # 5. next_task hands back P (predecessor) before S, despite S's higher priority
    nxt = wf.next_task()
    assert nxt["resume"] is True
    assert nxt["task"]["id"] == p["id"]
    # 6. rework P back to Review
    wf.advance(p["id"], to="review", worklog="reworked P", evidence="sha-p2")
    assert api.stage_of(p["id"]) == "Review"
    # 7. now S may advance to Review
    res = wf.advance(s["id"], to="review", worklog="did S", evidence="sha-s")
    assert res["moved_to"] == "Review"
    assert api.stage_of(s["id"]) == "Review"


# --- C4 (#104): decompose(ordered=True) chains children head→tail (precedes/follows) ---


def _ordered_parent(api):
    """A parent task in Design assigned to me — the precondition decompose enforces
    (_require_mine). decompose(ordered=True) then chains the created children."""
    return api.add_task("epic parent", "Design", assignee=api.me_user)


def test_decompose_ordered_chains_children_follows_head_to_tail(env):
    """ordered=True writes `precedes` on child[i]→child[i+1] in ARRAY ORDER, which the fake
    (like real Vikunja 2.3.0) auto-inverts into `follows` on each successor. So every successor
    reports its immediate predecessor as an unfinished blocker while the HEAD has none — and the
    parenttask links + parent finalization (Backlog + epic) are all still there."""
    api, wf = env
    parent = _ordered_parent(api)
    res = wf.decompose(
        parent["id"],
        subtasks=[{"title": "one"}, {"title": "two"}, {"title": "three"}],
        ordered=True,
    )
    created = res["created"]
    assert len(created) == 3
    assert res.get("ordered") is True                       # additive marker on the ordered path
    # precedes written in ARRAY ORDER: child[i] precedes child[i+1] (the load-bearing direction)
    for i in range(len(created) - 1):
        assert (created[i]["id"], created[i + 1]["id"], "precedes") in api.relations
    # each successor sees its immediate predecessor as an unfinished blocker (the follows inverse)
    for i in range(len(created) - 1):
        preds = wf._unfinished_predecessors(created[i + 1]["id"])
        assert [p["id"] for p in preds] == [created[i]["id"]]
    # the HEAD has no predecessor -> claimable now
    assert wf._unfinished_predecessors(created[0]["id"]) == []
    # children still carry the parenttask link to the parent
    for c in created:
        assert (c["id"], parent["id"], "parenttask") in api.relations
    # parent finalized exactly as an epic: Backlog + epic label
    assert api.stage_of(parent["id"]) == "Backlog"
    assert any(lb["title"] == "epic" for lb in api.tasks[parent["id"]]["labels"])


def test_decompose_ordered_head_claimable_tail_gated(env):
    """THE direction guard: after an ordered decompose the HEAD is immediately claimable, but the
    next child is REFUSED and the refusal NAMES the head. This proves the chain is enforced
    FORWARD — a backwards chain would free the tail and gate the head (the silent-corruption bug)."""
    api, wf = env
    parent = _ordered_parent(api)
    created = wf.decompose(
        parent["id"], subtasks=[{"title": "head"}, {"title": "tail"}], ordered=True
    )["created"]
    # head claimable -> moves to Design
    assert wf.claim(created[0]["id"])["claimed"] is True
    assert api.stage_of(created[0]["id"]) == "Design"
    # tail gated: refusal names the head, and the tail is neither moved nor assigned
    with pytest.raises(WorkflowError) as exc:
        wf.claim(created[1]["id"])
    assert api.tasks[created[0]["id"]]["identifier"] in str(exc.value)
    assert api.stage_of(created[1]["id"]) == "Queue"
    assert api.tasks[created[1]["id"]]["assignees"] == []


def test_decompose_ordered_tail_unlocks_after_head_reaches_review(env):
    """The chain drains autonomously: once the head reaches Review (the 'ready' bar), the next
    child unlocks and becomes claimable."""
    api, wf = env
    parent = _ordered_parent(api)
    created = wf.decompose(
        parent["id"], subtasks=[{"title": "head"}, {"title": "tail"}], ordered=True
    )["created"]
    # drive the head all the way to Review
    wf.claim(created[0]["id"])
    wf.advance(created[0]["id"], to="build", spec="approach")
    wf.advance(created[0]["id"], to="review", worklog="did head", evidence="sha")
    assert api.stage_of(created[0]["id"]) == "Review"
    # now the tail unlocks
    assert wf.claim(created[1]["id"])["claimed"] is True
    assert api.stage_of(created[1]["id"]) == "Design"


def test_decompose_unordered_adds_no_precedes_regression_guard(env):
    """Migration / byte-for-byte guard: plain decompose (ordered omitted) writes NO precedes,
    keeps the parenttask links, leaves every child claimable, and returns the IDENTICAL dict shape
    (no additive ordered/note keys)."""
    api, wf = env
    parent = _ordered_parent(api)
    res = wf.decompose(parent["id"], subtasks=[{"title": "a"}, {"title": "b"}])
    created = res["created"]
    assert not any(kind == "precedes" for _t, _o, kind in api.relations)
    for c in created:
        assert (c["id"], parent["id"], "parenttask") in api.relations
        assert wf._unfinished_predecessors(c["id"]) == []       # all claimable, nothing gated
    # return shape is byte-for-byte unchanged: exactly {created, parent}, no ordered/note
    assert set(res) == {"created", "parent"}
    assert res["parent"] == {"id": parent["id"], "moved_to": "Backlog", "labeled": "epic"}


def test_decompose_ordered_false_explicit_adds_no_precedes(env):
    """ordered=False passed explicitly behaves exactly like ordered omitted: no precedes, same
    dict shape."""
    api, wf = env
    parent = _ordered_parent(api)
    res = wf.decompose(parent["id"], subtasks=[{"title": "a"}, {"title": "b"}], ordered=False)
    assert not any(kind == "precedes" for _t, _o, kind in api.relations)
    assert set(res) == {"created", "parent"}
    assert res["parent"] == {"id": parent["id"], "moved_to": "Backlog", "labeled": "epic"}


def test_decompose_ordered_single_child_rejected_no_relation(env):
    """ordered=True does not bypass the >=2 guard nor crash on a degenerate 1-element chain:
    the guard still rejects it and NO precedes tuple is written."""
    api, wf = env
    parent = _ordered_parent(api)
    with pytest.raises(WorkflowError, match="2"):
        wf.decompose(parent["id"], subtasks=[{"title": "only"}], ordered=True)
    assert not any(kind == "precedes" for _t, _o, kind in api.relations)


# --- C5 (#105): predecessor-cycle detection + distinguishable diagnostic signal ---
#
# A cycle can only be introduced by a human hand-editing follows/blocked relations in the web UI
# (an ordered decompose builds a linear, acyclic chain). When it happens every task in the loop
# has an unfinished predecessor, so nothing is claimable — and without this it would masquerade
# as a plain starving tail (or, worse, silence). next_task must emit a DISTINCT cycle signal.


def test_two_cycle_reported_as_cycle_naming_both_tasks(env):
    """A 2-cycle (A follows B, B follows A) -> the distinguishable CYCLE signal: task None,
    cycle True, cycle_tasks naming BOTH, message says 'cycle' and both refs. It must NOT read as a
    starving tail (no `starving` key) nor as an empty queue."""
    api, wf = env
    a = api.add_task("A", "Queue")
    b = api.add_task("B", "Queue")
    api.add_relation(a["id"], b["id"], "follows")   # A follows B -> B is A's predecessor
    api.add_relation(b["id"], a["id"], "follows")   # B follows A -> A is B's predecessor
    res = wf.next_task()
    assert res["task"] is None
    assert res["cycle"] is True
    assert "starving" not in res                     # distinguishable from the starving tail
    assert res != EMPTY and res["message"] != EMPTY["message"]
    assert {n["id"] for n in res["cycle_tasks"]} == {a["id"], b["id"]}
    assert a["identifier"] in res["message"] and b["identifier"] in res["message"]
    assert "cycle" in res["message"].lower()


def test_three_cycle_reported_naming_all_three(env):
    """A 3-cycle (A→B→C→A via follows) is detected and all three tasks are named."""
    api, wf = env
    a = api.add_task("A", "Queue")
    b = api.add_task("B", "Queue")
    c = api.add_task("C", "Queue")
    api.add_relation(a["id"], b["id"], "follows")   # A follows B
    api.add_relation(b["id"], c["id"], "follows")   # B follows C
    api.add_relation(c["id"], a["id"], "follows")   # C follows A
    res = wf.next_task()
    assert res["cycle"] is True
    assert {n["id"] for n in res["cycle_tasks"]} == {a["id"], b["id"], c["id"]}
    for t in (a, b, c):
        assert t["identifier"] in res["message"]


def test_self_loop_detected_no_crash_no_hang(env):
    """THE malformed-relation guard: a self-referential 'A follows A' makes A its own unfinished
    predecessor. It must be reported as a 1-cycle — never crash, never loop forever. Reaching the
    assertions at all proves the traversal terminated."""
    api, wf = env
    a = api.add_task("A", "Queue")
    api.add_relation(a["id"], a["id"], "follows")   # A follows A
    res = wf.next_task()
    assert res["cycle"] is True
    assert [n["id"] for n in res["cycle_tasks"]] == [a["id"]]
    assert a["identifier"] in res["message"]


def test_blocked_relation_cycle_detected(env):
    """`blocked` is a predecessor kind like `follows`: a 2-cycle built from blocked links is
    caught too (parity with follows)."""
    api, wf = env
    a = api.add_task("A", "Queue")
    b = api.add_task("B", "Queue")
    api.add_relation(a["id"], b["id"], "blocked")   # A blocked-by B
    api.add_relation(b["id"], a["id"], "blocked")   # B blocked-by A
    res = wf.next_task()
    assert res["cycle"] is True
    assert {n["id"] for n in res["cycle_tasks"]} == {a["id"], b["id"]}


def test_deep_acyclic_chain_not_flagged_as_cycle(env):
    """THE false-positive guard: a long acyclic chain (head in Build, a deep Queue tail all gated)
    must read as a starving tail, NEVER a cycle. N deliberately EXCEEDS Python's default recursion
    limit (1000) so a naive recursive DFS would stack-overflow here — the iterative walk must not
    (this runs inside the pump's own tool)."""
    api, wf = env
    head = api.add_task("head", "Build")            # in-flight, below Review -> the tail is gated
    n = 1100
    chain = [api.add_task(f"s{i}", "Queue") for i in range(n)]
    # s0 follows s1 follows ... follows s[n-1] follows head — every Queue task gated, fully acyclic
    for i in range(n - 1):
        api.add_relation(chain[i]["id"], chain[i + 1]["id"], "follows")
    api.add_relation(chain[n - 1]["id"], head["id"], "follows")
    res = wf.next_task()
    assert res["task"] is None
    assert res.get("cycle") is None                 # NOT a cycle
    assert "cycle" not in res
    assert res["starving"] is True                  # an honest starving tail


def test_converging_dag_not_flagged_as_cycle(env):
    """THE diamond guard that separates `visited` from `on_path`: D (in Build) is a shared
    predecessor reached by two paths from A (A→B→D and A→C→D). When D is re-reached OFF the current
    path it must be PRUNED via `visited`, not mistaken for a back-edge. A single-set DFS would
    false-positive here."""
    api, wf = env
    d = api.add_task("D", "Build")
    b = api.add_task("B", "Queue")
    c = api.add_task("C", "Queue")
    a = api.add_task("A", "Queue")
    api.add_relation(b["id"], d["id"], "follows")   # B follows D
    api.add_relation(c["id"], d["id"], "follows")   # C follows D
    api.add_relation(a["id"], b["id"], "follows")   # A follows B
    api.add_relation(a["id"], c["id"], "follows")   # A follows C
    res = wf.next_task()
    assert res["task"] is None
    assert res.get("cycle") is None
    assert res["starving"] is True


def test_cycle_elsewhere_does_not_suppress_a_claimable_free_task(env):
    """THE isolation guarantee: a 2-cycle among X,Y (HIGHER priority) must NOT hide an unrelated,
    genuinely claimable free task (lower priority) elsewhere in Queue. next_task returns the free
    task — the free-queue loop returns it before cycle detection ever runs — so a cycle anywhere
    on the board never wedges the pump."""
    api, wf = env
    x = api.add_task("X", "Queue", priority=5)
    y = api.add_task("Y", "Queue", priority=5)
    api.add_relation(x["id"], y["id"], "follows")
    api.add_relation(y["id"], x["id"], "follows")
    free = api.add_task("free", "Queue", priority=1)   # unrelated, ungated, LOWER priority
    res = wf.next_task()
    assert res["resume"] is False
    assert res["task"]["id"] == free["id"]
    assert "cycle" not in res


def test_acyclic_gated_tail_still_reported_as_starving_not_cycle(env):
    """The ordinary starving tail (gated but acyclic) is unchanged: a lone Queue successor blocked
    by a Build predecessor is a starving tail, explicitly NOT a cycle."""
    api, wf = env
    pred = api.add_task("pred", "Build")
    succ = api.add_task("succ", "Queue")
    api.add_relation(succ["id"], pred["id"], "follows")
    res = wf.next_task()
    assert res["starving"] is True
    assert res.get("cycle") is None
    assert "cycle" not in res


def test_empty_queue_still_empty_not_cycle(env):
    """Sanity: with nothing gated the empty-queue signal is untouched — the cycle path never fires
    (byte-for-byte the #102 empty result, no cycle discriminators)."""
    api, wf = env
    res = wf.next_task()
    assert res == EMPTY
    assert "cycle" not in res


# --- #126 (VMCP-47): next_task's LIGHT board must not conflate "absent" with "not a predecessor".
# A predecessor parked off the light board (Backlog beyond page 1, Your Call, Done) used to be
# invisible to next_task yet seen by claim -> next_task offered what claim refused (a livelock).
# FakeAPI.view_tasks now truncates non-required buckets (page_size) like the real client, so this
# is reproducible at all; the gate escalates to the full board to make absence definitive.


def _hide_off_light_board(api):
    """Make every NON-required bucket (Backlog/Your Call/Done) return nothing on the LIGHT board
    next_task fetches — models a predecessor sitting beyond the light board's fetched pages (an
    unbounded Backlog/Done, or a Your Call parked by call_human), which #43 deliberately does not
    page. The exhaustive full board (require_titles=None, read by claim/advance) is unaffected."""
    api.page_size = 0


def test_next_task_backlog_head_off_light_board_gates_not_offers(env):
    """THE #126 regression: the chain HEAD sits in Backlog (returned via return_task) BEYOND the
    light board's window, so next_task can't see it directly. It must NOT read the tail as ungated
    and offer it (the livelock: claim then refuses); instead it escalates to the full board, finds
    the head, and reports the starving tail + needs_retriage. next_task and claim AGREE — both
    refuse the tail. (Before the fix next_task offered the tail while claim raised — the split.)"""
    api, wf = env
    head = api.add_task("head", "Backlog", labels=("blocked",))   # returned via return_task
    tail = api.add_task("tail", "Queue")
    api.add_relation(tail["id"], head["id"], "follows")
    _hide_off_light_board(api)
    res = wf.next_task()
    assert res["task"] is None                                    # NOT offered
    assert res["starving"] is True
    assert res["needs_retriage"] is True
    assert res["waiting_count"] == 1
    w = res["waiting"][0]
    assert w["task"]["id"] == tail["id"]
    blk = w["blocked_by"][0]
    assert blk["id"] == head["id"] and blk["stage"] == "Backlog"
    assert head["identifier"] in blk["ref"]
    # next_task <-> claim agreement: claim refuses the same tail, naming the same head
    with pytest.raises(WorkflowError) as exc:
        wf.claim(tail["id"])
    assert head["identifier"] in str(exc.value)
    assert api.stage_of(tail["id"]) == "Queue"


def test_next_task_escalates_to_full_board_at_most_once(env):
    """The escalation is bounded to ONE extra fetch and only when needed: with a predecessor off
    the light board next_task issues exactly TWO view_tasks (light + one full), never N-per-cand."""
    api, wf = env
    head = api.add_task("head", "Backlog")
    tail = api.add_task("tail", "Queue")
    api.add_relation(tail["id"], head["id"], "follows")
    _hide_off_light_board(api)
    wf.next_task()
    assert api.view_tasks_calls == 2                              # one light + one escalation, memoised


def test_next_task_no_escalation_when_predecessor_on_light_board(env):
    """#43/#105 preserved: when the predecessor is already on the light board (a ready head at
    Review, in NEXT_TASK_STAGES) next_task never fetches the full board — exactly ONE view_tasks."""
    api, wf = env
    _pred, succ = _chain(api, pred_stage="Review")
    res = wf.next_task()
    assert res["resume"] is False and res["task"]["id"] == succ["id"]
    assert api.view_tasks_calls == 1                              # no escalation on the common path


def test_next_task_your_call_predecessor_off_light_board_gates(env):
    """A predecessor parked in Your Call by call_human is NOT ready (READY_STAGES={Review,Done})
    AND is off the light board (#43). next_task must escalate, gate the successor (starving, but
    NOT needs_retriage — Your Call isn't Backlog), and agree with claim's refusal."""
    api, wf = env
    pred, succ = _chain(api, pred_stage="Your Call")
    _hide_off_light_board(api)
    res = wf.next_task()
    assert res["task"] is None
    assert res["starving"] is True
    assert res["needs_retriage"] is False
    assert res["waiting"][0]["blocked_by"][0]["stage"] == "Your Call"
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    assert pred["identifier"] in str(exc.value)


def test_next_task_deleted_predecessor_still_not_a_blocker(env):
    """"Genuinely deleted -> not a blocker" survives the escalation: a follows-predecessor absent
    from BOTH the light AND the full board must NOT freeze the tail — next_task offers it and claim
    allows it (agreement). Modelled by orphaning the predecessor (still referenced by the relation,
    but on no bucket) — exactly the found-is-None-after-escalation branch the fix must keep open."""
    api, wf = env
    pred, succ = _chain(api, pred_stage="Backlog")
    del api.task_bucket[pred["id"]]                               # orphaned: relation dangles, on no board
    res = wf.next_task()
    assert res["resume"] is False
    assert res["task"]["id"] == succ["id"]                        # offered, not gated
    assert wf.claim(succ["id"])["claimed"] is True               # claim agrees: claimable
    assert api.stage_of(succ["id"]) == "Design"


def test_next_task_and_claim_agree_when_off_board_predecessor_ready(env):
    """Agreement in the "ready" direction too: a predecessor in Done (off the light board) is READY,
    so escalation classifies it as not-a-blocker and next_task offers the successor — exactly as
    claim allows it. Proves escalation reads the real STAGE, not just presence/absence."""
    api, wf = env
    _pred, succ = _chain(api, pred_stage="Done")
    _hide_off_light_board(api)
    res = wf.next_task()
    assert res["resume"] is False and res["task"]["id"] == succ["id"]
    assert api.view_tasks_calls == 2                              # escalated to learn the Done stage
    assert wf.claim(succ["id"])["claimed"] is True


def test_next_task_offers_free_task_despite_off_board_gated_candidate(env):
    """A genuinely claimable free task must still be returned even when a HIGHER-priority candidate
    is gated by an off-light-board predecessor — the off-board gate must not hide real work. Before
    the fix the higher-priority off-board candidate was wrongly offered instead of the free task."""
    api, wf = env
    head = api.add_task("head", "Backlog")
    gated = api.add_task("gated", "Queue", priority=5)           # higher priority, blocked off-board
    api.add_relation(gated["id"], head["id"], "follows")
    free = api.add_task("free", "Queue", priority=1)
    _hide_off_light_board(api)
    res = wf.next_task()
    assert res["resume"] is False
    assert res["task"]["id"] == free["id"]


# --- the starving-tail message is the plain tail plus the retriage escalation, and nothing else
# (VMCP-114 / #586) ---
#
# WHY THIS STRING AND NOT THE OTHER TEN. #586 measured it: an in-memory mutant appending a marker
# clause to 14 next_task payload strings at once left the suite at 6 failed / 590 passed, and all
# six failures belonged to the three already-pinned controls — 11 prose strings absorb a new clause
# invisibly. Pinning all 11 byte-exact would be the wrong repair: a static literal is emitted in
# exactly ONE state, so a clause inside it is a wording edit that shows up in the diff, and a
# byte-pin on it breaks on every legitimate rewording (which teaches the next agent to weaken pins
# to get green). What a static literal CANNOT do is emit a clause in a state the clause was not
# written for. Conditionally ASSEMBLED prose can — and in next_task there are exactly two such
# strings: the resume note (re-pinned by #570) and this one. This is the other half of that pair.
#
# WHAT IS ACTUALLY AT RISK HERE. The message carries TWO conditional pieces, both driven by
# `stage == "Backlog"`: the per-blocker annotation inside the waiting line, and the trailing
# escalation. Together they are the only place a human is told that a chain HEAD was returned to
# Backlog and needs re-triage — SKILL.md's rule for a starving tail is "surface it so a human sees
# the stalled chain", and that is the sentence they read. Lose it and the tail stalls unseen; emit
# it unconditionally and every ordinary starving tail sends a human hunting for a return_task that
# never happened.
#
# MEASURED, and this is the part worth remembering: the pre-existing guard
# (`"re-triage" in res["message"].lower()`, test_next_task_returned_head_in_backlog_flags_retriage)
# is satisfied by EITHER piece, so the two mutually mask each other's deletion. Deleting the
# trailing escalation: 596 passed. Deleting the annotation: 596 passed. Emitting the escalation
# unconditionally: 596 passed. Moving the escalation BEFORE the waiting list (+0 bytes of new
# text): 596 passed. None of those four is visible to any substring assertion.
#
# THE SHAPE, and what was rejected. A differential over ONE changed attribute: the same board, the
# same tasks, the same refs — the chain head simply moves Build -> Backlog. The left side is what
# workflow.py renders in the retriage state; the right side is what it renders in the plain state,
# transformed by the two literals THIS FILE owns. The sides therefore come from different places
# and can genuinely disagree: an unconditional `message +=` lands after the escalation on the left
# but before it on the right, so even a mutation present in both states fails the equality (and the
# endswith anchor below catches it first). Rejected, for the reasons #570 recorded: importing the
# clause text as constants from workflow.py (both sides on one source — a sentence written INTO the
# constant moves both and stays green), and counting clauses by splitting on a marker (pins a naming
# convention the code never promised).
#
# NOT PINNED BY THIS TEST: the base prose. Reading it from the sibling state instead of copying it
# keeps a rewording of the shared sentence a one-file edit HERE, and that trade still stands for
# this differential. What no longer stands is the REASON this note used to give — «a clause
# inserted INSIDE the base is by construction emitted in every state — a wording change, not a
# state-dependent hazard». VMCP-143 (632) showed that true of EMISSION and false of CONTENT, and
# the mechanism is this equality's own: a clause emitted in BOTH states appears on both sides and
# cancels, so being emitted everywhere is precisely what makes it invisible here rather than what
# makes it harmless. The endswith anchor stops an unconditional clause from hiding in the derived
# base; it does not see one inserted MID-base. That gap is READ elsewhere in this file, not here —
# see «the plain starving base, pinned WHOLESALE» (VMCP-155 / #660), which pins the plain base
# outright at the tail counts it builds and states its own boundary.
#
# THE COUNT, and why this env holds THREE waiting tasks (VMCP-125 / #606). The escalation
# interpolates `len(retriage)`, the base interpolates `len(waiting)`. This test's first env built
# one gated task behind one head, so both rendered as `1` and the two interpolations were
# indistinguishable: swapping the escalation to `len(waiting)` passed the whole suite (693 passed,
# exit 0, on e171c8d). The clause was pinned; the NUMBER inside it was not. On a five-tail board
# with ONE head in Backlog that swap renders ". 5 of these are stalled behind a chain HEAD returned
# to Backlog" where the truth is ". 1 of these". The waiting lines still annotate that one head and
# only it, so the message contradicts itself rather than lying outright — but the escalation is the
# one place the payload states the retriage count as a NUMERAL, and it is where a reader who does
# not enumerate the list takes the number from.
#
# NOT REPAIRED BY A NEW PAYLOAD KEY. There is no TOP-LEVEL retriage count (`waiting_count` is
# `len(waiting)`; the top-level `needs_retriage` is a BOOLEAN), and adding one was rejected on two
# grounds: it is a production change made to serve a test, and it would carry no information the
# payload lacks — the per-tail `waiting[].needs_retriage` flags already sum to that number, which is
# what this test asserts below. Not for fear of the hub, which never sees this payload: it reads
# only the flat verdict line, built by `classify_next` from task/review/resume/stage/cycle/starving
# and nothing else (claimable_cmd.py:69-80).
#
# WHAT MAKES THE PIN BITE is that both sides of the count are ABSOLUTE — the escalation literal
# spells the number and the param row spells it again for the env. A RELATIVE check ("the numeral in
# the message equals the payload's own count") would have caught the measured swap too, but it
# passes any edit moving the count and the prose together, which is #570's same-source trap. The env
# is what makes the absolute form possible: 3 waiting != 2 retriaged, so the swap renders 3 against
# a pinned 2; 2 != 1, so a hard-code to 1 — the value the old env's number happened to equal — also
# goes red.
#
# AND WHY THE PIN SPANS TWO ENVS (VMCP-146 / #635), which is a repair of what the paragraph above
# cost. Making the counts differ moved this test OFF the boundary the predicate is written on:
# `if retriage:` narrowed to `if len(retriage) > 1:` drops the escalation sentence at exactly one
# value of the count — 1 — and 606's env asserts the count is 2, so that value became structurally
# unreachable and the narrowing went GREEN. Measured on this repo's own history, one mutation each,
# whole suite, pristine copy, caches cleared, restores checksum-verified: `1 failed, 693 passed` on
# bce31d7 (the pre-606 tree, env 1 waiting / 1 retriaged — and the one failure is this very test)
# against `723 passed` on 7e97b2b (today's main, env 3 / 2). The state it stops covering is the
# COMMONEST real board — one chain head sent back by one return_task — and there the narrowing
# deletes the sentence outright, which is the exact harm the escalation exists to prevent.
#
# The trade was invisible because the lost discrimination was never STATED: the old env held the
# boundary by accident, so nothing failed when it went. Hence the property is now asserted of the
# PARAM SET rather than of an env — one row that separates the counts, one row that sits ON the
# boundary. Neither row can carry both halves (separating them is exactly what puts the retriage
# count above 1), so a set collapsed back to a single row fails loudly instead of re-blinding a
# correct pin — the same failure mode, one level up.
#
# WHAT THE SECOND ROW DOES NOT BUY, so it is not over-read: it is one more STATE, not a stronger
# technique. Spanning 1 and 2 does kill BOTH degenerate hard-codes — measured, a hard-coded `2` is
# GREEN under 606's single env and RED here (at the 1/1 row), while `1` was already red and stays
# red (at the 3/2 row) — but arithmetic that renders correctly at both spanned values still passes:
# `max(1, len(retriage))` is GREEN, as it would be under any finite set of states.
# And the loss this repairs really was confined to the escalation's own predicate, which bounds the
# claim from the other side too. Re-measured against the pre-fix tree: deleting the escalation is
# `1 failed`, emitting it unconditionally `3 failed` (this test plus both of 632's rows), and the
# SAME narrowing applied to the payload flag — `bool(retriage)` -> `len(retriage) > 1` — is
# `2 failed` there, caught by test_next_task_returned_head_in_backlog_flags_retriage and
# test_next_task_backlog_head_off_light_board_gates_not_offers, which build boards with exactly one
# returned head. So only the narrowing that manifests solely at 1 AND solely in the message was
# ever slipping through; the flag half of the same boundary never stopped being covered.
# Two NEIGHBOURING boundaries of this same class, measured GREEN on this tree and deliberately left
# out of THIS slice, are now CLOSED by VMCP-158 (664) — recorded here so the next reader follows
# them instead of re-measuring them. The headline count at exactly ONE waiting task (632's
# parametrize spanned 2 and 3, and the differential below cannot see the base at all): closed by
# widening that parametrize to 1, in 632's section. And `any` -> `all` over a tail's blockers,
# which no env could tell apart because no gated tail carried blockers at two DIFFERENT stages (the
# one two-blocker tail in the suite has both in Queue, where the quantifiers agree): closed by the
# quantifier section at the END of this file, which builds the mixed pair.

_IN_BUILD = "in 'Build'"
_IN_BACKLOG = "in 'Backlog'"
_RETRIAGE_ANNOTATION = " [sent back to Backlog via return_task — needs human re-triage]"
# One literal per env, spelled with the count that env actually reaches, exactly as #570's clause
# literals do. Written out rather than formatted from the parameter (`f". {retriage_n} of these …"`
# would be the tidy spelling) — that would put the message's number and the env's number back on
# ONE source, which is the same-source trap #570 recorded and 632 re-stated for its numerals.
_RETRIAGE_ESCALATION_2 = (
    ". 2 of these are stalled behind a chain HEAD returned to Backlog (return_task) — a human "
    "must re-triage the head before the tail can resume."
)
_RETRIAGE_ESCALATION_1 = (
    ". 1 of these are stalled behind a chain HEAD returned to Backlog (return_task) — a human "
    "must re-triage the head before the tail can resume."
)

# The envs the pin spans: (waiting tasks, of them behind a head returned to Backlog, that env's
# escalation literal). Row one keeps the two counts APART — 3 != 2 kills the swap of one
# interpolation for the other, 2 != 1 kills a hard-code to the value the pre-606 env happened to
# equal. Row two sits ON the predicate's boundary, retriage == 1. Both properties are asserted of
# this LIST inside the test, because each row satisfies only one of them.
_STARVING_ENVS = [
    (3, 2, _RETRIAGE_ESCALATION_2),
    (1, 1, _RETRIAGE_ESCALATION_1),
]


def _blocker_moved_to_backlog(msg: str, blocker_ref: str) -> str:
    """Rewrite ONE waiting line — the one naming `blocker_ref` — from a Build predecessor to a head
    returned to Backlog. Anchored on the ref, so WHICH line the differential rewrites is not a
    silent premise; at one waiting task the old blanket `.replace()` was position-free only by
    degeneracy. The premise that survives — both states listing `waiting` in the SAME ORDER, since
    the differential rewrites the plain message in place — is asserted at the call site."""
    was = f"{blocker_ref} {_IN_BUILD}"
    assert msg.count(was) == 1, (was, msg)
    return msg.replace(was, f"{blocker_ref} {_IN_BACKLOG}{_RETRIAGE_ANNOTATION}")


@pytest.mark.parametrize("waiting_n, retriage_n, escalation", _STARVING_ENVS)
def test_the_starving_message_is_the_plain_tail_plus_the_retriage_escalation_and_nothing_else(
    request, env, waiting_n, retriage_n, escalation
):
    """The retriage escalation and its per-blocker annotation appear IF AND ONLY IF a blocker sits
    in Backlog, in that position, with the count of RETRIAGED tails and nothing else may follow.

    The two next_task calls differ in exactly one attribute — the chain heads' stage — so every
    other byte of the message is common to both and cancels out of the differential. `_move` is the
    call return_task itself makes on a returned head (workflow.py:1313); the label it also adds is
    irrelevant here, since the retriage condition reads the blocker's STAGE and nothing else.

    Two envs, and it takes both (see the section note): one where the waiting and the retriage
    counts DIFFER, so neither numeral can be read off the other, and one where the retriage count
    is 1 — the value `if retriage:` is written on, and the only value at which narrowing it to
    `> 1` changes a byte. 606 added the first and, unnoticed, removed the second."""
    api, wf = env
    heads = [api.add_task(f"chain head {i}", "Build") for i in range(waiting_n)]
    tails = [api.add_task(f"tail {i}", "Queue") for i in range(waiting_n)]
    for tail, head in zip(tails, heads):
        api.add_relation(tail["id"], head["id"], "follows")

    plain = wf.next_task()
    assert plain["starving"] is True and plain["needs_retriage"] is False
    plain_msg = plain["message"]
    # The anchor that keeps the differential honest (#570's `_clause_free_base` plays the same
    # role): in the plain state the message ENDS with the rendered waiting line, so an
    # unconditional clause appended anywhere after it moves this ending and fails right here
    # instead of cancelling out of the equality below.
    assert plain_msg.endswith(_IN_BUILD), plain_msg
    assert plain_msg.count(_IN_BUILD) == waiting_n, plain_msg
    assert _RETRIAGE_ANNOTATION not in plain_msg, plain_msg

    returned = heads[:retriage_n]            # the one changed attribute — what return_task does
    for head in returned:
        wf._move(head["id"], "Backlog")

    retriage = wf.next_task()
    assert retriage["starving"] is True and retriage["needs_retriage"] is True
    # This env really reaches the counts its row claims — otherwise the literal below is pinned
    # against a state nobody built.
    assert retriage["waiting_count"] == plain["waiting_count"] == waiting_n
    assert sum(w["needs_retriage"] for w in retriage["waiting"]) == retriage_n
    # The rows that ACTUALLY RAN, read off the running node instead of from the module constant.
    # `@parametrize(…, _STARVING_ENVS)` beside an assert over `_STARVING_ENVS` would be TWO sources
    # — #570's same-source trap in reverse — and the gap is constructible: slice only the decorator
    # (`_STARVING_ENVS[:1]`), leave the list alone, and both asserts below pass while one row runs.
    # Measured that way by an independent pass: the boundary mutation went back to `1 passed`.
    # Reading the marker puts the pinned property and the executed set back on ONE source, and the
    # param names are asserted so a restructured decorator fails loudly instead of pinning some
    # other list.
    marker = request.node.get_closest_marker("parametrize")
    assert marker.args[0] == "waiting_n, retriage_n, escalation", marker.args
    rows = marker.args[1]
    assert (waiting_n, retriage_n, escalation) in rows, rows
    # The two properties the PARAM SET exists for, each held by a DIFFERENT row, pinned so neither
    # can be lost in silence the way the second one was. Separated counts (606): the base's numeral
    # and the escalation's must be different numbers, and neither may be 1, the value a hard-code
    # would most plausibly pick. The boundary (635): some row must sit at retriage == 1, or
    # narrowing `if retriage:` to `if len(retriage) > 1:` becomes unobservable. No single row can
    # satisfy both (separating the counts is exactly what lifts the retriage count above 1), so
    # whichever row a collapse keeps, one of these fails HERE — for every row that still runs —
    # instead of re-blinding the equality below.
    assert any(w != r != 1 for w, r, _ in rows), rows
    assert any(r == 1 for _, r, _lit in rows), rows

    # `expected` rewrites the plain message IN PLACE, so the equality below assumes both states
    # list the waiting tasks in the same order. At one waiting task that was vacuous; at three it
    # is a real premise, so assert it here rather than let a reorder surface as a wall of diff.
    ids = [w["task"]["id"] for w in retriage["waiting"]]
    assert ids == [w["task"]["id"] for w in plain["waiting"]], ids

    blocker_ref = {w["task"]["id"]: w["blocked_by"][0]["ref"] for w in plain["waiting"]}
    expected = plain_msg
    for tail in tails[:retriage_n]:
        expected = _blocker_moved_to_backlog(expected, blocker_ref[tail["id"]])
    assert retriage["message"] == expected + escalation, retriage["message"]


# --- the prose's INTERPOLATED VALUES, not just its clauses (VMCP-143 / #632) ---
#
# #586 pinned next_task's prose against CLAUSE growth and, in the SAME commit (037db94), one VALUE
# pair on purpose — `wip_saturated`'s «all 3 WIP slot(s) are busy (4 active)», owned there as a
# literal and asserted against BOTH SKILL.md and the payload ("Pinned as the VALUES and the
# IMPERATIVE, not as prose"). VMCP-125 (606) pinned the first value inside the STARVING message
# (the retriage count). This section pins six more, each measured GREEN — i.e. genuinely
# unprotected — on BASE 886211e, against an unmutated whole-suite control of 0 failed: the four
# values of `_cycle_signal` (the closed loop, each ref, each stage, the task count), the LEFT side
# of `_starving_tail`'s waiting line, and the starving headline's count. With the pins in place the
# same six go red, 2 or 3 failed, and every restore returns to 0 failed. Each was probed
# alone, on a pristine workflow.py in an isolated clone, needle asserted to occur exactly once (the
# waiting line's f-string occurs VERBATIM three times — in `_starving_tail`, `claim` and `advance`
# — so a bare needle silently mutates three renderings), caches cleared, restores
# checksum-verified. A seventh probe, swapping the waiting line's BLOCKER ref, came back 1 failed
# WITHOUT these pins: 606's ref-anchored helper already catches it, as a precondition
# rather than as a statement about the arrow. The pin below makes that half semantic too, so it no
# longer depends on a helper's internals.
#
# THE BASE IS NAMED BY A SHA, AND WHAT THAT REPLACED IS RECORDED RATHER THAN QUIETLY REWRITTEN
# (VMCP-173 / #698). Until that card the sentence above read «on 886211e, this commit's parent»,
# and the appositive was false the day it landed: `git log --format='%H %P' -1 1bcdede` gives this
# section's own commit the parent c45bd21 — the v0.2.120 bump, TWO commits past 886211e, with
# ec68f43 between them. 632's worklog says the base moved under it mid-integration, precisely what
# the phrase is banned for in the param-set note inside
# `test_the_starving_waiting_line_reads_blocked_task_then_blocker_and_the_headline_counts_them`
# below, and again in #660's section and #664's — named by CARD rather than by position, because
# sections here are APPENDED and «the two after this one» goes false on the next one written. The
# SHA was right and only the relationship was not, so nothing above needed re-measuring: re-run on
# 886211e for #698, the selection collects 716 with 0 failed, so the BASELINE those six rounds
# were taken against reproduces at the sha they name — as it does on the parent it misnamed, which
# collects the same 716, so this defect never showed itself as a wrong NUMBER. Only as a wrong
# tree to check out. The six rounds themselves were NOT re-applied: what is re-read below is the
# green they were already measured against, not a fresh kill count.
# EIGHT PASS TOTALS WENT WITH IT — 716/721/715, four in the paragraph above, two in
# `WHY PER-IDENTIFIER` below and two in `WHAT THE ENVS HOLD APART`. Every
# claim they decorated survives as the failure count already beside it, and the total is the half
# that rots: the same selection collects 716 at 886211e and 884 at e9639c7 — both SHA-pinned,
# which is the form VMCP-167 (688) leaves open for HISTORY, its other branch being an ASSERT for a
# count a reader acts on, and nobody acts on these — while 698's own card reached for a third
# figure and pinned it to a DATE, which for a tree-wide count is the one anchor nobody can check
# out. Re-reading each GREEN above as `control 0 failed` fabricates nothing: a pytest summary
# carrying no `failed` clause IS zero failures, so what changed is which half of an already
# measured line is quoted, not when it was taken. That is what let both of this section's entries
# leave `LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT` without a re-measurement. FOUR totals do survive
# above, and they are a different animal: the 716s and the 884 are the SIZE of a named tree, which
# is all «collects» claims, and no round in this section is quoted with a denominator any more.
# Do not read any of it as a guard on totals: `_ROUND_COUNT` there matches `N failed` only, so
# putting the old wording back is 0 failed — run twice while this card was in flight, once over
# the whole `tests/unit` and once over the two-file selection that carries the ratchet, each
# against its own unmutated control of 0 failed on that same selection.
#
# WHY PER-IDENTIFIER SUBSTRING ASSERTIONS CANNOT DO THIS JOB, and this is the whole point of the
# section. Every ref in `_cycle_signal`'s message renders TWICE — once in `loop`, once in `detail`
# — and the head ref THREE times, because the loop is closed with `nodes[0]`. So the pre-existing
# `identifier in message` checks are satisfied by whichever copy survives. Measured in two halves,
# because no ONE tree shows both: rendering `n['stage']` where `n['ref']` belongs is 0 failed
# WITHOUT this section — same BASE 886211e, same unmutated control of 0 failed — and under THIS
# section's env (stages pairwise distinct) the same mutant renders
# `Задачи в цикле: Queue in 'Queue'; Build in 'Build'; Design in 'Design'`
# — every identity gone from the detail clause — while every pre-existing test still passes:
# 3 failed, and the three are this section's own params. Mutual masking, demonstrated rather
# than argued. The repair is therefore not another PER-IDENTIFIER check but a CONTIGUOUS literal
# spanning a value in its position — `in msg` is still the operator, what changed is what the
# literal covers: the surviving copy is rendered with different punctuation and cannot satisfy it.
# THE RENDERING ABOVE IS QUOTED AS IT WAS MEASURED, on a tree where this message was still
# Russian; VMCP-292 (1166) translated its prose and touched neither the interpolated values nor
# the punctuation around them. Re-run there rather than reasoned about, selection
# `test_workflow_sequence_gate.py -k cycle` (67 collected, 11 selected): control 0 failed /
# 0 errors, the same stage-for-ref mutation 3 failed / 0 errors — this section's own three
# params, exactly as recorded above — and the mutant now renders
# `Tasks in the cycle: Queue in 'Queue'; Build in 'Build'; Design in 'Design'`, i.e. the line
# above with its lead-in translated and every identity still gone.
#
# BOTH SIDES ABSOLUTE, which is 606's rule and the reason these pins can disagree with the code.
# `_spelled_ref` respells the ref format in THIS FILE, so no literal below is derived from
# workflow.py; the numerals are spelled in the parametrize lists rather than computed from the
# parameter, so a test-side `str(size)` cannot quietly put both sides back on one source. A RELATIVE
# check (numeral == the payload's own count) would pass any edit moving value and prose together.
#
# WHAT THE ENVS HOLD APART. The cycle env gives its nodes PAIRWISE DISTINCT stages that are never
# equal to a ref — without that, swapping `n['ref']` for `n['stage']` is unobservable by
# construction. The starving env gives every tail a blocker of its own, so no task is its own
# blocker and the arrow's two sides genuinely differ. Both properties are asserted, not assumed:
# 606's bug was an env that silently collapsed into an equality and blinded a correct pin.
# PARAMETRIZING OVER SIZE is what kills a hard-coded count, which no single state can, and that is
# measured HERE rather than inherited, both rounds on BASE 886211e with the pins in place and both
# against the same unmutated control of 0 failed: hard-coding the cycle count to `2` goes red at
# sizes 1 and 3 and green at 2 — 2 failed; hard-coding the headline to `2` goes red at 3 tails
# only — 1 failed. 606 hit that limit from the other side — its review comment measured a
# hard-coded 2 passing 606's single-state pin, and a hard-coded 1 passes it by construction,
# since 606's old env rendered a literal 1. The self-loop is the case the production comment names
# FIRST ("render the loop CLOSED ... so a 2-cycle and a self-loop both read unambiguously" — it
# names both, and both are parametrized): drop the closure and a self-loop renders as a bare ref
# with no arrow.
#
# NOT PINNED HERE — AND NO LONGER UNPINNED, which is a narrower sentence than "closed" and is meant
# to be. This paragraph is now a POINTER, kept rather than deleted because the note it replaces was
# the only record the finding had, and the pin it points at states its own boundary. What was open: a
# clause inserted INSIDE the starving base prose — `f" ({len(waiting)} need human re-triage)"`,
# FALSE in the plain state where ZERO tails need re-triage — is invisible to this card's value pins
# AND to 606's differential alike whenever it renders IDENTICALLY in the plain and the retriage
# states, since the differential rewrites the plain message in place and a state-independent edit
# cancels out of it. That is clause growth (#586's class), not one of the interpolated VALUES this
# section owns, which is why 632 measured it and correctly closed nothing here. The justification
# #586 and 606 both inherited («a clause inserted INSIDE the base is by construction emitted in
# every state — a wording change, not a state-dependent hazard») is true of EMISSION and false of
# CONTENT: 632's review said so, VMCP-155 (660) acted on it. What reads that region now is the
# wholesale equality in «the plain starving base, pinned WHOLESALE» later in this file — position
# closed at the tail counts it builds, with its own boundary stated there rather than here; the
# rounds live THERE too, re-measured against their own control instead of carried up.

_CYCLE_STAGES = ["Queue", "Build", "Design"]   # pairwise distinct, and never equal to a ref


def _spelled_ref(task: dict) -> str:
    """The ref format as THIS FILE spells it — deliberately not imported from workflow.py, so the
    expected literals below and the rendered message come from two independent sources and can
    genuinely disagree. Asserted against the payload's own `ref` before any literal uses it, so a
    format change fails loudly here instead of silently weakening every pin in this section."""
    return f"{task['identifier']} ({task['id']})"


def _cycle_env(api, size: int) -> list[dict]:
    """A `size`-node predecessor cycle whose members sit in DISTINCT stages: task i follows task
    i+1, and the last follows the first. Only the Queue member is a gate candidate; the walk
    reaches the others as unfinished predecessors, so the reported order is the creation order."""
    tasks = [api.add_task(f"cycle {i}", _CYCLE_STAGES[i]) for i in range(size)]
    for i in range(size):
        api.add_relation(tasks[i]["id"], tasks[(i + 1) % size]["id"], "follows")
    return tasks


@pytest.mark.parametrize("size, count", [(1, "1"), (2, "2"), (3, "3")])
def test_the_cycle_message_pins_the_closed_loop_every_ref_beside_its_own_stage_and_the_count(
    env, size, count
):
    """`_cycle_signal`'s four interpolated values in THREE contiguous literals — the detail literal
    carries each ref beside its OWN stage, so one assert covers two of the four. This is the branch
    that tells a human a chain can NEVER self-unblock and only they can cut it, so a wrong ref sends
    them to the wrong card and a wrong count misstates how much of the board is stuck."""
    api, wf = env
    tasks = _cycle_env(api, size)
    res = wf.next_task()
    assert res["cycle"] is True

    stages = _CYCLE_STAGES[:size]
    refs = [_spelled_ref(t) for t in tasks]
    # The env properties every literal below rests on. Collapse any of them — one shared stage, a
    # stage that reads like a ref, a reordered walk — and the pins go vacuous instead of failing.
    assert [n["id"] for n in res["cycle_tasks"]] == [t["id"] for t in tasks]
    assert [n["stage"] for n in res["cycle_tasks"]] == stages
    assert [n["ref"] for n in res["cycle_tasks"]] == refs
    assert len(set(stages)) == size and len(set(refs)) == size
    assert not set(refs) & set(stages)

    msg = res["message"]
    # 1. the loop, rendered CLOSED: the head repeats at the end, so a 1-cycle still shows an arrow.
    assert f"— {' → '.join(refs + refs[:1])}: " in msg, msg
    # 2. the task count — the one value here with no machine-readable twin anywhere in the payload
    #    (`cycle_tasks` merely permits counting a list).
    assert f": {count} task(s) " in msg, msg
    # 3. the detail: every ref beside ITS OWN stage, in order, and nothing after it.
    assert msg.endswith("; ".join(f"{r} in '{s}'" for r, s in zip(refs, stages))), msg


@pytest.mark.parametrize("n_tails, headline", [
    (1, "1 queued task(s) can't be claimed"),
    (2, "2 queued task(s) can't be claimed"),
    (3, "3 queued task(s) can't be claimed"),
])
def test_the_starving_waiting_line_reads_blocked_task_then_blocker_and_the_headline_counts_them(
    request, env, n_tails, headline
):
    """The waiting line states WHICH task waits on WHICH blocker, and the headline says how many.
    606's differential cannot see either: it changes one attribute between two calls, so a
    corruption that renders identically in both states cancels out of the equality. Both mutations
    measured green without this test — the line's LEFT side replaced by the blocker's own
    ref, so the line reads `X ← X in 'Build'` and the task that actually waits vanishes from it
    (nothing is transposed, hence not an inversion), and the headline count corrupted
    state-independently.

    The FIRST row, one tail (VMCP-158 / #664), is the boundary: a FLOOR under the headline count
    (`max(2, len(waiting))`) renders truthfully at every other count this test spans, so it changes
    a byte only here. Note the scope: "this test spans", not "the suite builds". The one-tail state
    was reached all along — instrumenting `_starving_tail` over the whole suite on c0da162 records
    eight calls at one waiting tail, from seven distinct nodes — but none of them asserts the
    message, so the floor's wrong numeral was UNOBSERVED rather than unreachable. Same distinction
    the boundary note below draws at the other end of the span. 606's differential cannot supply
    that row either, even though 635 made it
    build exactly this state — the differential reads the base prose out of the plain message and
    rewrites it in place, so a corruption that is identical in both states cancels out of the
    equality regardless of how many tails the env holds."""
    api, wf = env
    heads = [api.add_task(f"chain head {i}", "Build") for i in range(n_tails)]
    tails = [api.add_task(f"tail {i}", "Queue") for i in range(n_tails)]
    for tail, head in zip(tails, heads):
        api.add_relation(tail["id"], head["id"], "follows")

    res = wf.next_task()
    assert res["starving"] is True and res["needs_retriage"] is False
    # The env property the arrow pin lives on: every tail has a blocker of its OWN, so the two sides
    # of the arrow are always different tasks. Give two tails one shared head and the inversion
    # stops being observable on the shared line.
    assert len({_spelled_ref(t) for t in tails + heads}) == 2 * n_tails
    # The pairing and its order, pinned to what this test built, so the rendered line below is read
    # against the board the test set up rather than against whatever the payload happens to say.
    assert [(w["task"]["id"], w["blocked_by"][0]["id"]) for w in res["waiting"]] == [
        (t["id"], h["id"]) for t, h in zip(tails, heads)
    ], res["waiting"]
    # And the SPELLING, against the payload's own `ref` — the same check the cycle test makes, so
    # `_spelled_ref`'s promise holds in BOTH tests. Without it a ref-FORMAT change surfaces only as
    # a wall-of-message diff at the equality below, instead of naming what actually moved.
    assert [(w["task"]["ref"], w["blocked_by"][0]["ref"]) for w in res["waiting"]] == [
        (_spelled_ref(t), _spelled_ref(h)) for t, h in zip(tails, heads)
    ], res["waiting"]

    # The two properties of the PARAM SET, read off the RUNNING node rather than off
    # `[(1, …), (2, …), (3, …)]` as a module constant — 635's idiom, and it is there because
    # slicing only
    # the decorator leaves an assert over the constant green while one row runs (measured by an
    # independent pass on 635's own set). The first: some row must sit at ONE tail, the boundary
    # this card restores (VMCP-158 / #664) and the only count REACHABLE THROUGH `next_task` at
    # which a floor like
    # `max(2, len(waiting))` changes a byte: measured on BASE c0da162, that floor
    # is **0 failed — GREEN** without this row (pristine workflow.py `a29f65f0d11f…`, mutant
    # `03eae4d34575…`) and **1 failed** with it, the one failure being this test's own
    # `[1-…]` row. That scope is not decoration — the floor also changes a byte at ZERO, measured
    # by the same direct call this note uses further down (`0 queued …` pristine, `2 queued …`
    # under it), and zero is exactly what `next_task` cannot hand it.
    # That base is named by SHA and never as "this commit's parent": the phrase holds
    # only until the next rebase, and an earlier spelling of this note carried it here after a
    # later rebase had already falsified it — c0da162 was never the parent of the commit that said
    # so. So where this note names a base, it names a sha.
    # Every failure count this note MEASURED comes from a whole-suite round against an unmutated
    # control of 0 failed on the same selection, and is quoted as that count rather than as
    # `N of <total>` on purpose: the total moves with every test a sibling lands, and it kept
    # moving under this very card — across the rebases that wrote this note, and again after the
    # note had landed. Both halves of that pair were run on that ONE base rather than one half
    # being inherited. Numbers here that this note did NOT measure say so on the spot: 632's
    # one-failure result, cited below, is quoted from its own tree, and the 727/731 denominators the
    # earlier spelling carried off 589190c — a base three bumps older whose workflow.py differs —
    # went stale the moment the branch was rebased and are kept as this rule's EXHIBIT, not as
    # evidence.
    # The second is 632's own property, now STATED
    # instead of merely being true: the set must span at least two distinct counts, which is what
    # kills a hard-coded numeral and what no single state can do — re-measured here, since the row
    # strengthens it: a headline hard-coded to `2` is **2 failed**, dying at `[1-…]` AND
    # `[3-…]`, where the section note above records 632 measuring the same mutant at one failure,
    # the 3-tail row alone (its own tree, its own baseline — quoted, not re-measured, since the
    # 2/3 set no longer exists to measure). No single row carries both properties (a set
    # holding only the 1-row spans a single count; a set without it spans several but misses the
    # boundary), so a collapsed set fails HERE by name instead of silently re-blinding the headline
    # pin below — measured, not asserted: delete the 1-row from the decorator alone and both
    # surviving rows fail on `any(n == 1 …)` with the collapsed set printed, no code mutation.
    # HONEST BOUNDARY, in 635's spirit and not oversold: spanning 1/2/3 kills degenerate CONSTANTS,
    # not arithmetic that renders correctly at every spanned value — `min(3, len(waiting))` is
    # **0 failed — GREEN** after this pin, and that one IS killable HERE: add a 4-tail row and the
    # same mutant is **1 failed**, dying at `[4-…]` — against that row's OWN control, the row
    # added with nothing mutated, which is **0 failed**. (A pass total used to stand here in place
    # of that control, and it is why the rule above is about the FORM rather than about keeping
    # numbers fresh: written as one number, it read as another by the time review measured it, and
    # as a third by the time this line was rewritten. Nothing was wrong with the round.)
    # WHY it survives without that row is NOT "no env builds a bigger tail" — that was this note's
    # first answer and it is FALSE, refuted by instrumenting `_starving_tail` and running the whole
    # suite on c0da162: fifteen calls, and one of them carries **1100** waiting tails. It is
    # `test_deep_acyclic_chain_not_flagged_as_cycle`, earlier IN THIS FILE, whose chain is
    # deliberately longer than Python's recursion limit; it asserts `task is None`, no `cycle`, and
    # `starving is True`, and never touches `message`, so it cannot see a corrupted headline at any
    # count. So the boundary on THIS axis is the span of counts this test PINS (1..3), not the span
    # the file BUILDS — a distinction the quantifier section at the end of the file does not have to
    # draw, because there the largest set anything builds and the largest set anything pins are the
    # same number.
    # `max(1, len(waiting))` is green too and is NOT that boundary — it is an equivalent mutant on
    # input unreachable THROUGH `next_task`, so its greenness is evidence about nothing:
    # `_starving_tail` has exactly one call site, in the `if gated:` branch, and builds `waiting`
    # from `gated`, so `len(waiting) >= 1` holds on every input that arrives that way and the floor
    # is byte-identical to the original there. That scope is load-bearing rather than pedantic: a
    # test may call the method DIRECTLY, as this file already does with `_unfinished_predecessors`
    # and `_move`, and one that does kills the mutant — constructed and measured,
    # `wf._starving_tail([])["message"]` reads `0 queued task(s) …` pristine and `1 queued task(s)
    # …` under the floor. So "no test set could kill it" (the earlier spelling) is false; the true
    # claim is that no test reaching it through `next_task` can. (635's section frames its own
    # `max(1, len(retriage))` as green "under any finite set of STATES", which is the narrower and
    # correct shape; that value is likewise >= 1 inside `if retriage:`. Noted here so the pattern is
    # not reached for a third time as if it measured something — and so the escalation from states
    # to test sets is not made again.)
    marker = request.node.get_closest_marker("parametrize")
    assert marker.args[0] == "n_tails, headline", marker.args
    rows = marker.args[1]
    assert (n_tails, headline) in rows, rows
    assert any(n == 1 for n, _h in rows), rows
    assert len({n for n, _h in rows}) >= 2, rows

    msg = res["message"]
    assert msg.startswith(headline), msg
    assert msg.endswith(" | ".join(
        f"{_spelled_ref(t)} ← {_spelled_ref(h)} {_IN_BUILD}" for t, h in zip(tails, heads)
    )), msg


# --- the plain starving base, pinned WHOLESALE (VMCP-155 / #660) ---
#
# WHAT WAS OPEN. `_starving_tail`'s base prose was anchored at both ENDS and nowhere between them:
# the headline test above pins a `startswith` and an `endswith`, and 606's differential reads the
# plain message and rewrites it IN PLACE. So a clause inserted in the middle — after "This is NOT
# an empty queue", before "Waiting:" — moves neither anchor and cancels out of the differential
# whenever it renders IDENTICALLY in the plain and the retriage states. VMCP-143 (632) measured
# exactly that and left it open ON PURPOSE (it closed plenty else — six interpolated VALUES): this
# is CLAUSE growth, #586's class, not one of the values that card enumerated, and a partial guard
# shipped inside a card about VALUES would have read as a whole one — the shape CLAUDE.md prices as
# «a guard oversold is worse than one honestly described». Its reviewer filed the follow-up so the
# state had a home other than a comment. The pin below is the whole
# plain base as ONE equality, which makes POSITION stop mattering: wherever in the base a clause is
# inserted it moves a byte this literal does not have. Position is the axis it closes; the other
# axis — whether a clause renders in these rows at all — is the boundary at the end of this note.
#
# BOTH SIDES ABSOLUTE — 606's rule, and the reason these two sides can genuinely disagree with each
# other. `_STARVING_LEAD_IN` is respelled in THIS file instead of imported from workflow.py,
# `_spelled_ref` already does the same for the refs, and each row's headline numeral is spelled in
# the parametrize rather than formatted from the parameter: a test-side `str(n_tails)` would put the
# message's number and the expectation's back on ONE source, which is #570's same-source trap.
#
# MEASURED. Whole `tests/unit` selection every round, `PYTHONDONTWRITEBYTECODE=1` with
# `__pycache__` deleted first, each tree a separate `git clone --no-hardlinks` (never `cp -R`,
# which drags `.venv` and puts the ORIGINAL `src` earlier on `sys.path`) and `vikunja_mcp.__file__`
# printed per round to prove the mutated file is the one that ran. FAILED counts only — pass totals
# move with every test a sibling lands. Restores were checksum-verified against a pristine
# workflow.py every round — but rounds here did NOT all run on one tree, so a checksum is named
# WITH ITS TREE and never once for all. TWO trees carry checksums: BASE 75a1e52 (workflow.py
# `2271861474add8cd…`) and the tree this card landed as, 52d6085 (workflow.py `24ccf733…`), eight
# commits later and #657 having touched workflow.py in between. A THIRD tree appears further down
# and deliberately carries NO checksum: the two-row DRAFT of the equality test never landed, so it
# has no sha to pin one to, and its rounds are placed by the sentence that names them instead. Read
# "two trees" as "the two trees a checksum can belong to", not as the count of trees measured on.
# The rule for reading any round below is that a round can only have
# run where its subject EXISTS: anything measured against the equality below is the landed tree,
# since that test does not exist at BASE, and the rest is BASE. Do not read the two groups off the
# headings alone — the ONE-LINE ALTERNATIVE paragraph SAYS "on BASE" inside itself, while the
# paragraph actually HEADED that way is the WITHOUT one above it; the ONE-LINE ALTERNATIVE's own
# last round, the wordless clause against the equality, is the landed tree by that rule.
# That two-way split is NOT
# exhaustive, and reading it as exhaustive is the very failure it was written to prevent: measuring
# below runs on a THIRD tree too — the two-row DRAFT of the equality test, which carries both its
# own control and the clause round under it — and "the rest is BASE" would send it to a tree
# holding neither that test (0 hits at `75a1e52`, 1 at `6dfd68b`) nor
# `_STARVING_LEAD_IN` (0 against 3) — measured, not assumed. What governs is the subject-EXISTS
# rule; the two-way split is only its answer for the rounds whose tree is one of these two, and a
# round on any other tree NAMES ITS OWN TREE where it stands, as that draft round does — which is
# also the fallback where subject-EXISTS cannot place a round by itself, as with a round that
# mutates an assert HAND-ADDED for it and therefore present in no tree at all.
# The two checksummed trees are named by sha, never "this commit's parent", and each checksum is
# pinned TO its sha: workflow.py moves with any sibling that edits it, so a reader who re-hashes
# today's file gets a third number without anything here being wrong. Re-derive either with
# `git show "${rev}:src/vikunja_mcp/workflow.py" | shasum -a 256` — BRACED, because in zsh the
# unbraced `"$rev:src/…"` is eaten by the `:s` modifier and collapses to the bare revision, so what
# gets hashed is `git show`'s COMMIT output and not the file. Exit 0, no warning, and a perfectly
# plausible sha256: measured here, 16606 bytes hashed instead of 125898.
#
# WITHOUT this section, on BASE: control 0 failed; the clause
# `f" ({len(waiting)} need human re-triage)"` inserted
# before `Waiting:` -> 0 failed — GREEN, the hole reproduced; the same insertion carrying
# `len(retriage)` instead -> 2 failed, both rows of 606's differential — the state-DEPENDENT half.
# Read the first round for the other half rather than a claim about it: nothing in the suite saw
# the state-INDEPENDENT clause. That second number is MINE and not 632's, and the two digits differ
# without either being an error: at 1bcdede that differential took `env` alone and ran as ONE case
# (checked with `git show "1bcdede:…"`, no parametrize above it), and #635 later split it into two
# rows. Each digit was right on its own tree — which is the argument for re-running rather than
# carrying one over, not evidence that the older one was wrong.
#
# WITH this section — every round IN THIS PARAGRAPH ran on the LANDED tree named above, 52d6085,
# and NOT on BASE; an earlier spelling of this note said "same base", which was true of the WITHOUT
# rounds and false of these. The scope is this paragraph's four rounds and not "everything below",
# which is what that earlier spelling said and is false of the text under it: the ONE-LINE
# ALTERNATIVE paragraph below CONTAINS "constructed on BASE and measured" — in its third sentence,
# not its opening, so locate it by reading rather than by position. This narrowing is about which
# TREE a round ran on and nothing else; the later paragraphs' "this section's control of 0 failed"
# still names the control stated here, which is the only one this section has.
# Same selection, control 0 failed: that `len(waiting)` clause is
# -> 3 failed, all three rows of the test below and nothing else; the `len(retriage)` variant is
# -> 5 failed, this test's three rows plus 606's two. Both compositions were read off a round run
# with the failures LISTED, not inferred from a total — the first spelling of this note inferred
# one and an independent pass was right to ask. Attribution is measured too, because a red suite no
# more names the assert that did the work than a green one does: with the same clause applied and
# ONLY the equality below deleted, the round goes back to -> 0 failed. These rounds ran on THIS
# tree's asserts; only this prose moved afterwards, and nothing executes prose.
#
# WHAT IT BUYS OVER THE ONE-LINE ALTERNATIVE, which is why that one is recorded as REJECTED and not
# as pending. It is blindness to VOCABULARY, not closure of the class — an earlier draft of this
# note said "it closes the class" and a round refuted that; see the boundary below. The clause 632
# measured happens to carry the word `re-triage`, so a plain-state `assert "re-triage" not in
# plain_msg` in 606's test kills THAT SPELLING and only it: constructed on BASE and measured,
# control 0 failed with that assert added and nothing mutated, the `len(waiting)` clause under it
# is -> 2 failed, while a clause with no retriage vocabulary and no interpolation at all,
# `" (nothing here is claimable)"`, is -> 0 failed under the very same assert. Against the equality
# below that wordless clause is -> 3 failed. Any spelling versus one spelling, at the counts these
# rows build — that is the whole of the difference, and it is enough: the one-liner could only ship
# honestly labelled PARTIAL, and unlabelled it would be the oversold guard CLAUDE.md names.
#
# THE COST, stated rather than waved through: the base's wording is now a TWO-FILE edit — change
# the prose in workflow.py and this literal moves with it. Measured, not assumed: against this
# section's control of 0 failed, moving BOTH together is -> 0 failed, so nothing else in the suite
# pins that prose and the two-file edit really is the whole cost. It is the price #586
# declined to pay in 037db94 and 606 inherited, on a stated reason ("a clause inserted INSIDE the
# base is by construction emitted in every state — a wording change, not a state-dependent
# hazard") that 632's review showed to be true of EMISSION and false of CONTENT. The same price is
# already paid elsewhere in this repo — by #570's clause literals in test_workflow_wip.py, and by
# this file's own value pins.
#
# HONEST BOUNDARY, and it was measured AGAINST THIS NOTE rather than conceded in the abstract. An
# equality is a SET OF POINTS, not a property — `_clause_free_base` says of the same shape that it
# "CLOSES nothing" — and the points here are the plain state at ONE, TWO and THREE tails, every
# blocker in Build. A clause guarded on a count OUTSIDE that span walks straight past: the wordless
# clause guarded on `len(waiting) == 4` is -> 0 failed against this section's control of 0 failed.
# The span is 632's headline span and not a taste, and the reason it is three rows rather than two
# is a round on the two-row DRAFT of this test: an independent second pass guarded the same clause
# on `len(waiting) == 2` and got -> 0 failed there — re-measured here on that draft, control
# 0 failed, the clause -> 0 failed — while the headline test above RENDERED the corrupted message
# at its own `[2-…]` row and passed. With the third row in place the same clause is -> 1 failed,
# and the one failure IS the `[2-…]` row, so the row is what does the work. That round is what
# struck "a clause guarded on a particular `len(waiting)` cannot hide" out of this note, and it is
# why the span a pin BUILDS should match the span its neighbours build — 2 was never "the count
# nobody built", it was built forty lines up. A clause keyed on something no row varies — the
# blockers' stage,
# the board, anything reachable through `self` at that point — is invisible the same way; the
# answer to any of these is another row, not a bigger claim about the rows here. And a clause that
# renders ONLY in the retriage state is outside this pin BY CONSTRUCTION, no row building that
# state; it belongs to the half 606's differential is for, of which the `len(retriage)` round above
# is one instance measured caught — an instance, not a proof about every such clause. The two pins
# are complementary, neither being the other's backstop.

_STARVING_LEAD_IN = (
    " — each waits on an unfinished predecessor (a predecessor is 'ready' only at Review or "
    "Done). This is NOT an empty queue. Waiting: "
)


@pytest.mark.parametrize("n_tails, headline", [
    (1, "1 queued task(s) can't be claimed"),
    (2, "2 queued task(s) can't be claimed"),
    (3, "3 queued task(s) can't be claimed"),
])
def test_the_plain_starving_message_is_the_headline_the_lead_in_and_the_waiting_lines_only(
    request, env, n_tails, headline
):
    """The plain starving message is those three parts and NOTHING else — the wholesale anchor the
    base prose never had. The headline test above pins its two ENDS; this one pins the whole of it,
    ends included, which is what it takes to see the MIDDLE — where a clause can sit without moving
    either anchor and without rendering differently in the two states 606's differential compares.

    Read the equality as the assertion of record and the anchors above as the ones that NAME what
    moved: a `startswith` failure says the headline moved, this one says the message did."""
    api, wf = env
    heads = [api.add_task(f"chain head {i}", "Build") for i in range(n_tails)]
    tails = [api.add_task(f"tail {i}", "Queue") for i in range(n_tails)]
    for tail, head in zip(tails, heads):
        api.add_relation(tail["id"], head["id"], "follows")

    res = wf.next_task()
    # The state this pin is ABOUT, asserted so a mutation that flipped the env into the retriage
    # state could not be read here as a wording change: no blocker in Backlog, so neither the
    # per-blocker annotation nor the escalation may render, and the base is the whole message.
    assert res["starving"] is True and res["needs_retriage"] is False

    # The env properties every literal below rests on — the failure this file records for 606, an
    # env that silently collapsed into an equality and blinded a correct pin. Every tail has a head
    # of its OWN (so no
    # task is its own blocker), all refs are pairwise distinct, and the pairing is the one this test
    # built. The spelling is checked against the payload's own `ref`, so a ref-FORMAT change fails
    # by name here instead of as a wall of diff at the equality.
    assert len({_spelled_ref(t) for t in tails + heads}) == 2 * n_tails
    assert [(w["task"]["ref"], w["blocked_by"][0]["ref"]) for w in res["waiting"]] == [
        (_spelled_ref(t), _spelled_ref(h)) for t, h in zip(tails, heads)
    ], res["waiting"]

    # The rows that ACTUALLY RAN, read off the running node rather than off the decorator's list as
    # a module constant — 635's idiom, whose reason THAT card measured: slicing only the decorator
    # leaves an assert over the constant green while a single row runs. The property asserted is the
    # SPAN, a property no single row can carry: with one count only, a clause guarded on any other
    # count renders nowhere here and this pin never sees it.
    marker = request.node.get_closest_marker("parametrize")
    assert marker.args[0] == "n_tails, headline", marker.args
    rows = marker.args[1]
    assert (n_tails, headline) in rows, rows
    assert len({n for n, _h in rows}) >= 2, rows

    assert res["message"] == headline + _STARVING_LEAD_IN + " | ".join(
        f"{_spelled_ref(t)} ← {_spelled_ref(h)} {_IN_BUILD}" for t, h in zip(tails, heads)
    ), res["message"]


# --- the QUANTIFIER over a tail's blockers (VMCP-158 / #664) ---
#
# `_starving_tail` decides a tail's retriage flag with
# `any(b["stage"] == "Backlog" for b in blockers)`. Swapping that `any` for `all` is measured
# GREEN — **0 failed** on BASE c0da162 (a sha, never "this commit's parent" — see the headline
# section above for why), mutant sha a99d30fac406… — i.e.
# genuinely unobservable: the mutated run failed exactly as often as its control, 0 failed, on the
# same whole-suite selection. Every round quoted in this section is a FAILURE count against that
# same control of 0 failed; pass totals are left out on purpose, because they move with every test
# a sibling lands and this card watched them move over and over. The
# reason is NARROWER than "no env ever gives a tail two blockers", which is what the first draft of
# this note asserted: `test_converging_dag_not_flagged_as_cycle` (earlier in this file, the diamond
# guard) already builds a two-blocker gated tail — its A follows BOTH B and C — and it asserts
# `starving is True`, so it does reach this predicate. What no env built BEFORE the rows below is a
# tail whose blockers sit at DIFFERENT stages (present tense would now be false — those rows build
# exactly that, which is the point of them). That is NECESSARY for the two quantifiers to differ
# but not sufficient,
# and the loose spelling of this sentence used to claim it was both: they differ exactly when the
# set holds a Backlog member AND a non-Backlog one, so `("Build", "Design")` differs in stage while
# both quantifiers still agree (False). The test's own assert uses the exact condition, not this
# sentence's. A's blockers are both in Queue, where `any` and `all` agree (both False).
#
# MEASURED, not read — and re-measured HERE rather than inherited: `_starving_tail` instrumented on
# a pristine c0da162 and the whole suite run. Every blocker set it saw, by size and stages:
#     1099 x n=1 ['Queue'] | 16 x n=1 ['Build'] | 6 x n=1 ['Backlog'] | 1 x n=1 ['Your Call']
#        1 x n=1 ['Design'] | 1 x n=2 ['Queue', 'Queue']
# One set of size two, both members at one stage. So the honest statement is NOT "no assertion
# anywhere could have caught it": had that one pre-existing two-blocker env paired a Backlog
# blocker with a non-Backlog one, an ordinary `needs_retriage` assert would have gone red. The
# state that separates the quantifiers is the stage MIX, not the blocker COUNT, and it had never
# been built.
#
# NOT A REGRESSION. Unlike 635's boundary — which was covered on bce31d7 and lost on 7e97b2b, a
# before/after pair the section above can quote — this one has been unreachable for as long as the
# predicate has existed. There is no commit to name, and nothing was traded away to get here.
#
# WHY `all` IS THE WRONG RULE, in product terms. A tail blocked by one head returned to Backlog AND
# one ordinary Build predecessor is exactly the board that most needs a human: the Build
# predecessor will finish on its own, the returned head will not, and only re-triage moves it. Under
# `all` that tail's `needs_retriage` goes False, so it drops out of `retriage` — the escalation
# sentence loses it from its count, or disappears entirely when it was the only such tail — and the
# stalled chain stops reporting itself. Nor is a multi-blocker tail an exotic shape — it is what
# `_unfinished_predecessors` was written for: it walks BOTH relation kinds
# (`PREDECESSOR_RELATION_KINDS = ("follows", "blocked")`) and returns every unfinished one, deduped
# by id, so a card that follows an ordered-epic step AND is hand-marked blocked-by something else
# in the web UI lands here with two. That mixed-KIND path is REALISM, not something these rows pin:
# the env below links BOTH blockers with "follows", and the predicate reads the blocker's STAGE and
# never its relation kind, so nothing here would notice if one of those edges changed kind.
# Nor was the SHAPE ever the exotic part — the diamond guard above already gives one tail two heads
# (A follows B and C) and one head two tails (B and C both follow D). What no env gave a tail, until
# the rows below, is two blockers at DIFFERENT stages.
# (Attribution, and this is the THIRD correction in this one block — recorded rather than quietly
# rewritten, because the repeat is the finding. Draft 1 credited 606's section with recording a
# one-head-to-many-tails fan-out; grepped, it does not. Draft 2 replaced that with "and no env in
# this file builds one", equally false — the diamond guard's D fans out to B and C. And draft 2's
# headline claim, that every gated tail in every env carries exactly one blocker, was false for
# that same env. Three claims about what this file contains, each written from reading it and none
# from measuring it; the histogram above is the first one that was measured.
# Draft 3 then made the SAME mistake twice more, and both were caught by a second independent pass
# rather than by the author, which is the reason that pass is a rule and not a nicety: "no env in
# this file builds" a four-tail state — refuted by a 1100-tail env earlier in this file (see the
# headline test's boundary note) — and "these rows reach blocker-set sizes ONE and TWO", true of the
# file and false of the rows it names. FIVE claims about this file's contents, all five wrong, none
# of them measured before it was written. The standing lesson: about what a suite CONTAINS, do not
# write from reading — instrument and run.)
#
# WITH THE ENV, the same `all` mutant is **2 failed**, and the two failures are the two MIXED
# rows — the all-Build row stays green, which is the point: it is the row that cannot tell the
# quantifiers apart and it is there for the OTHER property below.
#
# WHAT THE ENV BUYS BEYOND THE QUANTIFIER, as a bonus rather than as this card's thesis: the two
# MIXED rows are the same pair of stages in both orders, so a degenerate read of the FIRST blocker
# only (`blockers[0]["stage"] == "Backlog"`) satisfies one row and fails the other — measured,
# **1 failed**, dying exactly at `[blocker_stages1-…]`, the reversed row. That both-orders
# property is what the measurement rests on, so it is asserted of the row set inside the test:
# dropping the reversed row alone re-blinds this exact mutant, and did so in silence. Both halves of
# THAT are measured too, and separately, because a green suite never says which assert did the work:
# drop the reversed row with the mirror assert PRESENT and the run is **2 failed** (both
# surviving rows, on that assert); drop the row AND delete that one assert and the run is
# **0 failed — GREEN**; do that and add the first-blocker mutant and it is **0 failed** again —
# the silence, reproduced.
#
# AND WHAT THESE ROWS ARE NOT EXCLUSIVE ABOUT, stated so nobody reads a red here as "the quantifier
# moved". Both mixed rows anchor the escalation at count 1, so they also inherit 635's boundary:
# `if retriage:` narrowed to `if len(retriage) > 1:` is **3 failed** — 635's own `[1-1-…]`
# row plus these two. That is extra coverage of a NEIGHBOUR, not evidence about `any`/`all`; the
# mutation that isolates this section is the `all` swap above, and it touches no other test.
#
# WHAT WAS NEVER AT RISK, so the pin is not over-read. The per-blocker ANNOTATION inside the waiting
# line carries no quantifier at all — it is decided per blocker, inside the join — so it renders
# beside every Backlog blocker and beside no other whatever this predicate does. Only the payload
# flag (`waiting[].needs_retriage`, and the top-level `needs_retriage` derived from it) and the
# escalation's presence/count ever read through it. The test asserts the annotation half anyway,
# as the control that keeps the other two honest.
#
# HONEST BOUNDARY, and it is the SIZE of the blocker set, not the set of stages — which is worth
# spelling out, because the stage answer is the one that looks right and was measured FALSE. Every
# row here builds ONE tail carrying exactly TWO blockers, so what these rows reach is size TWO and
# only that (an earlier spelling said "sizes ONE and TWO", which is true of the file and false of
# these rows). Size two was already reached before this section — the diamond guard's tail, per the
# histogram above — so what they add AT that size is the mixed stage pair, not the size itself.
# What nothing reaches is THREE: the largest set in the histogram
# is two, and these rows do not widen it. So a rule that degrades only from three blockers up stays
# invisible: `len(blockers) <= 2 and any(…)` is **0 failed — GREEN** after this pin — and that IS
# the boundary rather than a guess about one, measured from the other side too: add a fourth row
# with THREE blockers (Backlog, Build, Build) and the same mutant is **1 failed**, dying at
# that new row alone — against that row's OWN control, the row added with the code pristine,
# which is 0 failed. Same shape as
# 635's admission — the fix buys STATES, not a technique that generalises past them.
# NOT the remainder, though it reads like one: "these envs use only Backlog and Build, so any
# predicate agreeing with `== "Backlog"` on that pair survives". Constructed and refuted —
# `any(b["stage"] != "Build" for b in blockers)` is **2 failed**, killed by
# test_next_task_all_gated_returns_starving_signal_not_empty and
# test_next_task_your_call_predecessor_off_light_board_gates, which gate SINGLE-blocker tails at
# other stages and assert the flag is False there. The stage half of this predicate was already
# covered elsewhere; only its quantifier was not.

# The envs the pin spans: (the blockers' stages, in the order the test links them; the tail's
# expected retriage flag; that env's escalation literal, or None where no escalation may appear).
# THREE properties, one assert each inside the test, because no single row carries them all: the
# two MIXED rows are the only shape on which `any` and `all` disagree (mixed meaning a Backlog
# member beside a non-Backlog one — merely differing stages is not enough); they are the SAME pair
# in BOTH orders, which is what keeps a first-blocker-only read observable; and the all-Build row is
# what stops a hard-coded True from satisfying the flag.
_MIXED_BLOCKER_ENVS = [
    (("Backlog", "Build"), True, _RETRIAGE_ESCALATION_1),
    (("Build", "Backlog"), True, _RETRIAGE_ESCALATION_1),
    (("Build", "Build"), False, None),
]

# Test-owned and deliberately COUNT-AGNOSTIC: the negative row asserts the escalation is ABSENT, so
# needling a fragment that carries no numeral keeps that assertion from passing merely because the
# count changed. The positive rows anchor the full literal above.
_ESCALATION_NEEDLE = "stalled behind a chain HEAD"


@pytest.mark.parametrize("blocker_stages, needs_retriage, escalation", _MIXED_BLOCKER_ENVS)
def test_a_tail_needs_retriage_when_ANY_of_its_blockers_sits_in_backlog_not_when_all_do(
    request, env, blocker_stages, needs_retriage, escalation
):
    """ONE waiting tail carrying TWO blockers — two of the three rows put them at DIFFERENT stages,
    which is the state no other env in this suite builds (a two-blocker tail does exist, in the
    diamond guard above, but with both blockers in Queue) and the only one on which `any(...)` and
    `all(...)` can disagree; the third row is the all-Build mirror, deliberately a state on which
    they AGREE, so a hard-coded True cannot satisfy the flag. (This sentence is printed for all
    three rows, so it must not claim mixed stages of the mirror — an earlier spelling did.) The
    mixed rows are the board a human is most needed on (a returned chain head beside an ordinary
    predecessor that will finish by itself); under `all` that tail stops reporting itself."""
    api, wf = env
    tail = api.add_task("tail", "Queue")
    # The `blocked` label is what return_task leaves on a head it sends back; carried here for
    # fidelity only — the retriage predicate reads the blocker's STAGE and nothing else.
    heads = [
        api.add_task(f"head {i}", stage, labels=("blocked",) if stage == "Backlog" else ())
        for i, stage in enumerate(blocker_stages)
    ]
    for head in heads:
        api.add_relation(tail["id"], head["id"], "follows")

    res = wf.next_task()
    assert res["starving"] is True
    # THE env property this whole test exists for, asserted rather than assumed:
    # ONE waiting tail carrying TWO blockers, in the order this test built them.
    assert res["waiting_count"] == 1, res["waiting"]
    w = res["waiting"][0]
    assert w["task"]["id"] == tail["id"]
    assert [b["id"] for b in w["blocked_by"]] == [h["id"] for h in heads], w["blocked_by"]
    assert [b["stage"] for b in w["blocked_by"]] == list(blocker_stages), w["blocked_by"]

    # The rows that ACTUALLY RAN, read off the running node rather than off `_MIXED_BLOCKER_ENVS`
    # — 635's idiom, and for its measured reason: slicing only the decorator leaves an assert over
    # the module constant green while a single row runs. THREE properties, one assert each. Two
    # collapses were CONSTRUCTED here with the code left pristine, and each fails by the name of the
    # property it drops: `_MIXED_BLOCKER_ENVS[:2]` (keep the mixed rows, drop the mirror) is
    # **2 failed** on the no-Backlog assert, `_MIXED_BLOCKER_ENVS[2:]` (keep only the mirror)
    # is **1 failed** on the MIXED assert,
    # and both print the collapsed set. The THIRD assert exists because a review pass constructed
    # the collapse those two miss, against the version of this test that carried only them:
    # `[_MIXED_BLOCKER_ENVS[0], _MIXED_BLOCKER_ENVS[2]]` — drop the reversed row — ran GREEN, and
    # the FIRST-blocker mutant the section above measures ran green under it too, in silence. That
    # third assert's own bite is measured, not assumed, in three runs rather than one, because a
    # green suite never names the assert that did the work: the same collapse WITH the assert is
    # **2 failed**; the same collapse with that ONE assert deleted is **0 failed — GREEN**;
    # and that plus the first-blocker mutant is **0 failed** — the silence it prevents, rebuilt.
    # Each round here is quoted as a FAILURE count against an unmutated control of 0 failed on
    # the same selection; totals are deliberately absent, since they move with every sibling.
    marker = request.node.get_closest_marker("parametrize")
    assert marker.args[0] == "blocker_stages, needs_retriage, escalation", marker.args
    rows = marker.args[1]
    assert (blocker_stages, needs_retriage, escalation) in rows, rows
    # MIXED (a Backlog member beside a non-Backlog one) is the ONLY shape on which the quantifiers
    # disagree — differing STAGES is necessary but not sufficient, so this reads the exact
    # condition, not the section note's shorthand …
    assert any(
        "Backlog" in s and any(b != "Backlog" for b in s) for s, _n, _e in rows
    ), rows
    # … and a row with NO Backlog at all is what stops a hard-coded True from satisfying it.
    assert any("Backlog" not in s for s, _n, _e in rows), rows
    # … and some pair of rows must be the same stages in the OTHER order, or the FIRST-blocker
    # degenerate above stops being observable. Constructed: drop the reversed row and both the
    # collapsed set AND `blockers[0]["stage"] == "Backlog"` run green, in silence.
    assert any(
        a != b and sorted(a) == sorted(b) for a, _n, _e in rows for b, _m, _f in rows
    ), rows

    assert w["needs_retriage"] is needs_retriage
    assert res["needs_retriage"] is needs_retriage

    msg = res["message"]
    if escalation is None:
        assert _ESCALATION_NEEDLE not in msg, msg
    else:
        assert msg.endswith(escalation), msg
    # The ANNOTATION half is per-blocker and carries no quantifier, so it renders beside every
    # Backlog blocker and beside no other, whatever the flag above does.
    for head, stage in zip(heads, blocker_stages):
        rendered = f"{_spelled_ref(head)} in '{stage}'"
        assert rendered in msg, msg
        assert (rendered + _RETRIAGE_ANNOTATION in msg) is (stage == "Backlog"), msg
