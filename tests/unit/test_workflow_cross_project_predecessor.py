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
from vikunja_mcp.api import VikunjaError
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


# --- #1190: the refusal's ADVICE tail, per fail-closed branch -------------------------------
#
# The blocking DECISION does not change here — #1179 chose fail-closed and that stands. What
# changes is the sentence a human is left holding. Measured on a live `Workflow` over FakeAPI,
# the pre-#1190 refusal for an unreadable neighbour board ended:
#
#     … in 'unknown — project N has no readable tracker board for this token (403/404), so
#     whether it is finished cannot be established'. A predecessor becomes ready only at
#     Review or Done; finish that one first
#
# and nobody can finish a card on a board they cannot read. Each test below carries its own
# CONTROL — a readable same-project blocker in the same round — because the thing that says
# the probe measured anything is the control keeping the OLD tail verbatim.
#
# MUTATION SWEEP over the pins below, run in a CLONE (never the tree being edited), with
# `__pycache__` deleted and PYTHONDONTWRITEBYTECODE=1 per round and `vikunja_mcp.__file__`
# printed per round, resolving inside the clone every time. Selection: this file plus
# `test_workflow_sequence_gate.py`, 85 collected in every round including the control.
# The control is re-stated on every line on purpose: one declared at the top of a section
# stops vouching for the rounds below the next blank line.
#
#   control 0 failed   round: drop the escape clause entirely            -> 6 failed
#   control 0 failed   round: render the escape clause unconditionally   -> 13 failed
#   control 0 failed   round: check `done` AFTER the board read          -> 2 failed
#   control 0 failed   round: give the 403 branch a `done` escape        -> 1 failed
#   control 0 failed   round: `finishable` defaults to False             -> 2 failed
#   control 0 failed   round: render the escape BEFORE the generic tail  -> 2 failed
#   control 0 failed   round: mark the no-bucket branch NOT finishable   -> 1 failed
#   control 0 failed   round: drop the guard on the starving clause      -> 10 failed
#   control 0 failed   round: always insert a separator period           -> 1 failed
#
# Two rows are worth reading twice. The 13 and the 10 are not this file's own doing: an
# unconditional clause reddens the WHOLESALE pin on the plain starving message in
# `test_workflow_sequence_gate.py`, which is why the clause in `_starving_tail` is guarded
# rather than appended. And the last row measured NOTHING on its first run: against that same
# control 0 failed, the separator round came back 0 failed — it was unpinned, and the
# mixed-case assertion in the advance-latch test below is what that empty round bought.

_GENERIC = "A predecessor becomes ready only at Review or Done; finish that one first"
_ESCAPE_LEAD = "At least one of those stages could NOT be established"


def _forbid_task(api, task_id):
    """Make GET /tasks/<id> answer 403 for ONE id — the shape `_offboard_predecessor`'s 403
    branch is written against. NOT re-measured against a live server here, and worth saying so:
    the one live measurement anyone has of this situation points the other way. When the token
    loses access to the neighbour PROJECT, a real 2.3.0 strips the far card out of
    `related_tasks` altogether (two-reader control in the same moment: the owner reads
    `{'blocked': [4]}`, the agent reads `{}`), so that route never reaches this branch at all.
    FakeAPI models `forbidden` per PROJECT and `get_task` is task-scoped and never consulted
    that set, so the branch had no fixture of any kind before this."""
    inner = api.get_task

    def guarded(tid):
        if tid == task_id:
            raise VikunjaError(403, "You don't have the permission to see this")
        return inner(tid)

    api.get_task = guarded


def test_an_ordinary_blocker_keeps_the_generic_tail_and_carries_no_escape(env):
    """THE CONTROL for every test below. A predecessor on a readable board in Build is
    finishable, so "finish that one first" is correct advice and must not move — and its
    blocker dict must carry no `escape`/`finishable` key at all, which is what keeps the
    ordinary refusal byte-for-byte what it was. `endswith` rather than `in` is the load-bearing
    half: a draft of this change gave the shared constant a sentence-final period and this
    assertion is what would have caught it, since the pre-#1190 message had none."""
    api, wf = env
    pred = api.add_task("pred", "Build")
    succ = api.add_task("succ", "Queue")
    api.add_relation(succ["id"], pred["id"], "blocked")
    blockers = wf._unfinished_predecessors(succ["id"])
    assert len(blockers) == 1
    assert "escape" not in blockers[0], blockers[0]
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert msg.endswith(_GENERIC), msg
    assert _ESCAPE_LEAD not in msg


def test_the_unreadable_board_refusal_names_the_escapes_instead_of_the_impossible_one(env):
    """The card's own case. Every blocker is unresolvable, so the generic tail is REPLACED —
    it is the one action that cannot be taken and was the only one printed."""
    api, wf = env
    proj, blocker = _sibling_blocker(api, stage="Build", forbidden=True)
    succ = _blocked_card(api, blocker["id"])
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert _GENERIC not in msg, msg
    assert _ESCAPE_LEAD in msg
    assert "marking it done WILL" in msg
    assert f"share project {proj['id']}" in msg
    assert "vikunja-mcp setup" in msg
    assert "remove the follows/blocked relation" in msg


def test_marking_an_unreadable_predecessor_done_really_does_release_the_card(env):
    """The escape above is only worth printing if it WORKS, so it is measured rather than
    asserted: `done` is read in `_offboard_predecessor` BEFORE any board read, so the card
    clears while the neighbour's board stays exactly as unreadable as it was.

    Round and control in one: the same card refuses before the update and claims after it,
    with nothing else touched."""
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Build", forbidden=True)
    succ = _blocked_card(api, blocker["id"])
    with pytest.raises(WorkflowError):
        wf.claim(succ["id"])                      # control: still blocked
    api.update_task(blocker["id"], done=True)     # the escape the refusal names
    assert wf.claim(succ["id"])["claimed"] is True
    with pytest.raises(VikunjaError):             # the board is STILL unreadable
        api.kanban_view(_proj["id"])


def test_the_403_on_the_task_branch_says_done_will_not_help_because_it_cannot(env):
    """The three fail-closed branches do NOT share one escape, and this is why the advice is
    per-branch rather than one sentence appended to all of them: here `get_task` raises before
    `done` is ever looked at, so "mark it done" is as impossible as "finish that one first".
    Measured in the same test — the update lands and the card stays refused."""
    api, wf = env
    _proj, blocker = _sibling_blocker(api, stage="Build")
    succ = _blocked_card(api, blocker["id"])
    _forbid_task(api, blocker["id"])
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert f"the token got 403 reading task {blocker['id']}" in msg
    assert "not even marking it done" in msg
    assert "Share its project with this token" in msg
    api.update_task(blocker["id"], done=True)
    with pytest.raises(WorkflowError):            # and it really does not release it
        wf.claim(succ["id"])


def test_the_no_bucket_branch_KEEPS_the_generic_tail_because_finishing_really_works_there(env):
    """Third branch, and the one that says the switch is `finishable` and not "was anything
    unresolvable". The board READS here — only the predecessor is in none of its buckets — so
    "finish that one first" is CORRECT advice and the escape is additive rather than a
    replacement. A first draft dropped the generic tail on all three branches alike and was
    wrong on exactly this one."""
    api, wf = env
    proj, blocker = _sibling_blocker(api, stage="Build")
    del api.task_bucket[blocker["id"]]            # on the board of no bucket
    succ = _blocked_card(api, blocker["id"])
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert f"not in any bucket of project {proj['id']}'s board" in msg
    assert f"project {proj['id']}'s board READS" in msg
    assert _GENERIC in msg, msg
    assert msg.index(_GENERIC) < msg.index(_ESCAPE_LEAD), msg


def test_a_mixed_blocker_list_keeps_the_generic_tail_and_appends_the_escape(env):
    """Two predecessors, one finishable and one unresolvable. The generic tail is RIGHT for the
    first half, so it stays — and comes first, because that half is the actionable one."""
    api, wf = env
    near = api.add_task("near", "Build")
    _proj, far = _sibling_blocker(api, stage="Build", forbidden=True)
    succ = api.add_task("succ", "Queue")
    api.add_relation(succ["id"], near["id"], "blocked")
    api.add_relation(succ["id"], far["id"], "blocked")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert f"{_GENERIC}. {_ESCAPE_LEAD}" in msg, msg
    assert msg.index(_GENERIC) < msg.index(_ESCAPE_LEAD), msg


def test_two_predecessors_on_one_unreadable_board_yield_ONE_escape_sentence(env):
    """Deduped in first-seen order. This is the reason the two board branches word their escape
    around the PROJECT rather than the task: repeating the same three actions once per
    predecessor would bury the one thing the reader has to do, and the ref of every blocker is
    already printed immediately before the tail. Two predecessors on one unreadable board is the
    ORDINARY shape of a `handoff`-heavy chain, not a contrivance."""
    api, wf = env
    proj = api.add_project("dogiators-backend", buckets=STAGES, identifier="BACK")
    entry = api.other_projects[proj["id"]]
    build = next(b for b in entry["buckets"] if b["title"] == "Build")
    fars = []
    for title in ("far one", "far two"):
        task = api.create_task(proj["id"], title)
        api.move_task(proj["id"], entry["view"]["id"], build["id"], task["id"])
        fars.append(task)
    api._forbidden.add(proj["id"])
    succ = api.add_task("succ", "Queue")
    for far in fars:
        api.add_relation(succ["id"], far["id"], "blocked")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(succ["id"])
    msg = str(exc.value)
    assert msg.count(_ESCAPE_LEAD) == 1
    assert msg.count("or remove the follows/blocked relation") == 1


def test_the_advance_to_review_latch_carries_the_escape_too(env):
    """The other refusal that renders a blocker list, and the same defect verbatim: "get it
    back to Review first" is exactly as impossible on a board nobody can read. Control in the
    same round — a readable predecessor keeps the old tail and gets no escape."""
    api, wf = env
    control_pred = api.add_task("pred", "Build")
    control = api.add_task("control succ", "Build", assignee=api.me_user)
    api.add_relation(control["id"], control_pred["id"], "follows")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(control["id"], to="review", worklog="w", evidence="s")
    assert _ESCAPE_LEAD not in str(exc.value)
    assert "get it back to Review first" in str(exc.value)

    _proj, far = _sibling_blocker(api, stage="Build", forbidden=True)
    succ = api.add_task("succ", "Build", assignee=api.me_user)
    api.add_relation(succ["id"], far["id"], "follows")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(succ["id"], to="review", worklog="w", evidence="s")
    msg = str(exc.value)
    assert "get it back to Review first" not in msg, msg
    assert _ESCAPE_LEAD in msg
    assert api.stage_of(succ["id"]) == "Build"

    # MIXED, on this refusal specifically, because its generic sentence ends in a period of its
    # own while claim's does not: the join must add exactly one separator, never two and never
    # none. `..` is the whole assertion — it is what an unconditional separator prints here.
    near = api.add_task("near", "Build")
    api.add_relation(succ["id"], near["id"], "follows")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(succ["id"], to="review", worklog="w", evidence="s")
    mixed = str(exc.value)
    assert "get it back to Review first" in mixed and _ESCAPE_LEAD in mixed, mixed
    assert ".." not in mixed, mixed


def test_the_starving_tail_message_names_the_escape_because_no_refusal_is_ever_raised(env):
    """next_task SKIPS a gated card rather than refusing it, so a card parked by `handoff`
    behind an unresolvable predecessor never produces claim's refusal under an ordinary /loop
    drain. This message is then the only place its human is told anything — which is why the
    clause is here as well as on the two refusals.

    Control in the same round: an ordinary starving tail (readable blocker in Build) carries
    NO escape clause, so the plain message is byte-for-byte what it was."""
    api, wf = env
    pred = api.add_task("pred", "Build")
    api.add_relation(api.add_task("control succ", "Queue")["id"], pred["id"], "blocked")
    control = wf.next_task()
    assert control.get("starving") is True
    assert _ESCAPE_LEAD not in control["message"]

    _proj, far = _sibling_blocker(api, stage="Build", forbidden=True)
    api.add_relation(api.add_task("far succ", "Queue")["id"], far["id"], "blocked")
    res = wf.next_task()
    assert res.get("starving") is True
    assert _ESCAPE_LEAD in res["message"], res["message"]
    assert "marking it done WILL" in res["message"]


def test_the_escape_rides_on_the_blocker_dict_so_a_caller_can_read_it_machine_side(env):
    """`waiting[].blocked_by` is the pump's own payload, and the escape is a key on it rather
    than only prose — the same additive shape `stage` has. Absent on a resolvable blocker."""
    api, wf = env
    _proj, far = _sibling_blocker(api, stage="Build", forbidden=True)
    _blocked_card(api, far["id"])
    res = wf.next_task()
    blockers = [b for w in res["waiting"] for b in w["blocked_by"]]
    assert [b["id"] for b in blockers] == [far["id"]]
    assert "escape" in blockers[0]


def test_finishing_the_predecessor_releases_the_NO_BUCKET_card_and_NOT_the_unreadable_one(env):
    """The measurement `finishable` is keyed off, both halves in one round so neither is read
    from the other. Moving the predecessor to Review releases the no-bucket card — its board
    reads, so `READY_STAGES` matches — and does NOT release the unreadable-board one, because
    `_foreign_stages` answers None whatever the far card's stage. That is why the generic
    "Review or Done" tail survives on the first and is replaced on the second, and why the
    second's escape names `done` specifically rather than "finish it"."""
    api, wf = env
    bucket_proj, no_bucket = _sibling_blocker(api, stage="Build", title="no bucket")
    entry = api.other_projects[bucket_proj["id"]]
    del api.task_bucket[no_bucket["id"]]
    a = _blocked_card(api, no_bucket["id"])
    with pytest.raises(WorkflowError):
        wf.claim(a["id"])                                     # control: blocked
    review = next(b for b in entry["buckets"] if b["title"] == "Review")
    api.move_task(bucket_proj["id"], entry["view"]["id"], review["id"], no_bucket["id"])
    assert wf.claim(a["id"])["claimed"] is True               # finishing WORKED

    _dark_proj, dark = _sibling_blocker(api, stage="Build", forbidden=True, title="dark")
    b = _blocked_card(api, dark["id"])
    dark_entry = api.other_projects[_dark_proj["id"]]
    dark_review = next(x for x in dark_entry["buckets"] if x["title"] == "Review")
    api.task_bucket[dark["id"]] = dark_review["id"]           # it IS at Review over there
    with pytest.raises(WorkflowError) as exc:
        wf.claim(b["id"])                                     # and it still does NOT release
    assert "no readable tracker board" in str(exc.value)
    api.update_task(dark["id"], done=True)
    assert wf.claim(b["id"])["claimed"] is True               # only `done` does


# --- #1199: what ONE next_task pays for M cards gated on ONE neighbour --------------------
#
# The read itself is EXHAUSTIVE (`view_tasks` with no `require_titles`), so it pages the
# neighbour's unbounded Done — the very shape #43 removed from our own board — and `next_task`
# is the tool `vikunja-mcp claimable` runs on every hub poll tick, against cards a `handoff`
# can leave parked for days. Measured on FakeAPI at `048d1f9`, one next_task, M free-Queue
# cards each blocked on a card in ONE neighbour project:
#
#     M=0 -> view_tasks 1 (neighbour 0)   M=1 -> 3 (1)   M=3 -> 5 (3)   M=5 -> 7 (5)
#
# The neighbour column is the defect: it tracked M. After the fix the same rows read 1/3/3/3
# with the neighbour read exactly once. The pins below assert the PROPERTY rather than the new
# numbers, because the numbers move with every board shape and the property does not.
#
# MUTATION SWEEP over them, in a CLONE, `__pycache__` deleted and PYTHONDONTWRITEBYTECODE=1 per
# round, `vikunja_mcp.__file__` printed per round and resolving inside the clone, selection this
# file plus `test_workflow_sequence_gate.py`, 90 collected in every round including the control:
#
#   control 0 failed   round: memo back to a local of the helper          -> 3 failed
#   control 0 failed   round: candidate loop passes no memo               -> 3 failed
#   control 0 failed   round: ONE memo slot shared by every project       -> 1 failed
#   control 0 failed   round: `claim` shares a memo ACROSS calls          -> 2 failed
#   control 0 failed   round: narrow the neighbour read to the light set  -> 3 failed
#
# The last row kills only the `require_titles is None` half of the first pin, and that is worth
# knowing precisely: it is a COST mutation here, not a correctness one, because no test in this
# selection puts a neighbour's Done bucket past one page. What the narrowing really costs was
# constructed separately and lives in `docs/dossier/workflow.md` — with `page_size + 1` cards in
# the neighbour's Done and the predecessor last, the exhaustive read claims ALLOWED and the
# narrowed one REFUSES with "not in any bucket". That is why the read stays exhaustive.


def _neighbour_with_m_gated_cards(api, m, project_title="dogiators-backend"):
    """M free-Queue cards, each blocked on its OWN card in ONE neighbour project's Build."""
    proj = api.add_project(project_title, buckets=STAGES, identifier="BACK")
    entry = api.other_projects[proj["id"]]
    build = next(b for b in entry["buckets"] if b["title"] == "Build")
    for i in range(m):
        far = api.create_task(proj["id"], f"far {i}")
        api.move_task(proj["id"], entry["view"]["id"], build["id"], far["id"])
        succ = api.add_task(f"succ {i}", "Queue")
        api.add_relation(succ["id"], far["id"], "blocked")
    return proj


def _count_view_tasks(api):
    """Record every view_tasks call as (project_id, require_titles). Returns the live list."""
    seen = []
    inner = api.view_tasks

    def counted(project_id, view_id, require_titles=None):
        seen.append((project_id, require_titles))
        return inner(project_id, view_id, require_titles)

    api.view_tasks = counted
    return seen


@pytest.mark.parametrize("m", [1, 3, 5])
def test_m_cards_gated_on_one_neighbour_cost_ONE_read_of_that_neighbours_board(env, m):
    """The property, not the figure. `foreign_boards` is owned by next_task since #1199, so it
    spans CANDIDATES and not merely the predecessors of one candidate — which is what it did
    when it was a local of `_unfinished_predecessors`, the method next_task calls once per
    free-Queue candidate."""
    api, wf = env
    proj = _neighbour_with_m_gated_cards(api, m)
    seen = _count_view_tasks(api)
    res = wf.next_task()
    assert res.get("starving") is True, res
    neighbour = [c for c in seen if c[0] == proj["id"]]
    assert len(neighbour) == 1, seen
    # the CONTROL half: the read really is the exhaustive one, so this pin is not vacuously
    # true of a read that never happens.
    assert neighbour[0][1] is None, seen


def test_two_neighbours_cost_one_read_EACH_so_the_memo_is_per_project_not_a_global_off_switch(
    env,
):
    """The other side of the same pin: memoising per project must not degenerate into reading
    one board and answering for all of them."""
    api, wf = env
    first = _neighbour_with_m_gated_cards(api, 2, project_title="backend")
    second = _neighbour_with_m_gated_cards(api, 2, project_title="infra")
    seen = _count_view_tasks(api)
    assert wf.next_task().get("starving") is True
    assert len([c for c in seen if c[0] == first["id"]]) == 1, seen
    assert len([c for c in seen if c[0] == second["id"]]) == 1, seen


def test_claim_keeps_a_PER_CALL_memo_so_nothing_is_cached_ACROSS_calls(env):
    """claim/advance resolve a SINGLE card and hand in no memo, so they get a fresh one per
    call. Two predecessors of one card on one neighbour still cost one read (that memo always
    did that); two SEPARATE claims read the board twice, which is the point — nothing is cached
    across calls.

    That is the half this test can see, and it is not the whole claim. Within ONE `next_task`
    the staleness WINDOW really does grow, from one candidate to one call — constructed in
    `docs/dossier/workflow.md`, where a predecessor moving to the neighbour's Review between two
    candidates is seen before this change and not after. Not a new KIND of staleness (the same
    trade the per-candidate memo already made), but "widens by nothing" would be false, and this
    test was named that way in a first draft."""
    api, wf = env
    proj = api.add_project("dogiators-backend", buckets=STAGES, identifier="BACK")
    entry = api.other_projects[proj["id"]]
    build = next(b for b in entry["buckets"] if b["title"] == "Build")
    fars = []
    for title in ("far one", "far two"):
        far = api.create_task(proj["id"], title)
        api.move_task(proj["id"], entry["view"]["id"], build["id"], far["id"])
        fars.append(far)
    succ = api.add_task("succ", "Queue")
    for far in fars:
        api.add_relation(succ["id"], far["id"], "blocked")
    seen = _count_view_tasks(api)
    with pytest.raises(WorkflowError):
        wf.claim(succ["id"])
    assert len([c for c in seen if c[0] == proj["id"]]) == 1, seen
    with pytest.raises(WorkflowError):
        wf.claim(succ["id"])
    assert len([c for c in seen if c[0] == proj["id"]]) == 2, seen
