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


# --- C2 (#102): next_task filters gated successors + reports the starving tail ---

EMPTY = {"task": None, "message": "the queue is empty — no work for the agent"}


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
