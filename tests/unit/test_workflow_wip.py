"""The WIP slot gate — how many tasks one token may hold in Design/Build at once.

wip_limit generalises the #38 single-WIP flag (enforce_single_wip == wip_limit 1) and is what
makes the parallel drain bounded: without it a pump could claim the whole Queue in one tick.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.config import DEFAULT_WIP_LIMIT
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


def _env(**kwargs):
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, **kwargs)


def _hold(api, wf, title):
    """Claim a fresh Queue task so it lands in Design and counts against the limit."""
    task = api.add_task(title, "Queue")
    wf.claim(task["id"])
    return task


def test_the_unset_default_holds_three_and_refuses_the_fourth():
    """An unconfigured consumer gets THREE slots and a live gate — the human's decision of
    2026-07-30 (tracker #524), replacing the "unset = no gate at all" this test used to pin.

    This is where the number 3 is pinned BEHAVIOURALLY, and only here: the refusal itself has
    to say 3/3, so a fourth claim going through (or the count drifting) fails the test. Every
    other assertion about the default reads DEFAULT_WIP_LIMIT instead of repeating the literal."""
    api, wf = _env()
    for title in ("first", "second", "third"):
        _hold(api, wf, title)
    fourth = api.add_task("fourth", "Queue")
    with pytest.raises(WorkflowError, match=r"WIP limit reached \(3/3\)"):
        wf.claim(fourth["id"])


def test_the_unset_default_comes_from_the_shared_constant():
    """One definition of the number, two readers: workflow's fallback and config.py's < 1
    refusal. A second literal 3 hidden in _effective_wip_limit would drift away from the
    constant the config error advertises, so pin the identity rather than the value."""
    _api, wf = _env()
    assert wf._effective_wip_limit() == DEFAULT_WIP_LIMIT


def test_limit_two_allows_two_and_refuses_the_third():
    """An explicit number must stay the truth in BOTH directions — 2 is narrower than the
    default of 3, so this doubles as "the default is not a floor"."""
    api, wf = _env(wip_limit=2)
    _hold(api, wf, "first")
    _hold(api, wf, "second")
    third = api.add_task("third", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(third["id"])


def test_limit_one_is_the_legacy_single_wip_behaviour():
    api, wf = _env(wip_limit=1)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(second["id"])


def test_wip_limit_wins_over_enforce_single_wip():
    """Both set -> the number is the truth; the legacy flag must not clamp it back to 1."""
    api, wf = _env(enforce_single_wip=True, wip_limit=2)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True


def test_legacy_flag_alone_still_means_one():
    api, wf = _env(enforce_single_wip=True)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(second["id"])


def test_a_freed_slot_is_reusable():
    """advance to Review takes the task out of Design/Build, so the slot comes back."""
    api, wf = _env(wip_limit=1)
    first = _hold(api, wf, "first")
    wf.advance(first["id"], to="build", spec="do the thing")
    wf.advance(first["id"], to="review", worklog="did the thing", evidence="abc1234")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True


# --- next_task in parallel mode: exclude + slot accounting ---

def test_next_task_reports_wip_on_every_result():
    api, wf = _env(wip_limit=2)
    api.add_task("free", "Queue")
    res = wf.next_task()
    assert res["wip"] == {"active": 0, "limit": 2, "free": 2}


def test_wip_reports_the_default_when_the_toml_configures_nothing():
    """The payload the rulebook branches on must never say `null` again: an unconfigured
    consumer now reads limit/free as the default number, which is what tells the pump it may
    run a parallel drain without anyone editing a toml (tracker #524)."""
    _api, wf = _env()
    assert wf.next_task()["wip"] == {
        "active": 0, "limit": DEFAULT_WIP_LIMIT, "free": DEFAULT_WIP_LIMIT,
    }


def test_excluded_active_task_is_not_offered_again():
    """The orchestrator already has a live agent on it; re-offering would dispatch a second
    agent onto the same task. Liveness is a fact of the harness, so the CALLER states it."""
    api, wf = _env(wip_limit=2)
    held = _hold(api, wf, "in flight")
    free = api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False


def test_excluded_task_still_occupies_its_slot():
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res["task"] is None
    assert res["wip_saturated"] is True
    assert res["wip"] == {"active": 1, "limit": 1, "free": 0}


def test_empty_exclude_still_hands_back_the_active_task():
    """A killed turn loses the in-flight set. The next tick passes nothing, and abandoned
    work must surface as resume — this is the crash-recovery path, not a regression."""
    api, wf = _env(wip_limit=2)
    held = _hold(api, wf, "abandoned")
    res = wf.next_task()
    assert res["resume"] is True and res["task"]["id"] == held["id"]


def test_a_resume_at_zero_free_slots_tells_the_pump_to_check_its_exclude():
    """#527: the ONE state where a resume is ambiguous, answered in the payload the pump is
    actually reading at that moment.

    free == 0 with a resume means the caller holds every slot AND did not name one of those
    tasks in `exclude` — either an honest crash-recovery tick (no live agent, dispatch) or an
    incomplete set (a live agent is already there, and dispatching again is the double-dispatch
    `exclude` exists to prevent). The board cannot tell those apart; only the caller can, so the
    note sends it to its OWN set rather than to the board. Pinned as the INSTRUCTION, not the
    word: the assertion names the action the note must demand."""
    api, wf = _env(wip_limit=1)
    _hold(api, wf, "in flight")
    res = wf.next_task()
    assert res["resume"] is True and res["wip"]["free"] == 0
    assert "wip_saturated" not in res
    assert "check your exclude, not the board" in res["note"]
    assert "do NOT dispatch a second agent onto it" in res["note"]


def test_the_zero_slot_note_stays_off_the_ordinary_resume():
    """The clause is conditional on purpose: at free > 0 nothing is ambiguous (saturation was
    never on offer), so the common resume keeps its note unchanged. Without this the rule would
    be noise on every tick — the argument that made it worth adding at all."""
    api, wf = _env(wip_limit=2)
    _hold(api, wf, "in flight")
    res = wf.next_task()
    assert res["resume"] is True and res["wip"]["free"] > 0
    assert "exclude" not in res["note"]


def test_saturation_does_not_suppress_a_review_offer():
    """Background review is not 'your active task' and consumes no slot (SKILL.md rule)."""
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    other = api.add_task("someone else's work", "Review")
    api.add_comment(other["id"], "[worklog] done")
    res = wf.next_task(exclude=[held["id"]])
    assert res["review"] is True and res["task"]["id"] == other["id"]


def test_saturated_result_is_not_the_empty_queue():
    """The pump idles on an empty queue; it must WAIT (not sleep) when merely saturated."""
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res.get("wip_saturated") is True
    assert "empty" not in res["message"]


def test_excluded_review_task_is_not_offered_for_review():
    """review_task never assigns the reviewer to the reviewed task, so the pre-existing
    'my_id in assignees' self-review guard does NOT catch a task one of the pump's own
    live sub-agents is already reviewing — exclude is the ONLY thing standing between this
    board and a second agent dispatched onto the same review."""
    api, wf = _env()
    other = api.add_task("someone else's work", "Review")
    api.add_comment(other["id"], "[worklog] done")
    res = wf.next_task(exclude=[other["id"]])
    assert res["task"] is None
    assert "review" not in res


def test_an_excluded_FREE_queue_task_is_not_handed_back():
    """THE REGRESSION (#1202). Three of next_task's four task-bearing branches consulted
    `excluded`; the free-queue one did not, so the one branch that hands out UNCLAIMED work
    handed back a card the caller had just named.

    The comment at that filter argued it need not: an excluded id is a task the caller already
    holds, i.e. ASSIGNED, so the assignee filter drops it anyway. False — `exclude` states
    SUB-AGENT LIVENESS, not assignment, and SKILL.md instructs putting an UNASSIGNED Queue id
    into it on a claim refusal. Established by construction across all four branches before
    anything changed: the SAME card came back when free and unassigned in Queue, and was
    withheld when claimed into Design, when assigned but still in Queue, and when in Review.
    The three siblings are pinned directly above and below this one; this is the fourth."""
    api, wf = _env()
    free = api.add_task("free queue card", "Queue")
    res = wf.next_task(exclude=[free["id"]])
    assert res["task"] is None, res
    assert res["all_excluded"] is True


def test_the_exclusion_emptying_the_free_queue_is_NOT_an_empty_queue():
    """Its own discriminator, not the empty-queue message — which would be the same class of
    lie the card was filed about: the queue is full, of work this caller already has in hand.

    The `note` carries what the tool CANNOT know: whether a live agent holds these ids (wait,
    like wip_saturated) or whether claim refused them (the tick is done). Both readings are
    legitimate and they differ in what the pump does, so the payload states both rather than
    picking one — the same reason #527 and #571 put their guidance in the note.

    MUTATION SWEEP for the four #1202 tests here, in a CLONE of this tree, `__pycache__` deleted
    and PYTHONDONTWRITEBYTECODE=1 per round, `vikunja_mcp.__file__` printed per round and
    resolving inside the clone. Selection: this file plus tests/unit/test_claimable_cmd.py, 108
    collected in every round including the control — that figure moves with any landing touching
    either file, so re-measure rather than reuse. Rounds read by COUNTING lines beginning
    `FAILED `, `ERROR ` counted separately and 0 throughout.

      control 0 failed  free-queue filter blind to `exclude` again (pre-#1202) -> 4 failed
      control 0 failed  the signal spelled as the ordinary empty queue         -> 3 failed
      control 0 failed  withheld outranks the starving tail (order flipped)    -> 1 failed
      control 0 failed  withheld built from the raw Queue, not the offerable   -> 1 failed
      control after restore 0 failed

    Row 1 is the pre-#1202 code and kills FOUR, which is worth reading rather than counting: with
    the branch blind again the excluded card is OFFERED, so the starving-ordering test gets a
    task where it expects a stalled chain, and the claimable-contract test sees no signal to
    classify. Rows 3 and 4 kill exactly ONE each, and a DIFFERENT one each — which is what says
    the ordering decision and the `withheld` split are pinned separately rather than riding on
    the same assertion.
    """
    api, wf = _env()
    a = api.add_task("free A", "Queue")
    b = api.add_task("free B", "Queue")
    res = wf.next_task(exclude=[a["id"], b["id"]])
    assert res["task"] is None
    assert res["all_excluded"] is True
    assert [w["id"] for w in res["withheld"]] == [a["id"], b["id"]]
    assert "NOT an empty queue" in res["message"]
    assert "wip_saturated" in res["note"] and "claim REFUSED" in res["note"]
    assert "starving" not in res and "wip_saturated" not in res


def test_only_the_EXCLUSION_makes_a_free_queue_task_withheld():
    """`withheld` must name the candidates dropped by `exclude` and by NOTHING else, or the
    signal reports someone else's card as "you already have this one". A card that the filter
    would drop anyway — assigned, `blocked`-labelled, an epic container — is not withheld, so
    excluding one of those leaves an ordinary empty queue."""
    api, wf = _env()
    assigned = api.add_task("someone else's", "Queue", assignee={"id": 99, "username": "other"})
    blocked = api.add_task("externally blocked", "Queue", labels=("blocked",))
    epic = api.add_task("container", "Queue", labels=("epic",))
    ids = [assigned["id"], blocked["id"], epic["id"]]
    res = wf.next_task(exclude=ids)
    assert res["task"] is None
    assert "all_excluded" not in res, res
    assert res["message"] == "the queue is empty — no work for the agent"


def test_a_starving_tail_still_outranks_the_all_excluded_signal():
    """Ordering, and it is a decision rather than an accident: a gated candidate is the
    HUMAN-facing fact (a chain has stalled and nothing will clear it by itself), while a
    withheld one is the caller's own in-flight work, whose ids it already holds. So when both
    are present the starving tail is what comes back."""
    api, wf = _env()
    excluded_free = api.add_task("free, excluded", "Queue")
    head = api.add_task("unfinished head", "Build")
    gated = api.add_task("gated", "Queue")
    api.add_relation(gated["id"], head["id"], "blocked")
    res = wf.next_task(exclude=[excluded_free["id"]])
    assert res["task"] is None
    assert res["starving"] is True
    assert "all_excluded" not in res


def test_excluded_stuck_queue_task_is_not_handed_back():
    """An unfinished claim (assigned to me, still sitting in Queue) that another live
    sub-agent is already finishing must not be handed back as a second 'call claim' — the
    same slot, dispatched twice."""
    api, wf = _env()
    stuck = api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task(exclude=[stuck["id"]])
    assert res["task"] is None
    assert "resume" not in res


# --- the `stage` payload invariant the rulebook's tick branches on ---

def test_every_task_bearing_next_task_result_carries_its_stage():
    """SKILL.md decides "claim or not" by `stage` (Queue -> claim, even when resume is true,
    because that finishes a partial claim; Design/Build -> already yours). That rule is only
    writable if EVERY branch that hands back a task says which stage it came from. Two branches
    used to omit it — the free queue and the review offer — and the free queue is the most common
    branch there is, so the rulebook's discriminator was missing exactly where it mattered and the
    rule got written wrong twice (rounds 2 and 3 of review).

    Scope, stated honestly: this walks the four task-bearing shapes that exist TODAY (free queue,
    stuck claim, active task, review offer) and fails if any of them drops `stage`. It is an
    enumeration, not a guarantee — a FIFTH branch added later without `stage` would not fail
    here, because nothing enforces the invariant structurally. Extend this test when you add a
    branch; that obligation is the whole point of keeping all four in one place."""
    api, wf = _env(wip_limit=3)

    free = api.add_task("free", "Queue")
    res = wf.next_task()
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False and res["stage"] == "Queue", "free queue lost its stage"

    stuck = api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task()
    assert res["task"]["id"] == stuck["id"]
    assert res["resume"] is True and res["stage"] == "Queue", "stuck-in-Queue lost its stage"

    wf.claim(stuck["id"])                                  # now MY active task, in Design
    res = wf.next_task()
    assert res["task"]["id"] == stuck["id"]
    assert res["resume"] is True and res["stage"] == "Design", "the active task lost its stage"

    theirs = api.add_task("someone else's work", "Review")
    api.add_comment(theirs["id"], "[worklog] done")
    res = wf.next_task(exclude=[stuck["id"]])
    assert res["task"]["id"] == theirs["id"]
    assert res["review"] is True and res["stage"] == "Review", "the review offer lost its stage"


# --- liveness accessors: what workspace --gc asks the tracker ---

def test_active_task_ids_lists_my_design_and_build_tasks():
    api, wf = _env()
    first = _hold(api, wf, "designing")
    second = _hold(api, wf, "building")
    wf.advance(second["id"], to="build", spec="approach")
    api.add_task("someone else's queue item", "Queue")
    assert sorted(wf.active_task_ids()) == sorted([first["id"], second["id"]])


def test_review_task_ids_includes_cards_i_do_not_own():
    """A review tree is alive while the CARD is in Review — the reviewer is never its
    assignee, so keying this off ownership would reap a running reviewer's tree."""
    api, wf = _env()
    mine = _hold(api, wf, "mine")
    wf.advance(mine["id"], to="build", spec="approach")
    wf.advance(mine["id"], to="review", worklog="done", evidence="abc1234")
    theirs = api.add_task("theirs", "Review")
    assert sorted(wf.review_task_ids()) == sorted([mine["id"], theirs["id"]])


def test_parked_task_ids_lists_your_call_cards_and_nothing_else():
    """VMCP-68: NOT a liveness set — a parked card's tree is dead on purpose. `workspace --gc`
    reads it to GRADE its refusals: the same "unpushed commits" refusal is routine while a human
    still owes the card an answer, and an alarm anywhere else. So it must name exactly the Your
    Call column: an active task of mine and a card in Review must not leak into it."""
    api, wf = _env()
    parked = _hold(api, wf, "waiting on a human")
    wf.call_human(parked["id"], "which option do you want?")
    _hold(api, wf, "still mine, still active")
    api.add_task("under review", "Review")
    assert wf.parked_task_ids() == [parked["id"]]


def test_your_call_is_paged_exhaustively_on_the_liveness_board():
    """The truncation this set would otherwise die of: `require_titles` decides which buckets keep
    the pagination loop going, and a parked id left on an unread page reads as NOT parked — gc then
    grades a routine refusal as an alarm, quietly, and only on boards busy enough to fill a page.
    Squeeze the fake's page size to 1 (it mirrors the real client: non-required buckets are
    truncated to their first page) and require the SECOND parked card to still come back."""
    api, wf = _env()
    first = _hold(api, wf, "parked first")
    wf.call_human(first["id"], "?")
    second = _hold(api, wf, "parked second")
    wf.call_human(second["id"], "?")
    api.page_size = 1
    assert sorted(wf.parked_task_ids()) == sorted([first["id"], second["id"]])


def test_a_shared_liveness_board_serves_every_accessor_with_one_fetch():
    """Review finding (Important 4): gc_workspaces calls these accessors every tick — on
    FakeAPI's own view_tasks_calls counter, prove one liveness_board() fetch is enough for
    all of them, matching the #43 discipline next_task already follows for its own board reads.
    VMCP-68 added the third (parked_task_ids); it must ride the same fetch, since the whole read
    happens INSIDE the repo-wide flock every other agent's --release queues behind."""
    api, wf = _env()
    first = _hold(api, wf, "designing")
    parked = _hold(api, wf, "waiting on a human")
    wf.call_human(parked["id"], "which option?")
    api.add_task("under review", "Review")
    board = wf.liveness_board()
    calls_before = api.view_tasks_calls
    active = wf.active_task_ids(board=board)
    reviewing = wf.review_task_ids(board=board)
    waiting = wf.parked_task_ids(board=board)
    assert api.view_tasks_calls == calls_before          # NO extra fetch when a board is passed
    assert active == [first["id"]]
    assert len(reviewing) == 1
    assert waiting == [parked["id"]]


# --- VMCP-69 (517): the refusal names WHICH knob set the limit ---

@pytest.mark.parametrize("kwargs, holds, needle", [
    ({"wip_limit": 1}, 1, "`wip_limit` key"),
    ({"enforce_single_wip": True}, 1, "`enforce_single_wip = true`"),
    ({}, DEFAULT_WIP_LIMIT, "built-in default"),
])
def test_the_refusal_names_the_knob_that_set_the_limit(kwargs, holds, needle):
    """Three knobs can produce this refusal and they need three different next actions — edit a
    number, drop a legacy flag, or add a key that is not there at all. The message named
    `enforce_single_wip` back when that was the only one; since wip_limit it named nothing, so an
    agent hitting a surprising "WIP limit reached" had no breadcrumb pointing at the toml.

    Every case also asserts the toml is named, because "the default" is only actionable next to
    the file you would set it in."""
    api, wf = _env(**kwargs)
    for n in range(holds):
        _hold(api, wf, f"held {n}")
    extra = api.add_task("one too many", "Queue")

    with pytest.raises(WorkflowError) as err:
        wf.claim(extra["id"])

    assert needle in str(err.value)
    assert ".vikunja-mcp.toml" in str(err.value)


@pytest.mark.parametrize("kwargs", [
    {"wip_limit": 2}, {"enforce_single_wip": True}, {"enforce_single_wip": True, "wip_limit": 2},
    {},
])
def test_the_breadcrumb_cannot_drift_from_the_number_it_explains(kwargs):
    """A message naming the WRONG knob is worse than one naming none, and a second copy of the
    precedence if/elif is exactly how that happens. So ONE method resolves both and
    _effective_wip_limit is a view over it — split them again and this goes red."""
    _api, wf = _env(**kwargs)
    limit, origin = wf._wip_limit_with_origin()
    assert limit == wf._effective_wip_limit()
    assert origin


# --- VMCP-80 (529): the limit gates claim(), it is NOT an invariant on the active count ---

def _bounce_to_over_budget(api, wf, limit):
    """Fill every slot, then bounce a reviewed card back — the shape seen live at wip_limit 3.

    The bounce is what goes AROUND the gate: review_task needs no ownership and no claim, so
    the card lands back in Build regardless of how full the board is."""
    bounced = _hold(api, wf, "will be bounced")
    wf.advance(bounced["id"], to="build", spec="s")
    wf.advance(bounced["id"], to="review", worklog="w", evidence="sha")
    for n in range(limit):                       # the freed slot gets refilled, as the pump does
        _hold(api, wf, f"held {n}")
    assert wf.next_task()["wip"] == {"active": limit, "limit": limit, "free": 0}
    wf.review_task(bounced["id"], verdict="needs_work", report="not yet")
    return bounced


def test_a_review_bounce_pushes_active_past_the_limit():
    """The state VMCP-80 was filed about, reproduced rather than assumed: four active tasks at a
    limit of three. It is CORRECT — refusing rework at the limit would strand reviewed work — and
    the docs (SKILL.md, claim's docstring, CLAUDE.md, the drain design spec) now say so. This test
    is what makes those sentences true: change the code to make the overshoot impossible (gate
    review_task on the limit, or count only up to it) and the docs become lies while this goes
    red."""
    api, wf = _env(wip_limit=3)
    _bounce_to_over_budget(api, wf, 3)
    assert wf.next_task()["wip"] == {"active": 4, "limit": 3, "free": 0}


def test_a_second_bounce_overshoots_further():
    """`free` saturates at 0, so it cannot express "over budget by two" — pin that the raw count
    keeps climbing, which is the only thing that can."""
    api, wf = _env(wip_limit=2)
    first = _hold(api, wf, "first")
    second = _hold(api, wf, "second")
    for t in (first, second):
        wf.advance(t["id"], to="build", spec="s")
        wf.advance(t["id"], to="review", worklog="w", evidence="sha")
    _hold(api, wf, "a")
    _hold(api, wf, "b")
    wf.review_task(first["id"], verdict="needs_work", report="no")
    wf.review_task(second["id"], verdict="needs_work", report="no")
    assert wf.next_task()["wip"] == {"active": 4, "limit": 2, "free": 0}


def test_being_over_budget_does_not_loosen_the_claim_gate():
    """Documenting the overshoot must not read as permission to grow it: claim still refuses, and
    reports the TRUE count (4/3), not a clamped 3/3."""
    api, wf = _env(wip_limit=3)
    _bounce_to_over_budget(api, wf, 3)
    extra = api.add_task("one too many", "Queue")
    with pytest.raises(WorkflowError, match=r"WIP limit reached \(4/3\)"):
        wf.claim(extra["id"])


def test_the_overshoot_clears_when_the_rework_reaches_review():
    """The docs promise it resolves itself rather than needing a human to 'fix the board'."""
    api, wf = _env(wip_limit=3)
    bounced = _bounce_to_over_budget(api, wf, 3)
    wf.advance(bounced["id"], to="review", worklog="reworked", evidence="sha2")
    assert wf.next_task()["wip"] == {"active": 3, "limit": 3, "free": 0}


def test_advance_to_build_cannot_overshoot_because_design_is_already_active():
    """VMCP-80's dossier GUESSED that advance(to='build') on a Your Call answer was a second
    overshoot path ("presumably the same shape"). It is not, and the docs now say so, so pin the
    correction: Design and Build are both ACTIVE_STAGES, so advance moves the card between two
    counted stages and leaves `active` untouched. The overshoot on that path arrives one step
    earlier — when the HUMAN moves the card out of Your Call, which no tool mediates."""
    api, wf = _env(wip_limit=3)
    parked = _hold(api, wf, "will be parked")
    wf.call_human(parked["id"], question="which way?")
    assert wf.next_task()["wip"]["active"] == 0        # Your Call is not an active stage
    for n in range(3):                                 # the pump refills the freed slot
        _hold(api, wf, f"held {n}")

    view = api.kanban_view(3)
    api.move_task(3, view["id"], api.bucket_id("Design"), parked["id"])   # the human's move
    assert wf.next_task()["wip"] == {"active": 4, "limit": 3, "free": 0}

    wf.advance(parked["id"], to="build", spec="carrying on")
    assert wf.next_task()["wip"] == {"active": 4, "limit": 3, "free": 0}  # unchanged by advance


def test_lowering_the_configured_limit_puts_a_still_board_over_budget():
    """The third path, and the one no card-move explains: nothing on the board changed, the
    NUMBER did. It is why the docs frame active > limit as a state to read, not an event to
    trace back to a transition."""
    api, wf = _env(wip_limit=3)
    for n in range(3):
        _hold(api, wf, f"held {n}")
    assert wf.next_task()["wip"] == {"active": 3, "limit": 3, "free": 0}
    wf.wip_limit = 1                                   # a human edits the repo toml
    assert wf.next_task()["wip"] == {"active": 3, "limit": 1, "free": 0}


def test_the_resume_note_discloses_an_over_budget_board():
    """VMCP-80 scope item 3, decided YES: the pump branches on `wip.free`, and free is
    max(0, limit - active), so `free: 0` cannot distinguish "exactly full" from "over budget".
    The resume branch is where that matters — the card it hands back IS the rework that caused
    the overshoot — so the note states it there, at the moment of the decision."""
    api, wf = _env(wip_limit=3)
    _bounce_to_over_budget(api, wf, 3)
    note = wf.next_task()["note"]
    assert "4 active tasks against a limit of 3" in note
    assert "legitimate, NOT board corruption" in note
    assert "Drain the rework" in note


def test_the_resume_note_says_nothing_extra_at_or_below_the_limit():
    """The no-noise half of that decision, and the half a reviewer should distrust most: the
    over-budget clause must be absent whenever active <= limit. Drop the
    `if wip["active"] > limit` guard (append unconditionally) and this goes red while the test
    above stays green — that is the property, and it is asserted on BOTH envs.

    #527 narrowed the "byte-for-byte the old note" form this test used to take. The exactly-full
    env below sits at free == 0, and that state now appends its OWN clause (saturation is only
    reported once `exclude` is complete — see
    test_a_resume_at_zero_free_slots_tells_the_pump_to_check_its_exclude). The two clauses are
    independent and both correct there: "you are over budget" and "you are at zero free slots"
    answer different questions. So the byte-identical check stays where it is still the truth —
    the under-limit env, which has a free slot and therefore no clause of either kind."""
    api, wf = _env(wip_limit=3)
    _hold(api, wf, "just one")
    at_limit = _env(wip_limit=1)
    _hold(*at_limit, "exactly full")

    for wf_under in (wf, at_limit[1]):
        assert "against a limit of" not in wf_under.next_task()["note"]

    under_note = wf.next_task()["note"]
    assert wf.next_task()["wip"]["free"] > 0, "precondition: this env is genuinely under-limit"
    assert under_note.endswith("continue from where it left off"), under_note


# --- the free == 0 note is the base plus the ENUMERATED clauses, and nothing else (VMCP-106) ---
#
# Why literals live here. #529 pinned the resume note byte-exact on BOTH envs
# (`endswith("continue from where it left off")`), and at the exactly-full env that doubled as a
# no-noise-at-all guarantee: nothing may be appended at free == 0. #527 legitimately appends its
# own clause exactly there, so the endswith had to move to the under-limit env — and the narrower
# "and nothing ELSE is appended at free == 0" went with it. Measured, not argued (VMCP-106): in
# that state a third, unconditional clause added to workflow.py's free == 0 branch passed the
# whole suite, 582 tests, exit 0.
#
# The restored pin is a DIFFERENTIAL, and its two sides come from deliberately different places:
#   left  — the note workflow.py actually produces at free == 0;
#   right — the note an UNDER-LIMIT env produces (the one state that appends nothing, so it is the
#           bare base) + the clause text below, which this test file OWNS.
# That is what lets the sides disagree: a clause the code grows shows up on the left only. Lifting
# the clauses into constants in workflow.py and importing them here would put both sides on one
# source and pin nothing — a clause written INTO an existing constant would move both sides
# together and stay green. Counting clauses by splitting on the ". NOTE" marker was rejected for
# the same reason in reverse: it pins a naming convention the code never promised, and the probe
# above used a different marker, so it would have slipped straight through.
#
# The base is READ from the other env rather than copied so that a wording change to the shared
# prose stays a one-file edit. That trade has a price, and this states it in BOTH directions,
# because the sentence that used to stand here — "an UNCONDITIONAL clause cannot hide inside it" —
# was FALSE as written (VMCP-115); only its stated mechanism was true, and that mechanism is
# narrower than the sentence. Measured, each round on a pristine workflow.py:
#   CAUGHT by the endswith — a clause APPENDED on every resume, anywhere in this branch. It moves
#     the base's own ending, so _clause_free_base fails before any equality is reached: appended
#     straight after the base, and appended after BOTH `if` blocks, each 9 of this file's 47.
#   CAUGHT by the equality — an insertion INTO the base, mid-base included, that renders
#     differently in the under-limit env than at free == 0, because the two sides then move apart.
#     Measured with a mid-base insertion guarded on `stage == "Build"`: 4 failed, every [Build].
#   NOT CAUGHT — an insertion into the MIDDLE of the base that renders the SAME in both envs. The
#     tail is untouched, so both sides move together and the differential cancels: the WHOLE unit
#     suite passes, exit 0 (703 of 703 when this was written), while the rendered note visibly
#     changed. That blind spot is deliberately left open.
#     Closing it means pinning the base byte-exact, which is the copy this file rejected three
#     paragraphs up — every UX wording edit would become a test edit. And nothing was LOST to it:
#     #529's original `endswith` would have passed the same insertion too. What this pin is FOR is
#     clause GROWTH at the two append sites; a mid-base edit is one literal, read as copy.
#
# Both tests run over BOTH resume stages AND over an empty/non-empty `exclude`. An equality table
# is a set of points, not a property, so it only sees a clause keyed on a dimension some point
# actually varies — and it CLOSES nothing, because the variables in scope at the append site are
# open-ended. It spans exactly two, each added after a measured escape, not after an argument:
#   `stage` — as first written both envs handed back a DESIGN card, so a clause guarded by
#     `stage == "Build"` at free == 0 passed the whole suite (VMCP-106).
#   `excluded` — both tests then called next_task() with NO argument, so before this axis existed
#     a clause guarded by `if excluded:` passed the whole suite too (694 passed, exit 0 —
#     VMCP-115); with the axis it takes down all four [exclude=[live]] rows. It is the sharper
#     miss of the two, because #527's clause is literally ABOUT `exclude`, which makes `excluded`
#     the likeliest thing for the next clause here to key on. The axis is on BOTH tests, not just
#     the exactly-full one, and that is measured too: a clause needing `excluded and
#     wip["active"] > limit` fails ONLY the over-budget [exclude=[live]] rows (2 of 47), so a
#     table whose one non-empty exclude sat at the limit would have let it through.
# NOT spanned, and measured green rather than assumed: a clause keyed on the offered task's
# PRIORITY passes the whole suite, exit 0, and so does one keyed on `rework_first` (703 each,
# same tree as above — the red counts in this comment are per-FILE and stable, the green ones are
# whole-suite and will drift as the suite grows; re-measure, don't trust the digits). Neither
# guard can fire in any env here — measured, not inferred: every task these fixtures build reports
# priority 0 and no relations at all, so `rework_first` is always the empty set. `mine`, the task's
# labels and the board itself are in scope at the same point and are not spanned either. A clause
# keyed on any of them is invisible to this table; the answer is another row, not a bigger claim
# about the rows already here.
# The `res["stage"] == stage` and `res["wip"] == ...` assertions are what hold the rows apart —
# without them a reordering could quietly collapse the cases back onto one point.

_BASE_TAIL = "continue from where it left off"

_ZERO_FREE_CLAUSE = (
    ". NOTE: wip.free == 0 AND a resume, with no wip_saturated — saturation is only reported "
    "once `exclude` names every task you already have a live agent on, because your active "
    "tasks are offered BEFORE the slot check. So check your exclude, not the board: if an agent "
    "IS live on this task your exclude is incomplete — add this id and call next_task again "
    "(that is how the saturation signal appears), and do NOT dispatch a second agent onto it. "
    "If no agent is live on it, this is the ordinary crash-recovery resume"
)

# spelled with the numbers the over-budget env below actually reaches (4 active, limit 3): the
# clause interpolates them, and pinning the rendered form keeps the breadcrumb honest here too.
_OVER_BUDGET_CLAUSE_4_OF_3 = (
    ". NOTE — you hold 4 active tasks against a limit of 3: that is legitimate, NOT board "
    "corruption. The limit gates claim(); a card bounced back by review_task(verdict='needs_work') "
    "or moved out of Your Call by a human re-enters Build without passing it, and rework outranks "
    "a fresh claim. Drain the rework — the overshoot clears when it reaches Review. Don't 'fix' "
    "the board and don't call_human about it"
)


def _clause_free_base() -> str:
    """The resume note in the ONE state that appends nothing: under the limit, a slot free.

    This is the right-hand side's base, and it is fetched from a SEPARATE env on purpose — see the
    block comment above. The endswith is the anchor that keeps the differential honest."""
    api, wf = _env(wip_limit=3)
    _hold(api, wf, "just one")
    res = wf.next_task()
    assert res["wip"]["free"] > 0, "precondition: this env must append no clause at all"
    assert res["note"].endswith(_BASE_TAIL), res["note"]
    return res["note"]


@pytest.mark.parametrize("excluded", [False, True], ids=["exclude=[]", "exclude=[live]"])
@pytest.mark.parametrize("stage", ["Design", "Build"])
def test_the_exactly_full_resume_note_is_the_base_plus_527s_clause_and_nothing_else(
    stage, excluded
):
    """At free == 0 and active == limit exactly ONE clause is justified — #527's "check your
    exclude, not the board". Equality, not `in`: this is the assertion that goes red when a third
    clause is appended there, which is the coverage VMCP-106 was filed to restore. The `wip`
    check first is not decoration — it proves the env really is the exactly-full one, so a
    refactor that quietly moved this env under the limit could not turn the test into a
    restatement of the base."""
    # The non-empty-exclude row needs a SECOND held task: at wip_limit 1 excluding the only card
    # leaves nothing offerable and no resume at all, so it is exactly-full at 2 instead. What it
    # excludes is the card next_task would otherwise hand back, which is the real pump shape —
    # an agent is already live on it (#527's clause is about exactly that).
    limit = 2 if excluded else 1
    api, wf = _env(wip_limit=limit)
    held = [_hold(api, wf, f"exactly full {n}") for n in range(limit)]
    if stage == "Build":
        for task in held:
            wf.advance(task["id"], to="build", spec="carrying on")
    skip = [wf.next_task()["task"]["id"]] if excluded else []
    res = wf.next_task(exclude=skip)
    assert res["wip"] == {"active": limit, "limit": limit, "free": 0}
    assert res["stage"] == stage
    assert res["note"] == _clause_free_base() + _ZERO_FREE_CLAUSE, res["note"]


@pytest.mark.parametrize("excluded", [False, True], ids=["exclude=[]", "exclude=[live]"])
@pytest.mark.parametrize("stage", ["Design", "Build"])
def test_the_over_budget_resume_note_is_the_base_plus_both_clauses_in_that_order(stage, excluded):
    """The other half of free == 0, where BOTH clauses are legitimate — and the only test that
    says which comes first. Order carries meaning: the over-budget disclosure explains the state
    the pump is in, the exclude clause tells it what to do about the resume it just got, so the
    diagnosis precedes the instruction. Substring assertions (the three in
    test_the_resume_note_discloses_an_over_budget_board) cannot see order and cannot see a fourth
    clause; this can."""
    api, wf = _env(wip_limit=3)
    _bounce_to_over_budget(api, wf, 3)
    if stage == "Build":
        # EVERY active card has to move, not just the offered one: _my_active_tasks walks
        # ACTIVE_STAGES in order, so at equal priority a Design card always outranks a Build one
        # and advancing just the head merely promotes the next Design card behind it. (Measured —
        # the first draft of this test did exactly that and handed back Design anyway.)
        for tid in wf.active_task_ids():
            if api.stage_of(tid) == "Design":
                wf.advance(tid, to="build", spec="carrying on")
    # Same axis as the exactly-full test, and it has to be here too: a clause needing BOTH a
    # non-empty exclude and active > limit renders in no other row. Four active against a limit
    # of three means excluding one still leaves the stage under test offerable.
    skip = [wf.next_task()["task"]["id"]] if excluded else []
    res = wf.next_task(exclude=skip)
    assert res["wip"] == {"active": 4, "limit": 3, "free": 0}
    assert res["stage"] == stage
    assert res["note"] == (
        _clause_free_base() + _OVER_BUDGET_CLAUSE_4_OF_3 + _ZERO_FREE_CLAUSE
    ), res["note"]


# --- the STUCK-CLAIM branch at free == 0: the instruction it gives cannot be followed (#571) ---
#
# Same shape as the resume pins above, different fact. The stuck branch ("assigned to you, still in
# Queue — call claim") also outranks the slot guard, so at free == 0 it hands back an instruction
# the WIP gate will refuse, with no wip_saturated to explain why. What it must NOT say is #527's
# "check your exclude": this branch is only reached when `offerable` is empty, i.e. every active
# task of the caller is ALREADY excluded — so that ambiguity is structurally impossible here and
# copying the resume clause would send the pump auditing a set that is by construction complete.
#
# The literals below are OWNED BY THIS FILE for the reason spelled out at the top of the previous
# section: importing them from workflow.py would put both sides of the differential on one source,
# and a clause written INTO the constant would move both sides together and stay green.

_STUCK_BASE_TAIL = "call claim(task_id) to finish moving it into Design"

_STUCK_ZERO_FREE_CLAUSE = (
    ". NOTE: wip.free == 0, so claim(task_id) will be REFUSED right now (\"WIP limit reached\") — "
    "the slot gate stands between this instruction and Design. And no wip_saturated is reported "
    "because this branch is offered BEFORE the slot check, so the state is read from your own set, "
    "not the board: put this id in `exclude` for the rest of the tick and call next_task again — "
    "that is how the saturation signal appears. Do NOT dispatch an agent onto it: nothing has been "
    "claimed, and the card stays claimable once a slot frees"
)


def _stuck_free_base() -> str:
    """The stuck-claim note in the ONE state that appends nothing: a free slot.

    Mirrors _clause_free_base for the other resume-shaped branch — read from a SEPARATE env so a
    wording change to the shared prose stays a one-file edit, with the endswith as the anchor that
    keeps the differential honest (a clause appended UNCONDITIONALLY moves this ending and fails
    here instead of hiding inside the base)."""
    api, wf = _env(wip_limit=3)
    api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task()
    assert res["wip"]["free"] > 0, "precondition: this env must append no clause at all"
    assert res["note"].endswith(_STUCK_BASE_TAIL), res["note"]
    return res["note"]


def test_a_stuck_claim_at_zero_free_slots_says_the_claim_will_be_refused():
    """#571: the payload's own instruction is un-followable in this state, and nothing else says so.

    The pump is told "call claim(task_id)" while the WIP gate is standing right behind it, and no
    wip_saturated came with the offer because this branch is checked BEFORE the slot guard. Without
    the clause the pump walks into a "WIP limit reached" refusal it had no way to predict, and the
    saturation signal it actually needs never appears — that only happens once this id is in
    `exclude` and next_task is asked again. Pinned as the ACTION the note must demand, not as
    incidental wording, so a rewrite that keeps the instruction stays green."""
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task(exclude=[held["id"]])
    assert res["resume"] is True and res["stage"] == "Queue"
    assert "wip_saturated" not in res
    assert res["wip"]["free"] == 0
    assert "put this id in `exclude` for the rest of the tick and call next_task again" \
        in res["note"]
    assert "Do NOT dispatch an agent onto it" in res["note"]


def test_the_stuck_claim_note_is_unchanged_when_a_slot_is_free():
    """The no-noise half, and the one a reviewer should distrust most: at free > 0 the instruction
    is followable — claim() will go through — so the clause would be pure noise on the ordinary
    unfinished-claim tick. Drop the `if wip["free"] == 0` guard in workflow.py (append
    unconditionally) and this goes red while the test above stays green; that is the property."""
    api, wf = _env(wip_limit=3)
    stuck = api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True and res["task"]["id"] == stuck["id"]
    assert res["wip"]["free"] > 0
    assert res["note"].endswith(_STUCK_BASE_TAIL), res["note"]
    assert "wip.free == 0" not in res["note"]
    assert _STUCK_ZERO_FREE_CLAUSE not in res["note"]


def test_the_zero_free_stuck_note_is_the_base_plus_571s_clause_and_nothing_else():
    """Equality, not `in`: the same coverage VMCP-106 restored for the resume branch, applied to
    this one before it can be lost the same way. A future clause appended here — conditional or
    not — shows up on the left-hand side only and goes red, because the right-hand base is read
    from the free > 0 env that appends nothing. The `wip` check first is not decoration: it proves
    the env really is the saturated one, so a refactor that quietly gave it a free slot could not
    turn this into a restatement of the base."""
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task(exclude=[held["id"]])
    assert res["wip"] == {"active": 1, "limit": 1, "free": 0}
    assert res["stage"] == "Queue"
    assert res["note"] == _stuck_free_base() + _STUCK_ZERO_FREE_CLAUSE, res["note"]
