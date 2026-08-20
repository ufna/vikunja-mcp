import os
import time

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.formatting import html_to_text
from vikunja_mcp.workflow import (
    _ATTACHMENT_TTL,
    _MAX_ATTACHMENT_NAME_BYTES,
    STAGES,
    Workflow,
    WorkflowError,
    _human_size,
    _safe_attachment_name,
)


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = api.add_task("job", "Design", assignee=api.me_user)
    return api, wf, task


def test_advance_to_done_is_forbidden(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="human"):
        wf.advance(t["id"], to="done")


def test_advance_unknown_stage(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="invalid"):
        wf.advance(t["id"], to="review2")


def test_advance_build_requires_spec(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="spec"):
        wf.advance(t["id"], to="build", spec="   ")
    wf.advance(t["id"], to="build", spec="сделаю X через Y")
    assert api.stage_of(t["id"]) == "Build"
    assert any(c.startswith("[spec]") for c in api.comments_text(t["id"]))


def test_advance_review_requires_worklog_and_evidence(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError, match="worklog"):
        wf.advance(t["id"], to="review", worklog="сделано")
    wf.advance(t["id"], to="review", worklog="сделано", evidence="commit abc123")
    assert api.stage_of(t["id"]) == "Review"
    joined = "\n".join(api.comments_text(t["id"]))
    assert "[worklog]" in joined and "commit abc123" in joined


def test_advance_review_report_includes_root_cause(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(
        t["id"], to="review",
        worklog="починил рендер титула", evidence="commit deadbeef",
        root_cause="стейт лобби не подписан на смену экипировки",
    )
    report = next(c for c in api.comments_text(t["id"]) if c.startswith("[worklog]"))
    assert "Root cause: стейт лобби не подписан" in report
    assert "Worklog: починил рендер титула" in report
    assert "Evidence: commit deadbeef" in report


def test_advance_wrong_source_stage(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="Build"):
        wf.advance(t["id"], to="review", worklog="w", evidence="e")  # задача ещё в Design


def test_advance_requires_ownership(env):
    api, wf, t = env
    api.tasks[t["id"]]["assignees"] = [{"id": 9, "username": "other"}]
    with pytest.raises(WorkflowError, match="claim"):
        wf.advance(t["id"], to="build", spec="s")


def test_call_human_keeps_assignee(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="question"):
        wf.call_human(t["id"], question="")
    wf.call_human(t["id"], question="какой из двух вариантов деплоя выбрать?")
    assert api.stage_of(t["id"]) == "Your Call"
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert any(c.startswith("[needs-human]") for c in api.comments_text(t["id"]))


def test_return_task_unassigns_labels_and_moves_to_backlog(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="reason"):
        wf.return_task(t["id"], reason="")
    wf.return_task(t["id"], reason="нужен доступ к prod-базе")
    assert api.stage_of(t["id"]) == "Backlog"
    assert api.tasks[t["id"]]["assignees"] == []
    assert any(lb["title"] == "blocked" for lb in api.tasks[t["id"]]["labels"])
    assert any(c.startswith("[blocked]") for c in api.comments_text(t["id"]))


def test_return_task_refuses_from_review_and_still_works_everywhere_else(env):
    """#590: `return_task` had NO stage gate at all — measured through the real `Workflow` over a
    FakeAPI board (not a live tracker), a card in Review passed with NO refusal and walked to
    Backlog, unassigned and labeled `blocked`. That is the door a reviewer reaches
    for once `call_human` refuses them (it is gated to Design/Build), and it quietly evicts someone
    else's finished, reviewed work from the pipeline for human re-triage.

    The gate is Review-ONLY and sits BEFORE `_require_mine`, so the multi-identity reviewer — whose
    card is the IMPLEMENTER's — reads the same stage refusal instead of "claim it first", advice
    that would be actively wrong (you never claim work you are reviewing).

    #627: that ORDER sentence used to live in this docstring as prose only — every card built here
    was `assignee=api.me_user`, so the "in Review AND not mine" cell no one asserted was the one
    cell the order decides. RE-MEASURED for VMCP-159 (665) at dcd31ef^, the commit's own parent —
    named, because this sentence used to say "on this tree" and quote a whole-suite pass total that
    does not reproduce even there: inverting the order (`_require_mine` first) killed NOTHING,
    0 failed against an unmutated control of 0 failed, with all three pins of #590 among the green
    (three re-counted here, not inherited: this test, the `call_human` sibling below, and
    test_skill_contract's rulebook pin). WITH the branch below in place that same inversion is
    2 failed against a control that is likewise 0 failed, and the two are this test and
    test_return_task_refuses_from_done_the_human_only_transition_run_backwards. Those two NAMES are
    the durable half, and this paragraph is its own warning about the rest: the kill count moved
    from 0 to 2 for TWO reasons, and BOTH pins postdate the base point — the branch below is
    dcd31ef's own (delete it alone and the same inversion is 1 failed, measured), and the second
    kill is the Done sibling, added later by a different card, #626 (6ac1454, NOT an ancestor of
    dcd31ef^), pinning the same mutant on a multi-identity branch of its own. So the suite really
    did grow between the two measurements, and the warning survives that: both kills are deliberate
    pins rather than blind growth, which is why the NAMES are recorded and no total is — a total
    moves with EVERY test the repo adds, so it could not have told a deliberate pin from blind
    growth (CLAUDE.md: "Record the FAILED count, never the pass total"). The branch below mirrors
    the sibling assertion already in the `call_human` test — the two tools share a ruling, so they
    must share a pin.

    All four parts below are load-bearing. The refusal must leave the BOARD untouched: a guard that
    raises after the comment/label/unassign already landed is not a guard. The CONTROL sweep is
    what makes the "must NOT happen" half mean anything — without it the first assertion stays
    green for a `return_task` that raises unconditionally, which is not the ruling: every stage
    except Review keeps working, because returning a half-claimed Queue/Design/Build card really is
    the "externally blocked" case this tool exists for. And the OWNERSHIP control is what makes the
    order assertion mean anything: `"not assigned to you" not in msg` names a guard it does not
    exercise, so on its own it would stay green for a `return_task` with no `_require_mine` AT ALL
    — re-measured at that same dcd31ef^, deleting the guard killed nothing either: 0 failed against
    an unmutated control of 0 failed. Together the two say what one cannot: BOTH guards are live,
    and in their intersection STAGE wins.

    MUTATION-CHECKED: control PASS; delete the Review gate -> FAIL (the card walks to Backlog);
    invert the order (`_require_mine` first) -> FAIL on the multi-identity branch; delete
    `_require_mine` -> FAIL on the ownership control."""
    api, wf, _t = env
    reviewed = api.add_task("someone else's finished work", "Review", assignee=api.me_user)

    with pytest.raises(WorkflowError) as excinfo:
        wf.return_task(reviewed["id"], reason="я не понимаю, чего от меня хотят")
    msg = str(excinfo.value)
    assert "review_task" in msg and "needs_work" in msg, \
        f"the refusal must name the reviewer's actual channel, not just say no: {msg}"

    # nothing happened: the gate fires BEFORE the comment / label / unassign / move
    assert api.stage_of(reviewed["id"]) == "Review"
    assert api.tasks[reviewed["id"]]["assignees"], "the refused return still unassigned the card"
    assert not any(lb["title"] == "blocked" for lb in api.tasks[reviewed["id"]]["labels"])
    assert not any(c.startswith("[blocked]") for c in api.comments_text(reviewed["id"]))

    # multi-identity: the card in Review is the IMPLEMENTER's. THIS is the cell that pins the
    # check ORDER — under the inverted order the reviewer reads "claim it first" and never hears
    # about the stage or about the channel that actually works.
    theirs = api.add_task("someone else's card in review", "Review")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.return_task(theirs["id"], reason="я не понимаю, чего от меня хотят")
    multi_msg = str(multi.value)
    assert "review_task" in multi_msg and "needs_work" in multi_msg, \
        f"the reviewer must read the STAGE refusal, not an ownership one: {multi_msg}"
    assert "not assigned to you" not in multi_msg, \
        f"ownership ran first — 'claim it first' is the wrong advice for a reviewer: {multi_msg}"
    assert api.stage_of(theirs["id"]) == "Review"
    assert api.tasks[theirs["id"]]["assignees"][0]["id"] == 77

    # OWNERSHIP CONTROL: the assertion above is a NEGATIVE one about a guard it never reaches, so
    # pin that the guard is still there and still runs — from an OPEN stage, where the Review gate
    # cannot mask it, someone else's card is still refused BY OWNERSHIP.
    not_mine = api.add_task("someone else's card in Design", "Design")
    api.tasks[not_mine["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as owned:
        wf.return_task(not_mine["id"], reason="чужой сервис лежит")
    assert "not assigned to you" in str(owned.value), \
        f"_require_mine no longer guards return_task from an open stage: {owned.value}"
    assert api.stage_of(not_mine["id"]) == "Design"
    assert api.tasks[not_mine["id"]]["assignees"][0]["id"] == 77

    # CONTROL: every stage I deliberately left open still returns the card
    for stage in ("Design", "Build", "Queue", "Backlog", "Your Call"):
        open_task = api.add_task(f"blocked in {stage}", stage, assignee=api.me_user)
        wf.return_task(open_task["id"], reason="чужой сервис лежит")
        assert api.stage_of(open_task["id"]) == "Backlog", f"return_task broke from {stage}"
        assert api.tasks[open_task["id"]]["assignees"] == []
        assert any(lb["title"] == "blocked" for lb in api.tasks[open_task["id"]]["labels"])


def test_return_task_refuses_from_done_the_human_only_transition_run_backwards(env):
    """#626: `return_task` was ONE OF SEVERAL agent tools that moved a card OUT of Done — never
    the only one, and this test does not pin that it is. Measured through the real `Workflow` over
    a FakeAPI board, on a card driven the NORMAL way (Queue -> claim -> Design -> Build ->
    Review -> approve -> a human moves it to Done): it did not refuse, answered the success
    payload of the ONE `return` this method has — three keys, `moved_to`, `task_id` and
    `labeled`, in that order, reporting moved_to=Backlog and labeled=blocked — and left the card
    in Backlog with NO assignee and BOTH labels — `reviewed` and `blocked` — the board claiming
    "approved" and "blocked" at once. THAT PAIR IS #626'S MEASUREMENT AND NO LONGER REPRODUCIBLE
    HERE: #693 made `return_task` clear the verdict BEFORE it labels `blocked`, so lifting this
    gate today lands `['blocked']` alone — measured, on this very route. The paragraph stays in
    the past tense because it is what #626 ran; what changed is the after-state a reader gets by
    re-running it, which is why the refusal below no longer names that pair either.
    On that same card advance (build/review/done), call_human,
    claim and review_task (both verdicts) all refuse — the transition CLAUDE.md calls human-only,
    run BACKWARDS, and an invariant that holds in only one direction is not an invariant.

    THAT PAYLOAD IS DESCRIBED, NOT TRANSCRIBED, and #674 made it so deliberately. It stood here
    as a two-key dict literal that looked verbatim and was not — the real answer carries
    `task_id` between the other two. Pasting the run's literal back would have cured THAT much
    and was still the wrong repair, because the literal carries 107 as the value of `task_id`,
    and 107 belongs to the fixture rather than to the contract. FakeAPI draws ids from ONE shared
    `itertools.count(100)` that buckets, projects, views, labels, attachments and even comment
    timestamps consume as well (its real per-task counter is the differently-named
    `_task_index`), so a task is 107 only because the seven stage buckets took 100-106 ahead of
    it: built with two buckets the same first task is 102, and one full claim/advance/return_task
    cycle moves the NEXT task from 108 to 112 (measured, all three). What replaced the literal is
    re-derivable instead: `grep -n '"labeled": LABEL_BLOCKED' src/vikunja_mcp/workflow.py` has
    exactly one hit and it is that `return` — keep the file argument, because writing the pattern
    into this docstring put a second copy in the tree, so a bare `git grep` now answers this file
    too — and the key COUNT stated beside the key NAMES makes a dropped key self-inconsistent
    rather than invisible. Neither property is enforced by anything that runs, and that was
    measured rather than assumed: in an `rsync --exclude .venv` copy, with
    `vikunja_mcp.workflow.__file__` printed each round, restoring the wrong two-key list gave
    control 0 failed and mutant 0 failed over `tests/unit` — and again over the `.git`-dependent
    pins a copied tree skips, re-run in a clone. So do not "helpfully" restore a literal here:
    nothing goes red if you get it wrong again.

    Shutting this door does not shut them all: human-only Done is nowhere expressed as one rule,
    so any tool that moves a card without checking its stage reproduces the hole. `decompose`
    measurably did — on the same card it walked the parent to Backlog with `epic`, and THIS diff
    did not touch it — and was gated separately by #649, which closed the last instance known then
    without closing the class. An earlier draft of this docstring opened by calling
    return_task "the ONE agent tool that moves a Done card"; probing the tools the first repro had
    skipped disproved it, and the opening sentence above is the corrected one.

    Same FORM as the Review gate (#590): a stage check BEFORE `_require_mine`, with a refusal that
    names the channel that does work. Done is not "stuck", so the door it points at is `file_task`
    (a follow-up card for a human to triage) — `call_human` refuses from Done too, and only a human
    can move THIS card back.

    Not a regression, and that was RUN, not inherited from the card: the same probe against a
    shadow copy of 51ab50d^ (the parent of the commit that gated Review) prints the same three
    keys and the same `['reviewed', 'blocked']` after-state — the hole predates that card, which
    gated exactly Review. #674 re-ran both shadow trees rather than trusting this line: at
    51ab50d^ and at 6ac1454^ (the parent of the commit that gated Done, i.e. the state the first
    paragraph describes) the same probe still prints all three keys, `task_id` among them. So
    what this docstring got wrong was the rendering, not the behaviour it describes — the
    behaviour at those two shas did change, which is exactly what both commits were for.

    Four parts, all load-bearing, mirroring the sibling test above. The refusal must leave the
    BOARD untouched: a guard that raises after the comment/label/unassign already landed is not a
    guard. The multi-identity branch is what pins the check ORDER for the Done cell (the Review
    cell is pinned by #627), the OWNERSHIP control is what stops that negative assertion from
    passing for a `return_task` with no `_require_mine` at all, and the CONTROL sweep is what
    stops the whole test from passing for a `return_task` that refuses unconditionally — Your Call
    is deliberately in that sweep: unlike Done, that card is still the agent's OWN work in flight.

    MUTATION-CHECKED, each round naming the assertion it actually reddens (checked against the
    driver's raw output, because guessing the site is how a pin gets miscredited): control PASS;
    delete the Done gate -> FAIL at the first `pytest.raises` (the card walks to Backlog); put
    `_require_mine` before the stage gates -> FAIL on the multi-identity branch; delete
    `_require_mine` -> FAIL on the ownership control; widen the gate to ("Done", "Design") -> FAIL
    on the CONTROL sweep. An UNCONDITIONAL gate also fails, but at the ownership control, which it
    reaches first — so it is the widened mutant, not that one, that shows the sweep is live."""
    api, wf, _t = env

    # driven the NORMAL way, so the state the refusal protects is the real one
    accepted = api.add_task("work a human already accepted", "Queue")
    wf.claim(accepted["id"])
    wf.advance(accepted["id"], to="build", spec="сделаю X")
    wf.advance(accepted["id"], to="review", worklog="сделано", evidence="abc123")
    wf.review_task(accepted["id"], verdict="approve", report="ок")
    api.task_bucket[accepted["id"]] = api.bucket_id("Done")   # the HUMAN moves it — no tool can
    assert api.stage_of(accepted["id"]) == "Done"

    with pytest.raises(WorkflowError) as excinfo:
        wf.return_task(accepted["id"], reason="внешний блок")
    msg = str(excinfo.value)
    assert "Done" in msg and "file_task" in msg, \
        f"the refusal must say it is the human's transition and name the door that works: {msg}"

    # nothing happened: the gate fires BEFORE the comment / label / unassign / move
    assert api.stage_of(accepted["id"]) == "Done", "the refused return walked accepted work back"
    assert api.tasks[accepted["id"]]["assignees"], "the refused return still unassigned the card"
    labels = [lb["title"] for lb in api.tasks[accepted["id"]]["labels"]]
    # NOT "the board now says approved AND blocked at once": since #693 the verdict is cleared
    # BEFORE `blocked` goes on, so the state this message fires in is `['blocked']` ALONE — the
    # acceptance ERASED rather than contradicted, which is what the refusal itself now names.
    assert "blocked" not in labels, f"the refused return labelled accepted work `blocked`: {labels}"
    assert "reviewed" in labels, "the verdict label vanished — the human's acceptance with it"
    assert not any(c.startswith("[blocked]") for c in api.comments_text(accepted["id"]))

    # multi-identity: someone else's accepted card. THIS is the cell that pins the check ORDER —
    # under the inverted order the caller reads "claim it first", which for a card in Done is the
    # one thing that can never be right.
    theirs = api.add_task("someone else's accepted card", "Done")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.return_task(theirs["id"], reason="внешний блок")
    multi_msg = str(multi.value)
    assert "Done" in multi_msg and "file_task" in multi_msg, \
        f"the caller must read the STAGE refusal, not an ownership one: {multi_msg}"
    assert "not assigned to you" not in multi_msg, \
        f"ownership ran first — 'claim it first' is never the answer for a Done card: {multi_msg}"
    assert api.stage_of(theirs["id"]) == "Done"
    assert api.tasks[theirs["id"]]["assignees"][0]["id"] == 77

    # OWNERSHIP CONTROL: the assertion above is a NEGATIVE one about a guard it never reaches, so
    # pin that the guard is still live — from an OPEN stage someone else's card is refused BY
    # OWNERSHIP.
    not_mine = api.add_task("someone else's card in Build", "Build")
    api.tasks[not_mine["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as owned:
        wf.return_task(not_mine["id"], reason="чужой сервис лежит")
    assert "not assigned to you" in str(owned.value), \
        f"_require_mine no longer guards return_task from an open stage: {owned.value}"

    # CONTROL: the five stages that stay open still return the card — the gate is Review+Done,
    # not "everything above Build"
    for stage in ("Design", "Build", "Queue", "Backlog", "Your Call"):
        open_task = api.add_task(f"blocked in {stage}", stage, assignee=api.me_user)
        wf.return_task(open_task["id"], reason="чужой сервис лежит")
        assert api.stage_of(open_task["id"]) == "Backlog", f"return_task broke from {stage}"


def test_decompose_refuses_from_review_including_a_card_already_approved(env):
    """#663: the hole #649 measured on this very tool and deliberately left open — `decompose`
    walking a card out of REVIEW, which is the shape #590 gated for `return_task`. Measured through
    the real `Workflow` over a FakeAPI board (not a live tracker), on a card driven the NORMAL way
    (Queue -> claim -> Design -> Build -> Review): it did not refuse, answered
    a success payload whose `parent` reported moved_to=Backlog, labeled=epic (beside the two
    `created` children), and left the card in Backlog with NO assignee and `epic`, two fresh
    children in Queue and a `[decompose]` comment — work that is under review, pulled out of the
    pipeline and re-declared an unfinished container before anyone ruled on it.

    The APPROVED cell below is the stronger measurement and is why this test does not stop at a
    bare Review card: on a card whose review already returned `approve` (label `reviewed`, waiting
    only for a human's Done) the same run produced `reviewed` AND `epic` at once — the end state
    #649's Done gate exists to prevent, reached one stage earlier, by a card the human has not
    touched yet.

    Same FORM as #590/#626/#649: a stage check straight after `_find_task` and BEFORE
    `_require_mine`, refusing with the channel that does work — `review_task(verdict='needs_work')`,
    because deciding that work needs splitting is a Build-time call and its implementer owns it
    there. The placement is measured rather than tasteful: #649's own reasoning records that a
    guard inside `_move` fires only AFTER both children exist and `epic`/unassign have landed, so
    its refusal would lie to the caller. This gate is per-tool for the same reason #649's is, and
    the CLASS (no single rule anywhere for "stages an agent may not move a card out of") stays open
    by construction — filed as #662, out of this card's slice.

    Five parts, all load-bearing. The refusal must leave the board untouched AND create NOTHING:
    decompose's side effects start with children on the board, so a guard placed after them would
    leave orphans no refusal can take back. The multi-identity branch pins the check ORDER, and for
    this gate it is not hypothetical — measured before the gate, that cell answered "task 107 is
    not assigned to you — claim it first", which is the one answer a reviewer can never act on (you
    never claim work you are reviewing). The OWNERSHIP control stops that negative assertion from
    passing for a decompose with no `_require_mine` at all, and the CONTROL sweep stops the whole
    test from passing for a decompose that refuses unconditionally.

    MUTATION-CHECKED, each round naming the assertion it actually reddens, read out of pytest's raw
    output rather than guessed: control PASS; delete this Review gate -> FAIL at the first
    `pytest.raises` (DID NOT RAISE), while the #590/#626/#627/#649 pins stay GREEN on that same
    mutant, which is what shows this hole was a SEPARATE one and not a regression of theirs; put
    `_require_mine` before the stage gates -> FAIL on the multi-identity branch; delete
    `_require_mine` -> FAIL on the ownership control; widen the gate to ("Review", "Backlog") ->
    FAIL inside the sweep loop. Backlog is named deliberately: it is the sweep's FIRST stage, and
    #649 recorded why that matters — the ownership control's card stands in Build, so a mutant
    widened to Build (or an unconditional gate) reddens THERE instead and proves nothing about the
    sweep."""
    api, wf, _t = env

    # driven the NORMAL way, so the state the refusal protects is the real one
    reviewed = api.add_task("work under review", "Queue")
    wf.claim(reviewed["id"])
    wf.advance(reviewed["id"], to="build", spec="сделаю X")
    wf.advance(reviewed["id"], to="review", worklog="сделано", evidence="abc123")
    assert api.stage_of(reviewed["id"]) == "Review"
    cards_before = len(api.tasks)

    with pytest.raises(WorkflowError) as excinfo:
        wf.decompose(reviewed["id"], [{"title": "часть A"}, {"title": "часть B"}])
    msg = str(excinfo.value)
    assert "review_task" in msg and "needs_work" in msg, \
        f"the refusal must name the door back to Build, not just say no: {msg}"

    # nothing happened: the gate fires BEFORE any child is created and before label/unassign/move
    assert len(api.tasks) == cards_before, "the refused decompose still put children on the board"
    assert api.stage_of(reviewed["id"]) == "Review", "the refused decompose walked reviewed work out"
    assert api.tasks[reviewed["id"]]["assignees"], "the refused decompose still unassigned the card"
    assert not any(lb["title"] == "epic" for lb in api.tasks[reviewed["id"]]["labels"])
    assert not any(c.startswith("[decompose]") for c in api.comments_text(reviewed["id"]))

    # APPROVED and still in Review — the verdict is in, only a human's Done is missing. Measured
    # before the gate: this card came out of Backlog carrying `reviewed` AND `epic` at once.
    approved = api.add_task("approved, waiting for a human's Done", "Queue")
    wf.claim(approved["id"])
    wf.advance(approved["id"], to="build", spec="сделаю Y")
    wf.advance(approved["id"], to="review", worklog="сделано", evidence="def456")
    wf.review_task(approved["id"], verdict="approve", report="ок")
    cards_before = len(api.tasks)
    with pytest.raises(WorkflowError) as ok:
        wf.decompose(approved["id"], [{"title": "A"}, {"title": "B"}])
    assert "review_task" in str(ok.value) and "needs_work" in str(ok.value)
    assert len(api.tasks) == cards_before, "the refused decompose still put children on the board"
    assert api.stage_of(approved["id"]) == "Review"
    labels = [lb["title"] for lb in api.tasks[approved["id"]]["labels"]]
    assert "epic" not in labels, f"the board now says approved AND unfinished container: {labels}"
    assert "reviewed" in labels, "the verdict label vanished"

    # multi-identity: the card in Review is the IMPLEMENTER's. THIS is the cell that pins the check
    # ORDER — measured before the gate, it answered "not assigned to you — claim it first", advice
    # a reviewer must never be given.
    theirs = api.add_task("someone else's card in review", "Review")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.decompose(theirs["id"], [{"title": "A"}, {"title": "B"}])
    multi_msg = str(multi.value)
    assert "review_task" in multi_msg and "needs_work" in multi_msg, \
        f"the reviewer must read the STAGE refusal, not an ownership one: {multi_msg}"
    assert "not assigned to you" not in multi_msg, \
        f"ownership ran first — 'claim it first' is the wrong advice for a reviewer: {multi_msg}"
    assert api.stage_of(theirs["id"]) == "Review"
    assert api.tasks[theirs["id"]]["assignees"][0]["id"] == 77

    # OWNERSHIP CONTROL: the assertion above is a NEGATIVE one about a guard it never reaches, so
    # pin that the guard is still live — from an OPEN stage someone else's card is refused BY
    # OWNERSHIP.
    not_mine = api.add_task("someone else's card in Build", "Build")
    api.tasks[not_mine["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as owned:
        wf.decompose(not_mine["id"], [{"title": "A"}, {"title": "B"}])
    assert "not assigned to you" in str(owned.value), \
        f"_require_mine no longer guards decompose from an open stage: {owned.value}"

    # CONTROL: the five stages that stay open still decompose — the gate is Review+Done, not
    # "anything someone else might be looking at"
    for stage in ("Backlog", "Queue", "Design", "Build", "Your Call"):
        open_task = api.add_task(f"big job in {stage}", stage, assignee=api.me_user)
        wf.decompose(open_task["id"], [{"title": "A"}, {"title": "B"}])
        assert api.stage_of(open_task["id"]) == "Backlog", f"decompose broke from {stage}"
        assert any(lb["title"] == "epic" for lb in api.tasks[open_task["id"]]["labels"])


def test_decompose_refuses_from_done_the_other_half_of_the_same_bypass(env):
    """#649: the sibling hole #626 measured and deliberately left open — `decompose` walking a
    card OUT of Done — and the last instance of that bypass known when this landed. Measured
    through the real `Workflow` over a FakeAPI board, on a card driven the NORMAL way (Queue ->
    claim -> Design -> Build -> Review -> approve -> a human moves it to Done): it did not refuse,
    answered a success payload whose `parent` reported moved_to=Backlog, labeled=epic (beside the
    two `created` children — #663 re-ran this and corrected the shape, which used to be quoted
    here as if verbatim), and left the card in Backlog with NO assignee, carrying `reviewed` AND
    `epic` at once, with two fresh children in Queue — the board claiming work a human accepted is
    now an unfinished container.

    Not a regression, and that was RUN rather than inherited: at 51ab50d^ (the parent of #590's
    commit) `decompose` reads the same `_find_task` -> `_require_mine` with no stage check at all.

    WHY PER-TOOL AND NOT ONE SHARED GUARD — the alternative was weighed against the call sites
    rather than by taste, and the reasoning is here because the next reader will ask. The only
    chokepoint every card-touching tool shares is `_find_task`, and it also serves the READ paths
    (get_task/comment/download_attachment/attach_file): shutting Done there makes an accepted card
    unreadable, a worse regression than the hole. A guard inside `_move` cannot see the source
    stage (it would need a board fetch per call, N of them for N children) and would fire only
    AFTER the children exist and `epic`/unassign have landed — a guard that raises after the fact
    is not a guard. A shared `_refuse_if_done(stage, tool)` helper is still one call per tool: it
    de-duplicates the TEXT, not the obligation to call it, so it closes no class.

    Two corrections to that reasoning, both from the independent second pass, both kept because a
    rationale is worth less than the measurements under it. (1) An earlier draft added that such a
    helper would rewrite "five refusals #590/#626/#627 pin verbatim". That is false twice over and
    was DISPROVED by construction: exactly ONE refusal in this family is pinned by a literal
    string (`call_human`'s prefix, below), every other by tokens — and a Done helper would touch
    only the TWO Done refusals, not five. Collapsing every Done refusal into one shared message
    left the suite green. The first two reasons carry this decision; that third one never did.
    (2) The read-path objection rules out an UNPARAMETERISED gate, and only that: with a Done
    guard inside `_find_task`, get_task/comment/attach_file/download_attachment on an accepted
    card all refuse — measured, by building it. A PARAMETERISED one is constructible
    (`_find_task(..., *, allow_done=False)` with the read paths opting in), closes the class
    fail-closed, and was measured green. It is not rejected here as impossible; it is out of this
    card's slice, which is one instance. So the CLASS stays open by construction and is filed for
    a human's ruling as #662, that option included; this test pins the INSTANCE.

    Same FORM as #590/#626: a stage check straight after `_find_task` and BEFORE `_require_mine`,
    with a refusal that names the channel that does work — `file_task`, because work an accepted
    card revealed is NEW work, not a split of this one (call_human refuses from Done too).

    Five parts, all load-bearing. The refusal must leave the board untouched AND create NOTHING:
    decompose's side effects start with children on the board, so a guard placed after them would
    leave orphans no refusal can take back — that assertion is this test's own, not inherited from
    the return_task sibling. The multi-identity branch pins the check ORDER (before this gate that
    case answered "not assigned to you — claim it first", the one answer that can never be right
    for a card a human accepted), the OWNERSHIP control stops that negative assertion from passing
    for a decompose with no `_require_mine` at all, and the CONTROL sweep stops the whole test from
    passing for a decompose that refuses unconditionally. Review USED to be in that sweep, because
    `decompose` walked a card out of Review too — the same shape #590 gated for `return_task`,
    measured then, out of this card's slice (its subject is Done) and filed as #663. That card has
    since landed its own gate and its own test above, so the sweep here is over the FIVE stages
    that are still open.

    MUTATION-CHECKED, each round naming the assertion it actually reddens, read out of the
    driver's raw output rather than guessed — guessing the site is how a pin gets miscredited, and
    it happened here on the first pass, so the correction is kept: control PASS; delete the Done
    gate -> FAIL at the first `pytest.raises` (and the #590/#626/#627 pins stay GREEN on that same
    mutant, which is what shows this hole was a separate one); put `_require_mine` before the gate
    -> FAIL on the multi-identity branch; delete `_require_mine` -> FAIL on the ownership control.
    An UNCONDITIONAL gate fails on the OWNERSHIP CONTROL, not on the sweep, because it reaches that
    control first — and so does widening the gate to ("Done", "Build"), for the un-obvious reason
    that the ownership control's card stands in Build. The widening that actually proves the sweep
    is live is therefore ("Done", "Backlog"), Backlog being the sweep's first stage -> FAIL inside
    the sweep loop."""
    api, wf, _t = env

    # driven the NORMAL way, so the state the refusal protects is the real one
    accepted = api.add_task("work a human already accepted", "Queue")
    wf.claim(accepted["id"])
    wf.advance(accepted["id"], to="build", spec="сделаю X")
    wf.advance(accepted["id"], to="review", worklog="сделано", evidence="abc123")
    wf.review_task(accepted["id"], verdict="approve", report="ок")
    api.task_bucket[accepted["id"]] = api.bucket_id("Done")   # the HUMAN moves it — no tool can
    assert api.stage_of(accepted["id"]) == "Done"
    cards_before = len(api.tasks)

    with pytest.raises(WorkflowError) as excinfo:
        wf.decompose(accepted["id"], [{"title": "часть A"}, {"title": "часть B"}])
    msg = str(excinfo.value)
    assert "Done" in msg and "file_task" in msg, \
        f"the refusal must say it is the human's transition and name the door that works: {msg}"

    # nothing happened: the gate fires BEFORE any child is created and before label/unassign/move
    assert len(api.tasks) == cards_before, "the refused decompose still put children on the board"
    assert api.stage_of(accepted["id"]) == "Done", "the refused decompose walked accepted work back"
    assert api.tasks[accepted["id"]]["assignees"], "the refused decompose still unassigned the card"
    labels = [lb["title"] for lb in api.tasks[accepted["id"]]["labels"]]
    assert "epic" not in labels, f"the board now calls accepted work an epic container: {labels}"
    assert "reviewed" in labels, "the verdict label vanished"
    assert not any(c.startswith("[decompose]") for c in api.comments_text(accepted["id"]))

    # multi-identity: someone else's accepted card. THIS is the cell that pins the check ORDER —
    # under the inverted order the caller reads "claim it first", which for a card in Done is the
    # one thing that can never be right. Measured before the gate: that is exactly what it said.
    theirs = api.add_task("someone else's accepted card", "Done")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.decompose(theirs["id"], [{"title": "A"}, {"title": "B"}])
    multi_msg = str(multi.value)
    assert "Done" in multi_msg and "file_task" in multi_msg, \
        f"the caller must read the STAGE refusal, not an ownership one: {multi_msg}"
    assert "not assigned to you" not in multi_msg, \
        f"ownership ran first — 'claim it first' is never the answer for a Done card: {multi_msg}"
    assert api.stage_of(theirs["id"]) == "Done"
    assert api.tasks[theirs["id"]]["assignees"][0]["id"] == 77

    # OWNERSHIP CONTROL: the assertion above is a NEGATIVE one about a guard it never reaches, so
    # pin that the guard is still live — from an OPEN stage someone else's card is refused BY
    # OWNERSHIP.
    not_mine = api.add_task("someone else's card in Build", "Build")
    api.tasks[not_mine["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as owned:
        wf.decompose(not_mine["id"], [{"title": "A"}, {"title": "B"}])
    assert "not assigned to you" in str(owned.value), \
        f"_require_mine no longer guards decompose from an open stage: {owned.value}"

    # CONTROL: the five stages that stay open still decompose — the gate is Review+Done, not
    # "anything a human might be looking at"
    for stage in ("Backlog", "Queue", "Design", "Build", "Your Call"):
        open_task = api.add_task(f"big job in {stage}", stage, assignee=api.me_user)
        wf.decompose(open_task["id"], [{"title": "A"}, {"title": "B"}])
        assert api.stage_of(open_task["id"]) == "Backlog", f"decompose broke from {stage}"
        assert any(lb["title"] == "epic" for lb in api.tasks[open_task["id"]]["labels"])


def test_decompose_refusals_describe_the_verdict_they_would_actually_clear(env):
    """#777: both `decompose` refusals explained themselves through a counterfactual that #673
    had already made unreachable — "stack `epic` on top of `reviewed`" / "on the verdict label".

    THE FORM, and it is the reason this is a pin and not a typo fix. A refusal says why-not by
    naming what you WOULD get. #673 taught `decompose` to CLEAR the verdict on its way out, so
    the promised pair stopped being producible — but the text stayed, and it teaches the reader
    the opposite of the truth: that `decompose` PRESERVES a verdict. #693 fixed exactly this
    shape for `return_task` and this is the sibling. Nothing caught either: no test read these
    strings, so the whole suite was green with the false clause in place and is green without it.

    WHAT THIS ASSERTS is the pairing, not the wording. A counterfactual is checkable in exactly
    one honest way — remove the gate and look — so this test does BOTH halves in one run: it
    measures what the ungated tool does to a verdict-carrying card (the tool's own clearing path,
    reached from an OPEN stage, which is the same code the refusal is speculating about), and
    then requires each refusal's text to agree with that measurement rather than contradict it.
    Reworded prose stays green; prose that re-promises a surviving verdict does not.

    MEASURED before this test was written, on a live `Workflow` over `FakeAPI`, by neutralising
    both stage gates and running the tool from Review and from Done: the parent lands in Backlog,
    unassigned, `['bug', 'epic']` — the VERDICT gone and a non-verdict label untouched — with two
    children in Queue. So the new clause ("CLEAR the verdict label", "the human's acceptance
    would vanish") describes the run, and the old one did not.

    MUTATION-CHECKED, selection `tests/unit/test_workflow_gates.py`; control 0 failed:
      * restore the Review refusal's pre-#777 clause ("stack `epic` on top of the verdict
        label") -> 1 failed, here, on the survives-clause assert.
      * restore the Done refusal's pre-#777 clause ("stack `epic` on top of `reviewed`") ->
        1 failed, here, same assert.
      * delete `_clear_verdict_labels` from `decompose`'s body -> 3 failed, of which THIS test
        is one, on the behaviour assert — so the text half cannot pass while the behaviour half
        rots. The count is 3 and not 1 on purpose: #673's own pin and the verdict grid fire too,
        which is what makes this half a CONTROL on them rather than a fourth copy of them.
      * control again 0 failed, sources restored and sha256-verified against the pre-round copy.
    """
    api, wf, _t = env

    # HALF ONE — behaviour: the clearing the refusals speculate about is real, and it takes the
    # VERDICT only. Reached from an open stage, which is the same body a Review/Done card would
    # run through if its gate were lifted.
    carrier = api.add_task("carries a verdict and a kind label", "Build", assignee=api.me_user)
    api.tasks[carrier["id"]]["labels"] = [
        {"id": 901, "title": "reviewed"}, {"id": 902, "title": "bug"},
    ]
    wf.decompose(carrier["id"], [{"title": "A"}, {"title": "B"}])
    assert _label_titles(api, carrier["id"]) == ["bug", "epic"], (
        "decompose no longer clears the verdict, so both refusals below have gone back to "
        f"describing a state this tool does produce: {_label_titles(api, carrier['id'])}"
    )

    # HALF TWO — text: the refusal may not promise the verdict SURVIVES the split.
    #
    # REVIEW ONLY since #662, and the narrowing is DELIBERATE rather than a test bending to the
    # code. Done used to be the second half of this loop and asserted the same `CLEAR` clause on
    # `decompose`'s own Done refusal. That refusal no longer exists: #662 put human-only Done in
    # `_find_task` as ONE rule and deleted the per-tool gate it made dead, so from Done every
    # tool now reads one shared message. The price is exactly the "flattened prescriptive
    # routing" #662's description weighed and a human accepted — a shared message cannot name a
    # consequence only ONE tool has ("this call would clear your verdict"), because it is not
    # true of `get_task` or of `claim`. What replaces the cover is not nothing: the Done side is
    # pinned by the meta-test over `server._DEFERRED_TOOLS`, which asks the stronger question
    # (does EVERY mutating tool refuse from Done, and does every reading one still work) instead
    # of this narrower one about decompose's wording. So this loop keeps the stage where the
    # per-tool refusal is still a per-tool refusal.
    survives = ("stack `epic` on top of", "stack `epic` on the verdict", "on top of `reviewed`")
    for stage in ("Review",):
        card = api.add_task(f"verdict-carrying card in {stage}", stage, assignee=api.me_user)
        api.tasks[card["id"]]["labels"] = [{"id": 903, "title": "reviewed"}]
        with pytest.raises(WorkflowError) as ei:
            wf.decompose(card["id"], [{"title": "A"}, {"title": "B"}])
        msg = str(ei.value)
        for phrase in survives:
            assert phrase not in msg, (
                f"the {stage} refusal explains itself with a state #673 made unreachable — it "
                f"promises the verdict rides out under `epic`, while the tool CLEARS it: {msg}"
            )
        assert "CLEAR" in msg, (
            f"the {stage} refusal no longer names the real consequence (the verdict is erased), "
            f"which is the whole point of rewording it rather than deleting the clause: {msg}"
        )

    # ...and the Done card really does read the SHARED refusal now, so the clause above was
    # dropped because it moved, not because nobody looked. Asserted here rather than left to the
    # meta-test alone: this is the test whose coverage shrank, so this is where the replacement
    # has to be visible.
    done_card = api.add_task("verdict-carrying card in Done", "Done", assignee=api.me_user)
    api.tasks[done_card["id"]]["labels"] = [{"id": 904, "title": "reviewed"}]
    with pytest.raises(WorkflowError) as done_ei:
        wf.decompose(done_card["id"], [{"title": "A"}, {"title": "B"}])
    done_msg = str(done_ei.value)
    assert "Done" in done_msg and "file_task" in done_msg, done_msg
    for phrase in survives:
        assert phrase not in done_msg, done_msg


def test_call_human_refuses_from_review_and_the_stage_check_precedes_ownership(env):
    """#590: the second half of the reviewer's dead end. `call_human` was already gated to
    Design/Build, but its refusal only said WHERE it doesn't work, and `_require_mine` ran FIRST —
    so the realistic reviewer (multi-identity: the card in Review is the implementer's) got
    "not assigned to you — claim it first" and was pointed at the one thing they must never do.

    Reordering changes no refusal SET (both checks are conjunctive) — only which message the
    "in Review AND not mine" case gets. The prefix is kept BYTE-IDENTICAL because SKILL.md quotes
    it verbatim, and the Review-only pointer is appended after it.

    Why a pointer instead of opening the gate: measured, parking from Review is LOSSY. This
    method's body `_move`s the card to Your Call, and from Your Call `review_task` refuses both
    verdicts — the verdict would die with the question.

    MUTATION-CHECKED: control PASS; drop the Review append -> FAIL; restore the old
    `_require_mine`-before-stage order -> FAIL on the multi-identity assertion."""
    api, wf, t = env
    prefix = "call_human works only from Design/Build; task is in Review"

    # solo: the card in Review is mine (I implemented it) -> stage refusal + the real channel
    mine = api.add_task("my own card, awaiting review", "Review", assignee=api.me_user)
    with pytest.raises(WorkflowError) as solo:
        wf.call_human(mine["id"], question="кто из двух вариантов правильный?")
    assert prefix in str(solo.value), \
        f"SKILL.md quotes this prefix verbatim — it must not drift: {solo.value}"
    assert "review_task" in str(solo.value), \
        f"the refusal must send the reviewer to the channel that works: {solo.value}"

    # multi-identity: the card is the IMPLEMENTER's. THIS is what pins the check order —
    # under the old order this said "claim it first" and never mentioned the stage at all.
    theirs = api.add_task("someone else's card in review", "Review")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.call_human(theirs["id"], question="кто из двух вариантов правильный?")
    assert prefix in str(multi.value), \
        f"the reviewer must read the STAGE refusal, not an ownership one: {multi.value}"
    assert "not assigned to you" not in str(multi.value), \
        f"ownership ran first again — 'claim it first' is the wrong advice here: {multi.value}"

    # CONTROL: from Design, where the tool IS the right door, it still parks the card
    parked = wf.call_human(t["id"], question="какой вариант деплоя выбрать?")
    assert parked["moved_to"] == "Your Call"
    assert api.stage_of(t["id"]) == "Your Call"


def test_decompose_creates_children_in_queue_parent_epic(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="2"):
        wf.decompose(t["id"], subtasks=[{"title": "one"}])
    res = wf.decompose(t["id"], subtasks=[
        {"title": "step 1", "description": "d1", "priority": 3},
        {"title": "step 2"},
    ])
    assert len(res["created"]) == 2
    for child in res["created"]:
        assert api.stage_of(child["id"]) == "Queue"
        assert (child["id"], t["id"], "parenttask") in api.relations
    assert api.stage_of(t["id"]) == "Backlog"
    assert api.tasks[t["id"]]["assignees"] == []
    assert any(lb["title"] == "epic" for lb in api.tasks[t["id"]]["labels"])
    assert any(c.startswith("[decompose]") for c in api.comments_text(t["id"]))


def test_decompose_clears_the_stale_verdict_off_the_card_it_turns_into_an_epic(env):
    """#673: `decompose` turns a card into an epic CONTAINER, but left the verdict label it
    arrived with hanging on it. Measured through the real `Workflow` over a FakeAPI board (not a
    live tracker), along the exact route #663's own refusal recommends — a reviewer is turned away
    from Review (that refusal is #663's pin, context here rather than a step this test runs), sends
    the card back with review_task(verdict='needs_work'), and its owner decomposes from Build,
    which are the two steps this test DOES run: the parent landed in Backlog with `epic` AND
    `review-failed` at
    once. That is the shape #626 (`reviewed` + `blocked`, in its own words the board claiming
    'approved' and 'blocked' at once) and #663 (`reviewed` + `epic`) each closed elsewhere — the
    board asserting two things at once — arriving here by the route those very cards recommend as
    the correct one. #590 is the stage-gate ANCESTOR of the shape, not another instance of it: its
    repro adds a BARE task straight into Review, so what it measured was `blocked` landing ALONE
    on a card with no label at all — `['blocked']`, NOT #626's `['reviewed', 'blocked']`, and
    collapsing those two is exactly the mistake this sentence exists to prevent. The 'approved and
    blocked' phrasing is #626's and never #590's, settled with git rather than from memory: the
    phrase APPEARS in 6ac1454 (#626) per `git log -S`, run in both case forms because that search
    is case-SENSITIVE, and `git grep` over 51ab50d (#590) finds it nowhere. Expect `git log -S` to
    name THIS commit as well — a file that quotes the phrase counts as a hit for adding the quote,
    which is the gotcha rather than a third source. The line in workflow.py crediting the phrase to
    #590 is no witness either: `git blame` puts it in 6ac1454, so #626 wrote that credit about
    itself, which is where the confusion starts rather than where it ends.

    Not merely STALE. `advance` clears both mutually-exclusive verdict labels on both of its forms
    because resuming work invalidates the old assessment (#119); on an epic the label is
    INAPPLICABLE on top of that. A card is offered for independent review in exactly two places —
    `advance`'s push nudge and `next_task`'s pull path — and LABEL_EPIC is skipped by both, so the
    normal flow can never refresh that verdict. Measured rather than assumed, in both directions:
    `advance(to='review')` DOES move an epic into Review (it only withholds `review_needed`), and
    `review_task` gates on stage alone, so a reviewer handed the id by hand can still land a
    verdict on a container. 'Nothing routes a reviewer there', not 'nothing can ever supersede it'.

    Four parts. Route A is the reported one, and its `bug` label is load-bearing rather than
    decoration: without a NON-verdict label in the picture every card here carries at most one
    label, and a decompose that stripped labels WHOLESALE would be indistinguishable from one that
    clears the two verdicts — measured, that mutant passed this whole test before the `bug`
    assertion existed. Route B carries the OTHER verdict to the same place (an APPROVED card a
    human hand-pulls back to Build — no tool fires there, so `reviewed` survives the move) and is
    what keeps the fix from being one-sided. The CHILDREN assertions run in all three routes
    because the claim is about every child: FOUR calls in decompose touch one — create_task, the
    `parenttask` relation, the move to Queue and the `ordered` `precedes` chain — and not one of
    them takes a label, so the right answer is 'nothing to clear', and this holds it that way.
    The CONTROL card never carried a verdict, and its exact `== ['epic']` pins that the clear ADDS
    nothing: a stray `blocked` alongside it reddens there and nowhere else (control 0 failed; that
    mutant -> 1 failed). It cannot pin the other direction — a card with no labels has nothing to
    take — so neither a wholesale strip nor an over-take of one non-verdict label is caught there:
    with route A's `bug` assertion deleted BOTH of those mutants run at 0 failed against a control
    of 0 failed, and that assertion is what catches both.

    MUTATION-CHECKED IN BOTH DIRECTIONS, each round naming the assertion it actually reddens as
    read out of pytest's raw output rather than guessed. Selection: test_workflow_gates.py +
    test_skill_contract.py. Every round deleted `__pycache__` first (PYTHONDONTWRITEBYTECODE stops
    Python WRITING bytecode, not READING a stale .pyc) and printed `vikunja_mcp.__file__` resolved
    in the same environment — that is #646's check that no stray `src` shadows this tree, and it
    evidences the import path only; what shows a mutant actually ran is its distinct failure.
    Unmutated control round, fix in place: 0 failed. Delete the `_clear_verdict_labels` call —
    leaving the comment, so decompose behaves exactly as it shipped before this card -> 1 failed,
    this test, at route A's `review-failed not in titles`, and it was the ONLY failure in either
    file: every other test in both, which is where the five #590/#626/#627/#649/#663 gate pins
    live, stayed GREEN on that same mutant. That is what shows this hole was SEPARATE and not a
    regression of theirs. Clear only `review-failed` -> 1 failed, now at route B (`['reviewed',
    'epic']`). Clear only `reviewed` -> 1 failed, back at route A. Strip the snapshot's labels
    WHOLESALE and add `epic` as usual -> 1 failed, at route A's `bug` assertion; this round is the
    reason that assertion exists — with the assertion removed, the same mutant passes the whole
    selection (0 failed), a control this sweep had NAMED and not RUN. The second independent pass
    found that; the number above was then re-measured here rather than inherited.
    Add a stray `blocked` label alongside the clear -> 1 failed, at the CONTROL card's `==
    ['epic']`. Restored by text substitution, never `git checkout --`, because the tree carried
    uncommitted work.

    Out of this card's slice, measured on the same run and FILED as #693 rather than fixed here:
    the same hanging verdict USED TO ride `return_task` (an approved card a human hand-pulled back
    to Build then hit an external block LANDED in Backlog as `['reviewed', 'blocked']` — #626's
    'approved and blocked' shape, per the attribution measured above, NOT #590's) and `claim` (a
    hand-parked verdict RODE into Design, where the next `advance(to='build')` cleared it). #693
    has since closed both, so that sentence is history rather than a live defect — it is kept in
    the past tense because it is what this card MEASURED and filed. The standing rule now lives in
    `_VERDICT_POLICY` below, graded per tool off the live tool surface."""
    api, wf, _t = env

    def _reviewer():
        r = type(wf)(api, project_id=3)
        r._me_cache = {"id": 77, "username": "agent-reviewer"}
        return r

    # A. the route #663's own refusal recommends: a reviewer is turned away from Review, sends the
    #    card back with review_task(verdict='needs_work'), and its owner decomposes it from Build.
    bounced = api.add_task("работа, отбитая ревью", "Queue")
    wf.claim(bounced["id"])
    wf.advance(bounced["id"], to="build", spec="сделаю X")
    wf.advance(bounced["id"], to="review", worklog="сделано", evidence="abc123")
    _reviewer().review_task(bounced["id"], verdict="needs_work", report="надо дробить")
    # a NON-verdict label riding along, and it is the load-bearing part of this cell rather than
    # decoration: without it every card here carries at most one label, so a decompose that
    # stripped labels WHOLESALE would be indistinguishable from one that clears the two verdicts
    api.add_label(bounced["id"], api.get_or_create_label("bug")["id"])
    assert api.stage_of(bounced["id"]) == "Build"
    assert "review-failed" in _label_titles(api, bounced["id"])   # the state the fix must clear

    res = wf.decompose(bounced["id"], [{"title": "часть A"}, {"title": "часть B"}])
    titles = _label_titles(api, bounced["id"])
    assert "review-failed" not in titles, (
        f"the card became a container, so the verdict it arrived with does not apply to it any "
        f"more — an epic is skipped by both places that offer a card for review: {titles}"
    )
    assert "bug" in titles, (
        f"only the VERDICT labels go: `bug` is not a verdict, it is what tells a reviewer the "
        f"rubric, and a wholesale strip would take it too: {titles}"
    )
    assert "epic" in titles and api.stage_of(bounced["id"]) == "Backlog", \
        f"clearing the verdict must not disturb what decompose actually does: {titles}"
    # the children never carried a verdict (create_task makes them bare) and the parent's clear
    # must not invent one on them
    for child in res["created"]:
        assert _label_titles(api, child["id"]) == [], \
            f"a fresh subtask carries no labels at all: {_label_titles(api, child['id'])}"

    # B. the OTHER verdict reaches the same place: an APPROVED card a human hand-pulls back to
    #    Build (no tool fires, so `reviewed` survives the move) and then decomposes.
    approved = api.add_task("одобренная работа", "Queue")
    wf.claim(approved["id"])
    wf.advance(approved["id"], to="build", spec="сделаю Y")
    wf.advance(approved["id"], to="review", worklog="сделано", evidence="def456")
    _reviewer().review_task(approved["id"], verdict="approve", report="воспроизвёл, причина ясна")
    api.task_bucket[approved["id"]] = api.bucket_id("Build")   # человек руками, мимо тулов
    assert "reviewed" in _label_titles(api, approved["id"])

    res_b = wf.decompose(approved["id"], [{"title": "часть C"}, {"title": "часть D"}])
    titles = _label_titles(api, approved["id"])
    assert "reviewed" not in titles, (
        f"an approved card that a human sent back and an agent then split is not an approved "
        f"container: {titles}"
    )
    assert "epic" in titles, f"the epic label is still the point of decompose: {titles}"
    for child in res_b["created"]:
        assert _label_titles(api, child["id"]) == []

    # CONTROL: a card that never carried a verdict, where the clear must stay an exact no-op. What
    # this cell catches, measured over the same selection as the sweep above, is a decompose that
    # ADDS something: a stray `blocked` alongside the clear reddens here and nowhere else
    # (control 0 failed; that mutant -> 1 failed, right here). It does NOT catch a wholesale strip,
    # having nothing to strip: delete route A's `bug` assertion and the strip mutant runs at
    # 0 failed against a control of 0 failed, so that assertion is the only thing catching it.
    # The `== ['epic']` asserts the epic label too, but routes A and B assert it as well, so this
    # cell is not what pins that either.
    clean = api.add_task("чистая работа", "Queue")
    wf.claim(clean["id"])
    assert _label_titles(api, clean["id"]) == []
    res_c = wf.decompose(clean["id"], [{"title": "часть E"}, {"title": "часть F"}])
    assert _label_titles(api, clean["id"]) == ["epic"], \
        f"never-reviewed card: exactly the epic label, nothing removed and nothing extra: " \
        f"{_label_titles(api, clean['id'])}"
    for child in res_c["created"]:
        assert _label_titles(api, child["id"]) == []


def test_decompose_partial_failure_reports_created_children(env):
    # A failure on the 2nd create_task (network/429) must not drop a bare VikunjaError:
    # the child created by the 1st call is already on the board, and a blind retry would
    # duplicate it. decompose must raise a WorkflowError that surfaces that partial result.
    api, wf, t = env
    real_create = api.create_task
    calls = {"n": 0}
    created_ids = []

    def flaky_create(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise VikunjaError(429, "rate limited")
        child = real_create(*args, **kwargs)
        created_ids.append(child["id"])
        return child

    api.create_task = flaky_create

    with pytest.raises(WorkflowError) as ei:
        wf.decompose(t["id"], subtasks=[
            {"title": "first child"},
            {"title": "second child"},
        ])
    msg = str(ei.value)
    first_id = created_ids[0]
    # the already-created first child is named by id AND title -> the human/agent can see
    # exactly what leaked instead of blindly retrying
    assert f"#{first_id}" in msg
    assert "first child" in msg
    assert "second child" not in msg  # the 2nd child was never created
    # the partial result really is on the board (in Queue) — not imaginary
    assert first_id in api.tasks
    assert api.stage_of(first_id) == "Queue"
    # the parent is left un-finalized: no epic label, still assigned, not moved to Backlog
    assert not any(lb["title"] == "epic" for lb in api.tasks[t["id"]]["labels"])
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert api.stage_of(t["id"]) == "Design"


def test_decompose_first_child_failure_reraises_bare_error(env):
    # nothing was created yet -> no partial result to report; the bare error is safe to
    # retry, so decompose must NOT wrap it into a misleading "already created" message.
    api, wf, t = env

    def failing_create(*args, **kwargs):
        raise VikunjaError(429, "rate limited")

    api.create_task = failing_create
    with pytest.raises(VikunjaError):
        wf.decompose(t["id"], subtasks=[{"title": "a"}, {"title": "b"}])


def test_file_task_files_finding_into_backlog_with_marker_and_relation(env):
    api, wf, t = env
    # пустой title — отказ
    with pytest.raises(WorkflowError, match="title"):
        wf.file_task(title="   ")
    # находка по ходу работы над t: паркуем в Backlog и связываем с t
    res = wf.file_task(
        title="race in claim self-heal window",
        description="заметил по ходу работы",
        priority=2,
        related_task_id=t["id"],
    )
    new_id = res["filed"]["id"]
    assert new_id != t["id"]
    assert api.stage_of(new_id) == "Backlog"          # Backlog, НЕ Queue — приоритизирует человек
    assert res["filed"]["stage"] == "Backlog"
    assert api.tasks[new_id]["priority"] == 2
    assert any(c.startswith("[filed-by-agent]") for c in api.comments_text(new_id))
    assert (new_id, t["id"], "related") in api.relations
    assert res["related_to"] == t["id"]


def test_file_task_without_relation_has_no_link(env):
    api, wf, t = env
    res = wf.file_task(title="techdebt: refactor config walk-up")
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Backlog"
    assert not any(subj == new_id for subj, _other, _kind in api.relations)
    assert "related_to" not in res
    assert any(c.startswith("[filed-by-agent]") for c in api.comments_text(new_id))


def test_file_task_cross_project_lands_in_targets_backlog(env):
    api, wf, t = env
    # Backlog у цели НЕ первый бакет: дефолт-бакет = Inbox, так что пропущенный move
    # оставил бы карточку в Inbox и тест бы упал (create-в-нужном-проекте недостаточно).
    other = api.add_project("neighbor", buckets=["Inbox", *STAGES])
    res = wf.file_task(
        title="repo B: нужен эндпоинт для A",
        description="координация агент→агент",
        priority=1,
        related_task_id=t["id"],
        project_id=other["id"],
    )
    new_id = res["filed"]["id"]
    other_view = api.kanban_view(other["id"])
    other_backlog = next(
        b for b in api.buckets(other["id"], other_view["id"]) if b["title"] == "Backlog"
    )
    assert api.task_bucket[new_id] == other_backlog["id"]  # Backlog ЦЕЛИ, не свой
    assert res["filed"]["project_id"] == other["id"]
    assert res["filed"]["stage"] == "Backlog"
    assert (new_id, t["id"], "related") in api.relations   # связь через границу проектов
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert f"from project id={wf.project_id}" in marker    # provenance для людей цели
    assert f"#{t['id']}" in marker


def test_file_task_cross_project_no_access_fails_fast_nothing_created(env):
    api, wf, _t = env
    secret = api.add_project("secret", buckets=STAGES, forbidden=True)
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="can't file into project"):
        wf.file_task(title="x", project_id=secret["id"])
    assert len(api.tasks) == before        # fail-fast: доска резолвится ДО create_task


def test_file_task_cross_project_unknown_or_pseudo_project_refused(env):
    api, wf, _t = env
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="can't file into project 999999"):
        wf.file_task(title="x", project_id=999999)
    with pytest.raises(WorkflowError, match="positive"):
        wf.file_task(title="x", project_id=-1)  # псевдо-проекты Vikunja (favorites = -1)
    assert len(api.tasks) == before


def test_file_task_cross_project_target_without_backlog_refused(env):
    api, wf, _t = env
    virgin = api.add_project("virgin", buckets=["To-Do", "Doing", "Done"])  # без setup
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="Backlog"):
        wf.file_task(title="x", project_id=virgin["id"])
    assert len(api.tasks) == before


def test_file_task_explicit_own_project_id_is_todays_behavior(env):
    api, wf, t = env
    res = wf.file_task(title="own finding", related_task_id=t["id"], project_id=wf.project_id)
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Backlog"
    assert "project_id" not in res["filed"]    # без кросс-добавок в результате
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert marker == (
        f"[filed-by-agent] filed by an agent for human triage "
        f"(found while working on #{t['id']})"
    )


def test_file_task_cross_project_401_propagates_as_vikunja_error(env):
    """Binding contract (#140): a 401 from resolving the TARGET board must stay a VikunjaError —
    NOT be wrapped into a WorkflowError — so server._tool's rotated-token reload-and-retry still
    fires. Only 403/404 (a real access/shape problem) become an actionable WorkflowError; a 401
    (invalid/expired/rotated token) must propagate untouched. VikunjaError and WorkflowError are
    unrelated types, so pytest.raises(VikunjaError) here is RED if the 401 is ever wrapped."""
    api, wf, _t = env
    other = api.add_project("neighbor", buckets=STAGES)

    def boom(_pid):
        raise VikunjaError(401, '{"code":11,"message":"invalid token"}')

    api.kanban_view = boom                      # 401 lands on the target-board resolve
    with pytest.raises(VikunjaError) as ei:
        wf.file_task(title="x", project_id=other["id"])
    assert ei.value.status == 401


def test_file_task_queue_optin_lands_in_queue_ready_for_pickup(env):
    """#249: queue=True — явный опт-ин «человек попросил завести задачу в работу» (его
    указание и есть триаж). Карточка ложится сразу в Queue СВОЕГО проекта, неассайненная
    (→ сразу клеймабельна для next_task/claimable), маркер [filed-by-agent] честно
    фиксирует пропуск Backlog-триажа. Дефолт (queue=False) пинуют существующие тесты выше."""
    api, wf, t = env
    res = wf.file_task(
        title="переезд конфига на pydantic",
        description="человек явно попросил завести в работу",
        priority=1,
        related_task_id=t["id"],
        queue=True,
    )
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Queue"             # сразу в Queue, не в Backlog
    assert res["filed"]["stage"] == "Queue"
    assert api.tasks[new_id]["assignees"] == []        # без ассайни → клеймабельна любым агентом
    assert "Queue" in res["note"]
    assert (new_id, t["id"], "related") in api.relations
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert "Queue" in marker                           # провенанс: видно, что триаж пропущен


def test_file_task_queue_cross_project_refused_nothing_created(env):
    """#249: в ЧУЖУЮ Queue агент работу не инжектит — кросс-проектный файлинг остаётся
    Backlog-only (их доску триажит ИХ человек). Отказ fail-fast: ничего не создано.
    Граница гейта — именно КРОСС, а не сам параметр: явный СВОЙ project_id с queue=True
    работает (эквивалентен None, как пинует test_file_task_explicit_own_project_id...)."""
    api, wf, _t = env
    other = api.add_project("neighbor", buckets=STAGES)
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="queue"):
        wf.file_task(title="x", project_id=other["id"], queue=True)
    assert len(api.tasks) == before                    # fail-fast: карточка не создана
    res = wf.file_task(title="own queue ok", project_id=wf.project_id, queue=True)
    assert api.stage_of(res["filed"]["id"]) == "Queue"


def test_file_task_returns_the_ref_and_its_index_is_the_servers_not_the_id(env):
    """#735: the card file_task just created comes back with `ref` — its readable
    name — so an agent told by SKILL.md to echo one is not left to invent it.

    What the exact-equality assertion buys over "ref is a non-empty string" is the ANTI-
    FABRICATION property: the readable half must be the index the SERVER assigned and not
    anything derived from the global id. That is precisely the failure this fixes — #660 shipped
    "Filed as VMCP-181 (732)" where 732 is really VMCP-195 and VMCP-181 is a LIVE unrelated card
    (id 706), i.e. the numeric half was right and the readable half pointed somewhere else. So an
    id-deriving implementation, f"HGI-{new_id}", is RED here.

    That property depends on the FIXTURE keeping index and id apart (FakeAPI counts indexes from
    1 and ids from 100), which is why the second assertion checks the fixture itself: were they
    ever to coincide, the first assertion would still pass while testing nothing about
    fabrication. It is a guard on the test, not a second pin — and a separate `ref != HGI-<id>`
    assertion is deliberately NOT written, because given these two it could never fail, and a
    redundant assert reads like independent coverage it does not provide.

    The last assertion pins cross-TOOL agreement — the name file_task gives must be the name
    get_task gives, which is what makes echoing either safe. Its honest limit: no mutation of
    file_task reaches it, because any ref this test can distinguish already fails the first
    assertion; what it would catch is the two sides DRIFTING APART later. Delete
    `"ref": self._ref(created)` from workflow.file_task and this test is RED at the first
    assertion (KeyError)."""
    api, wf, _t = env
    res = wf.file_task(title="a finding worth naming")
    new_id = res["filed"]["id"]
    index = api.tasks[new_id]["index"]

    assert res["filed"]["ref"] == f"HGI-{index} ({new_id})", \
        "not the server's index — an id-derived readable half is the #660 fabrication"
    assert index != new_id, \
        "fixture guard: index and id coincided, so the assertion above pins nothing"
    assert res["filed"]["ref"] == wf.get_task(new_id)["ref"], \
        "file_task names the card differently from get_task — echoing either becomes a guess"


def test_file_task_never_reads_back_the_card_it_just_created(env):
    """#735: the ref must keep costing NOTHING — it is formatted from the dict create_task
    already returned, and the whole safety argument for the cross-project branch rests on that.
    If a later edit "simplified" this into a read-back (`get_task(new_id)` to fetch the
    identifier), the ref value would look identical in every other test here while the tool
    acquired a new failure mode: filing into a project the token may WRITE to but not READ back
    from would start raising AFTER the card exists — the card lands, the caller gets an error and
    no ref, and the fix for the fabrication becomes a new way to lose the reference entirely.

    Measured on live 2.3.0 before relying on it: a hooked call inventory of file_task shows no GET
    of the new card in either branch, and PUT /projects/{id}/tasks carries `identifier` itself.
    This pins that property against a re-read that no value assertion can see: it is the one
    mutation that keeps every other test in this file green (verified — inserting a gratuitous
    get_task before the result dict leaves the three ref tests passing and only this one RED).

    WIDENED BY VMCP-213 (756) FROM ONE METHOD TO EVERY READ PATH, because the name promised more
    than the code asked. MUTATION-CHECKED, `__pycache__` deleted per round then
    `PYTHONDONTWRITEBYTECODE=1`, this test alone as the selection, every round restored from a
    COPY and the restore confirmed by sha256 and by returning to the control. Control round:
    0 failed.
      * insert a BOARD-SCAN re-read into `file_task` (`api.view_tasks` over the target's kanban
        view, after the provenance comment) -> 1 failed with the widened hook and **0 failed**
        with the pre-756 one, same mutation, same selection, same control. That pair is the
        card: `view_tasks` is how the rest of this package reads cards at all — `_board`,
        `next_task`, `--gc`'s liveness fetch — so the "simplification" this test exists to stop
        had a second spelling the test could not see, and it is the spelling a refactor reaches
        for first
      * the widened hook is checked by CONTENT, not by call count, because a board read
        legitimately returns many cards; the question is whether THIS one came back"""
    api, wf, _t = env
    # EVERY read that could hand the new card back, not just `get_task` — VMCP-213 (756). The pin
    # is named "never reads back" and counted exactly one method, so the re-read this test exists
    # to forbid stayed available under another name: `view_tasks` is the board scan the rest of
    # this package reads cards with (`_board`, `next_task`, `--gc`'s liveness fetch all go through
    # it), so a later "simplification" that fetched the identifier by scanning the target project
    # would have reintroduced the whole failure mode — a card that lands and then a raise, in the
    # cross-project branch where the token is least likely to be able to read — with this test
    # green. Hooking both is what makes the assert as wide as its own name; `view_tasks` is
    # checked by CONTENT rather than by call count because a board scan legitimately returns many
    # cards and the question is whether THIS one came back.
    reads = []
    real_get, real_view = api.get_task, api.view_tasks

    def counting_get(task_id):
        reads.append(task_id)
        return real_get(task_id)

    def counting_view(project_id, view_id, require_titles=None):
        buckets = real_view(project_id, view_id, require_titles)
        # a board read returns BUCKETS carrying tasks, so the ids to count are one level down —
        # collecting the buckets' own ids instead makes the hook look installed and see nothing,
        # which is how the first version of this widening measured a green round on a real
        # read-back. A hook that reads the wrong field is indistinguishable from a passing test.
        reads.extend(task["id"] for bucket in buckets for task in bucket.get("tasks", ()))
        return buckets

    api.get_task = counting_get
    api.view_tasks = counting_view

    res = wf.file_task(title="own-project finding")
    assert reads.count(res["filed"]["id"]) == 0, \
        f"file_task read back the card it just created ({reads}) — the ref must stay free"

    other = api.add_project("neighbor", buckets=STAGES, identifier="NB")
    reads.clear()
    res = wf.file_task(title="cross finding", project_id=other["id"])
    assert reads.count(res["filed"]["id"]) == 0, \
        "cross-project file_task read the new card back — that is the branch where the token " \
        "is least likely to be able to, and the card would already exist when it failed"


def test_file_task_cross_project_ref_carries_the_targets_prefix(env):
    """#735, decided deliberately rather than by inertia: filed into ANOTHER project, the ref
    carries the TARGET board's identifier prefix, because that is the name THEIR humans search
    by — the card lives there, and the filer's own tools cannot even see it. A ref built from
    the SOURCE project (or a bare id) would name a card nobody can find on either board.

    Costs nothing extra: Vikunja computes `identifier` per project and returns it in the create
    response itself (measured on real 2.3.0 — 'PRB-1'; '#1' for a prefix-less project), so no
    second call is made — which is also why "the token may not see the card it just filed"
    cannot arise here. Point _ref at anything source-derived and this is RED."""
    api, wf, t = env
    other = api.add_project("neighbor", buckets=STAGES, identifier="NB")
    res = wf.file_task(title="repo B needs an endpoint", related_task_id=t["id"],
                       project_id=other["id"])
    new_id = res["filed"]["id"]

    assert res["filed"]["ref"] == f"NB-{api.tasks[new_id]['index']} ({new_id})"
    assert not res["filed"]["ref"].startswith("HGI-"), \
        "cross-filed card named with the SOURCE project's prefix — unfindable on the target board"
    assert "TARGET project's identifier prefix" in res["note"], \
        "the note stopped warning that the prefix is theirs, not yours"


def test_file_task_ref_degrades_to_the_bare_id_when_the_server_omits_the_identifier(env):
    """#735: the fallback must stay HONEST. If a server ever answers create without
    `identifier`, the ref degrades to "#<id>" — a reference that is merely unhelpful — and must
    NEVER synthesise a plausible index, which is the one outcome worse than no ref at all (it
    resolves to a different live card). Pinning the fallback also pins that nothing downstream
    of file_task assumes an identifier is present: without it, a `KeyError`/`None` here would
    break filing entirely for prefix-less or older servers.

    Two shapes are covered because they are NOT the same on the wire, and both were measured on
    real 2.3.0: a project with NO prefix still gets an identifier — the string "#<index>", which
    _ref keeps verbatim, so the ref reads "#1 (107)" — whereas a MISSING key is the defensive
    branch and yields "#<id>". Make _ref invent an identifier from the id and the "HGI-"
    assertions are RED; drop its `or ""` guard and the missing-key case raises instead."""
    api, wf, _t = env

    # (1) prefix-less PROJECT: the server still sends an identifier, "#<index>" — kept verbatim
    plain = api.add_project("no-prefix", buckets=STAGES, identifier="")
    res = wf.file_task(title="filed into a project with no prefix", project_id=plain["id"])
    new_id = res["filed"]["id"]
    assert res["filed"]["ref"] == f"#{api.tasks[new_id]['index']} ({new_id})"
    assert res["filed"]["ref"] != f"#{new_id}", \
        "the server's index was dropped in favour of the id — that half is not ours to invent"

    # (2) server OMITS the key entirely: degrade to the bare id, never to a synthesised index
    real_create = api.create_task

    def create_without_identifier(*a, **kw):
        created = real_create(*a, **kw)
        created.pop("identifier")
        return created

    api.create_task = create_without_identifier
    res = wf.file_task(title="filed against a server that omits identifier")
    new_id = res["filed"]["id"]
    assert res["filed"]["ref"] == f"#{new_id}"
    assert "HGI-" not in res["filed"]["ref"], \
        "an identifier was synthesised where the server supplied none"


def test_comment_and_get_task(env):
    api, wf, t = env
    with pytest.raises(WorkflowError):
        wf.comment(t["id"], text=" ")
    wf.comment(t["id"], text="нашёл гочу в API")
    dossier = wf.get_task(t["id"])
    assert dossier["stage"] == "Design"
    assert dossier["comments"][-1]["text"] == "нашёл гочу в API"
    assert dossier["assignees"] == ["agent-infra"]


def test_comments_stored_as_html_and_rendered_back_multiline(env):
    """#85: a multiline agent comment is STORED as escaped, structured HTML (so the
    Vikunja UI shows line breaks) yet get_task renders it back to clean multiline text
    (so the agent doesn't read tag soup), with markers and '<' both intact."""
    api, wf, t = env
    wf.comment(t["id"], text="строка 1\nстрока 2\n\nтег <id> и a < b")
    # raw stored form is HTML with paragraph + line-break structure and escaped '<'
    raw = api.comments(t["id"])[-1]["comment"]
    assert raw.count("<p>") == 2 and "<br>" in raw
    assert "&lt;id&gt;" in raw and "&lt; b" in raw
    # but the agent-facing dossier is plain multiline text, '<id>' unescaped, no tags
    text = wf.get_task(t["id"])["comments"][-1]["text"]
    assert text == "строка 1\nстрока 2\n\nтег <id> и a < b"


def test_worklog_comment_is_html_but_markers_still_detected(env):
    """The [worklog] report is stored as HTML, yet next_task's marker greps (and the
    comments_text helper) still see the leading marker."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="починил", evidence="commit c0ffee",
               root_cause="the marker was stripped by the HTML round-trip")
    raw = next(c["comment"] for c in api.comments(t["id"])
               if "[worklog]" in html_to_text(c["comment"]))
    assert raw.startswith("<p>[worklog]")          # stored as HTML
    # an independent reviewer is still offered this bug fix -> marker detection works
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    assert reviewer.next_task().get("review") is True


def test_get_task_returns_untruncated_description_and_related(env):
    """F3: get_task — полное досье, а не урезанная _summary (500 символов, без related)."""
    api, wf, t = env
    long_description = "х" * 600
    api.tasks[t["id"]]["description"] = long_description
    parent = api.add_task("epic", "Backlog")
    api.add_relation(t["id"], parent["id"], "parenttask")

    dossier = wf.get_task(t["id"])
    assert dossier["description"] == long_description
    assert len(dossier["description"]) > 500
    assert dossier["related"] == {
        "parenttask": [{"id": parent["id"], "title": "epic"}],
    }


def test_get_task_related_defaults_to_empty_dict_without_relations(env):
    api, wf, t = env
    dossier = wf.get_task(t["id"])
    assert dossier["related"] == {}


def test_ref_composes_readable_identifier():
    """#82: agents must echo the ref "<identifier> (<id>)" — exactly the "VMCP-27 (82)"
    shape the human asked for — not the bare global id on its own. #757 measured what
    each half is FOR: the identifier is the readable name (the UI prints it as the task
    page h1), the id is what addresses the card (/tasks/82); NEITHER is a search key."""
    assert Workflow._ref({"id": 82, "identifier": "VMCP-27"}) == "VMCP-27 (82)"
    # project with no identifier prefix -> Vikunja returns "#<index>", which we keep
    assert Workflow._ref({"id": 82, "identifier": "#27"}) == "#27 (82)"
    # defensive fallback when identifier is empty/absent -> bare "#<id>"
    assert Workflow._ref({"id": 82, "identifier": ""}) == "#82"
    assert Workflow._ref({"id": 82}) == "#82"


def test_get_task_surfaces_readable_ref(env):
    """get_task dossier carries the readable ref alongside the raw id."""
    api, wf, t = env
    dossier = wf.get_task(t["id"])
    assert dossier["ref"] == f"{api.tasks[t['id']]['identifier']} ({t['id']})"
    assert dossier["ref"].startswith("HGI-") and dossier["ref"].endswith(f"({t['id']})")


def test_review_flow_for_bug_labels(env):
    api, wf, t = env
    # довели багфикс до Review
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e",
               root_cause="the state was not subscribed to event X")

    # имплементеру (assignee) ревью ПРЕДЛАГАЕТСЯ: с #991 пропуск по авторству условен на
    # require_review_independence, а он по умолчанию false — в соло иначе не отдалась бы
    # НИ ОДНА карточка. Полный разбор обоих направлений — test_review_independence.py.
    mine = wf.next_task()
    assert mine.get("review") is True and mine["task"]["id"] == t["id"]

    # свободному агенту — предлагается
    api2 = api  # тот же борд, другой "я"
    reviewer = type(wf)(api2, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]

    # пустой report / кривой verdict / не-Review задача — отказ
    import pytest as _pytest
    with _pytest.raises(WorkflowError):
        reviewer.review_task(t["id"], verdict="approve", report="  ")
    with _pytest.raises(WorkflowError):
        reviewer.review_task(t["id"], verdict="lgtm", report="r")

    # needs_work: вердикт-коммент + возврат в Build, assignee сохранён
    reviewer.review_task(t["id"], verdict="needs_work", report="фикс лечит симптом")
    assert api.stage_of(t["id"]) == "Build"
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert any(c.startswith("[review] NEEDS WORK") for c in api.comments_text(t["id"]))

    # после вердикта задача больше не предлагается на ревью (вернулась в Build);
    # доводим снова и апрувим
    wf.advance(t["id"], to="review", worklog="w2", evidence="e2",
               root_cause="the state was not subscribed to event X")
    reviewer.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    assert api.stage_of(t["id"]) == "Review"
    assert any(c.startswith("[review] APPROVE") for c in api.comments_text(t["id"]))
    # свежий APPROVE (новее последнего worklog) закрывает ревью — задача не предлагается
    res = reviewer.next_task()
    assert not res.get("review"), res


def test_review_offered_for_non_bug_task_kind_change(env):
    """#117: независимое ревью теперь на ВСЕ задачи, не только bug — не-баг в Review
    предлагается свободному агенту с review_kind='change'."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e")
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    offered = reviewer.next_task()
    assert offered.get("review") is True
    assert offered["task"]["id"] == t["id"]
    assert offered["review_kind"] == "change"


def test_review_not_offered_without_worklog_report(env):
    """#117 guard: a card in Review with no [worklog] has nothing to review — a card parked in
    Review by hand (no work report) is NOT offered for independent review (advance→review always
    posts a worklog, so real Review cards have one)."""
    api, wf, t = env
    api.add_task("parked by hand", "Review")   # no worklog, no verdict, unassigned
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    res = reviewer.next_task()
    assert "review" not in res


def test_review_not_offered_for_epic_container(env):
    """#117: epic-контейнер (нет своего кода — evidence в детях) НЕ предлагается на ревью,
    даже неназначенный и с worklog — исключение по метке epic, а не по assignee."""
    api, wf, t = env
    epic = api.add_task("epic container", "Review", labels=("epic",))
    api.add_comment(epic["id"], "[worklog] собрано")   # отчёт есть, вердикта нет
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    res = reviewer.next_task()
    assert "review" not in res      # epic отфильтрован — на ревью не выдаётся


def test_review_reoffered_after_needs_work_rework(env):
    """Цикл: needs_work -> доработка -> Review снова -> задача ОПЯТЬ предлагается на ревью."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1",
               root_cause="the state was not subscribed to event X")

    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    assert reviewer.next_task().get("review") is True

    reviewer.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert not reviewer.next_task().get("review")          # в Build — ревьюить нечего

    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2",
               root_cause="the state was not subscribed to event X")
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]  # re-offer!

    reviewer.review_task(t["id"], verdict="approve", report="теперь по причине")
    assert not reviewer.next_task().get("review")          # свежий вердикт закрыл цикл


def _label_titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]


def _to_review(wf, task_id, root_cause="the state was not subscribed to event X"):
    """#718 made `root_cause` a precondition for a card labelled `bug`, and most callers
    here label one purely to reach the bug branch of the review flow. The default keeps
    those tests about what they were about; a caller that wants the field ABSENT passes
    root_cause=None explicitly, which is what the gate's own tests do."""
    wf.advance(task_id, to="build", spec="s")
    return wf.advance(task_id, to="review", worklog="w", evidence="e",
                      root_cause=root_cause)


def test_review_approve_adds_reviewed_strips_review_failed(env):
    """approve вешает reviewed и снимает review-failed (взаимоисключающие вердикт-метки)."""
    api, wf, t = env
    _to_review(wf, t["id"])
    # на момент апрува на задаче ещё висит review-failed (belt-and-suspenders на всякий)
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "review-failed"})
    wf.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    titles = _label_titles(api, t["id"])
    assert "reviewed" in titles
    assert "review-failed" not in titles
    assert api.stage_of(t["id"]) == "Review"  # апрув оставляет задачу в Review для человека


def test_review_needs_work_adds_review_failed_strips_reviewed(env):
    """needs_work вешает review-failed и снимает reviewed."""
    api, wf, t = env
    _to_review(wf, t["id"])
    # на момент needs_work на задаче висит reviewed (например, была одобрена и переоткрыта)
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "reviewed"})
    wf.review_task(t["id"], verdict="needs_work", report="фикс лечит симптом")
    titles = _label_titles(api, t["id"])
    assert "review-failed" in titles
    assert "reviewed" not in titles
    assert api.stage_of(t["id"]) == "Build"  # needs_work возвращает задачу в Build


def test_advance_review_resubmit_strips_review_failed(env):
    """Ресабмит в Review (после needs_work) снимает review-failed — reset вердикта."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1",
               root_cause="the state was not subscribed to event X")
    wf.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert "review-failed" in _label_titles(api, t["id"])  # needs_work повесил
    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2",
               root_cause="the state was not subscribed to event X")
    assert "review-failed" not in _label_titles(api, t["id"])  # ресабмит снял


def test_advance_review_first_submit_no_review_failed_label(env):
    """Первый сабмит в Review: review-failed нет — снятие это no-op, метка НЕ добавляется."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e",
               root_cause="the state was not subscribed to event X")
    assert "review-failed" not in _label_titles(api, t["id"])
    assert api.stage_of(t["id"]) == "Review"


def test_manual_bounce_of_approved_card_clears_reviewed_on_resubmit(env):
    """#119 — сам репортнутый баг. Одобренную карточку (метка `reviewed`) человек РУКАМИ
    вытаскивает из Review на доработку — ни одна тулза не срабатывает, поэтому `reviewed`
    переживает переезд. После доработки агент ресабмитит через advance(to='review'):
    несвежий `reviewed` ДОЛЖЕН исчезнуть (ресабмит инвалидирует любой прошлый вердикт),
    иначе карточка въезжает в новый Review с чужим APPROVE. И карточка снова предлагается
    на независимое ревью (оффер цепляется за свежесть [worklog], а не за метку)."""
    api, wf, t = env
    # довели до Review и получили независимый approve -> на карточке метка reviewed
    _to_review(wf, t["id"])
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    reviewer.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    assert "reviewed" in _label_titles(api, t["id"])
    assert api.stage_of(t["id"]) == "Review"
    assert not reviewer.next_task().get("review")   # свежий APPROVE закрыл ревью

    # ЧЕЛОВЕК руками тащит одобренную карточку из Review обратно в Build на доработку.
    # update_task(bucket_id=) задачу НЕ двигает — ручной drag в FakeAPI это прямая правка
    # task_bucket. Ни одна тулза не сработала -> reviewed переживает переезд (это и есть баг).
    api.task_bucket[t["id"]] = api.bucket_id("Build")
    assert api.stage_of(t["id"]) == "Build"
    assert "reviewed" in _label_titles(api, t["id"])

    # агент дорабатывает и ресабмитит в Review -> reviewed должен уйти, review-failed тоже нет
    wf.advance(t["id"], to="review", worklog="доработал по замечанию человека", evidence="e2")
    titles = _label_titles(api, t["id"])
    assert "reviewed" not in titles           # ключевая проверка: несвежий вердикт снят
    assert "review-failed" not in titles
    assert api.stage_of(t["id"]) == "Review"
    # ресабмит снова уходит на независимое ревью (свежий [worklog] новее прошлого [review])
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]


def test_advance_to_build_clears_stale_verdict_labels(env):
    """#119: человек может утащить вердикт-несущую карточку (reviewed ИЛИ review-failed) аж
    в Design; когда агент (пере)входит в сборку через advance(to='build'), несвежий вердикт
    снимается — карточка в активной (пере)сборке не несёт действующего вердикта."""
    api, wf, t = env                               # t стартует в Design, назначена на меня
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "reviewed"})
    wf.advance(t["id"], to="build", spec="доработка после ручного возврата человеком")
    assert "reviewed" not in _label_titles(api, t["id"])
    assert api.stage_of(t["id"]) == "Build"


def test_advance_to_build_fresh_claim_adds_no_verdict_labels(env):
    """Свежий клейм: advance(to='build') на задаче без вердикт-меток — чистый no-op снятия,
    никакие метки не появляются (страхуемся, что helper не добавляет, а только снимает)."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    assert _label_titles(api, t["id"]) == []
    assert api.stage_of(t["id"]) == "Build"


def test_resubmit_after_needs_work_clears_review_failed_and_no_reviewed(env):
    """Цикл needs_work: карточка в Build с review-failed, reviewed уже снят. Ресабмит через
    advance(to='review') снимает review-failed и НЕ воскрешает reviewed — чистый лист."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1",
               root_cause="the state was not subscribed to event X")
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    reviewer.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert "review-failed" in _label_titles(api, t["id"])
    assert "reviewed" not in _label_titles(api, t["id"])
    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2",
               root_cause="the state was not subscribed to event X")
    titles = _label_titles(api, t["id"])
    assert "review-failed" not in titles   # ресабмит снял
    assert "reviewed" not in titles        # и не воскресил


def test_stale_reviewed_label_does_not_suppress_review_offering(env):
    """#119 разбор подавления: оффер ревью в next_task цепляется за СВЕЖЕСТЬ комментов
    [worklog]/[review], а НЕ за метку `reviewed`. Карточка со стале-`reviewed`, у которой
    последний [worklog] новее последнего [review] (тут [review] вообще нет), всё равно
    предлагается на ревью — значит метка это косметическая ложь на доске, а не
    функциональная блокировка следующего ревью (поэтому задача человека и попала на новое
    ревью, несмотря на несвежий бейдж)."""
    api, wf, t = env
    _to_review(wf, t["id"])                                        # свежий [worklog], вердикта нет
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "reviewed"})  # стале-бейдж вручную
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]


def test_advance_review_bug_returns_review_needed_note(env):
    """advance(to='review') на баге отдаёт review_needed=True, review_kind='bug' + подсказку."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    res = _to_review(wf, t["id"])
    assert res["review_needed"] is True
    assert res["review_kind"] == "bug"
    assert res.get("note")


def test_advance_review_non_bug_returns_review_needed_kind_change(env):
    """#117: не-баг (feat/chore/docs) теперь ТОЖЕ требует независимого ревью —
    review_needed=True с review_kind='change' (root_cause не нужен)."""
    api, wf, t = env
    res = _to_review(wf, t["id"])
    assert res["review_needed"] is True
    assert res["review_kind"] == "change"
    assert res.get("note")


def test_advance_review_epic_container_no_review_needed(env):
    """#117: epic-контейнер (нет своего кода) НЕ триггерит независимое ревью —
    review_needed/review_kind отсутствуют, payload голый (как #94)."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "epic"})
    res = _to_review(wf, t["id"])
    assert "review_needed" not in res
    assert "review_kind" not in res
    assert res == {"moved_to": "Review", "task_id": t["id"]}


def test_fake_remove_label_idempotent_and_mirrors_client(env):
    """FakeAPI.remove_label зеркалит клиент и идемпотентен (отсутствующий id — no-op)."""
    api, wf, t = env
    lb = api.get_or_create_label("reviewed")
    api.add_label(t["id"], lb["id"])
    assert "reviewed" in _label_titles(api, t["id"])
    api.remove_label(t["id"], lb["id"])
    assert "reviewed" not in _label_titles(api, t["id"])
    # повторное снятие того же id и снятие никогда не висевшего id — без ошибки
    api.remove_label(t["id"], lb["id"])
    api.remove_label(t["id"], 123456)
    assert "reviewed" not in _label_titles(api, t["id"])


# --- вложения: get_task.attachments + download_attachment (#139) ---------------------


@pytest.fixture
def att_root(tmp_path, monkeypatch):
    """Redirect downloaded-attachment temp files under pytest's tmp_path so tests don't
    litter the real system temp dir — prod deliberately leaves them for the TTL sweep."""
    root = tmp_path / "att-root"
    monkeypatch.setattr("vikunja_mcp.workflow._ATTACHMENT_ROOT", str(root))
    return root


def test_get_task_surfaces_attachment_metadata(env):
    """#139 Part 1: an agent SEES a card's files ({id,name,mime,size}) instead of guessing —
    metadata only (no bytes), read from the raw task JSON under the existing read scope."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nfake"
    att = api.add_attachment(t["id"], "shot.png", "image/png", data)
    dossier = wf.get_task(t["id"])
    assert dossier["attachments"] == [
        {"id": att["id"], "name": "shot.png", "mime": "image/png", "size": len(data)}
    ]


def test_get_task_attachments_empty_list_when_none(env):
    """No attachments -> [] (the real server sends None; the dossier normalizes it), consistent
    with related/labels/assignees always being present even when empty."""
    api, wf, t = env
    assert wf.get_task(t["id"])["attachments"] == []


def test_download_attachment_writes_temp_file_with_original_name(env, att_root):
    """#139 Part 2: returns the PATH to a temp file that keeps the ORIGINAL filename (an image
    renderer keys off the .png extension) and holds the EXACT bytes; size/mime reported too."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nrealish-bytes"
    att = api.add_attachment(t["id"], "screenshot.png", "image/png", data)
    res = wf.download_attachment(t["id"], att["id"])
    assert os.path.basename(res["path"]) == "screenshot.png"   # extension preserved
    assert res["path"].startswith(str(att_root))               # under the dedicated temp root
    assert os.path.isfile(res["path"])
    with open(res["path"], "rb") as fh:
        assert fh.read() == data                               # exact bytes on disk
    assert res["name"] == "screenshot.png"
    assert res["mime"] == "image/png"
    assert res["size"] == len(data)


def test_download_attachment_unknown_id_lists_available(env, att_root):
    """A wrong attachment id fails actionably — naming the task's real attachments — not a bare
    404 the agent can't act on."""
    api, wf, t = env
    api.add_attachment(t["id"], "a.png", "image/png", b"x")
    with pytest.raises(WorkflowError, match="no attachment"):
        wf.download_attachment(t["id"], 987654)


def test_download_attachment_when_task_has_none(env, att_root):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="no attachment"):
        wf.download_attachment(t["id"], 1)


def test_download_attachment_refuses_oversized_before_download(env, att_root):
    """A huge file is refused via its METADATA size BEFORE any bytes are pulled — actionable,
    not a memory blowup. Stored bytes stay tiny; only the reported metadata size is large."""
    api, wf, t = env
    att = api.add_attachment(
        t["id"], "huge.bin", "application/octet-stream", b"x", size=26 * 1024 * 1024
    )
    with pytest.raises(WorkflowError, match="cap"):
        wf.download_attachment(t["id"], att["id"])


def test_download_attachment_sanitizes_traversal_filename(env, att_root):
    """A crafted filename can't escape the temp dir — only the basename is used, so the file
    lands INSIDE the per-download temp subdir, never at the traversal target."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "../../../../etc/evil.png", "image/png", b"data")
    res = wf.download_attachment(t["id"], att["id"])
    assert os.path.basename(res["path"]) == "evil.png"   # only the basename survives
    assert res["path"].startswith(str(att_root))         # stays under the temp root


def test_download_attachment_sweeps_stale_temp_dirs(env, att_root):
    """The best-effort TTL sweep reaps a PREVIOUS download's dir on the next call, bounding the
    leak — without deleting a fresh file the agent is about to Read."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "a.png", "image/png", b"x")
    stale_dir = os.path.dirname(wf.download_attachment(t["id"], att["id"])["path"])
    old = time.time() - (_ATTACHMENT_TTL + 60)
    os.utime(stale_dir, (old, old))                      # backdate past the TTL
    fresh = wf.download_attachment(t["id"], att["id"])["path"]
    assert not os.path.exists(stale_dir)                 # stale reaped by the sweep
    assert os.path.exists(fresh)                         # the just-written one kept


# --- вложения: attach_file (upload, #137) --------------------------------------------


def test_attach_file_uploads_and_round_trips_into_get_task(env, tmp_path):
    """#137: attach_file uploads a LOCAL file; it lands on the card and get_task then surfaces its
    metadata (round-trip). The basename is the name, size/mime are reported, and a new
    attachment_id comes back — so an agent can cite it as evidence."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nfinished-ui"
    src = tmp_path / "shot.png"
    src.write_bytes(data)
    res = wf.attach_file(t["id"], str(src))
    assert res["attached"] is True
    assert res["name"] == "shot.png"
    assert res["size"] == len(data)
    assert res["mime"] == "image/png"                    # guessed from the extension
    assert res["attachment_id"] is not None
    dossier = wf.get_task(t["id"])
    assert dossier["attachments"] == [
        {"id": res["attachment_id"], "name": "shot.png", "mime": "image/png", "size": len(data)}
    ]


def test_attach_file_missing_path_is_actionable(env, tmp_path):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="no file to attach"):
        wf.attach_file(t["id"], str(tmp_path / "nope.png"))


def test_attach_file_directory_is_refused(env, tmp_path):
    """A directory is not a regular file -> refused by the isfile guard (never uploaded as junk),
    just like a missing path."""
    api, wf, t = env
    with pytest.raises(WorkflowError, match="no file to attach"):
        wf.attach_file(t["id"], str(tmp_path))


def test_attach_file_refuses_oversized_before_reading(env, tmp_path, monkeypatch):
    """A file over the cap is refused via getsize BEFORE its bytes are read AND before any upload
    — actionable, no huge buffer, no wasted wire call."""
    api, wf, t = env
    monkeypatch.setattr("vikunja_mcp.workflow._MAX_ATTACHMENT_BYTES", 10)
    calls = {"n": 0}
    orig = api.upload_attachment

    def spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(api, "upload_attachment", spy)
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * 50)
    with pytest.raises(WorkflowError, match="cap"):
        wf.attach_file(t["id"], str(src))
    assert calls["n"] == 0                               # refused before any upload


def test_attach_file_uses_basename_not_full_path(env, tmp_path):
    """The attachment name is the basename, never the caller's full local path (which would leak
    the local dir layout and confuse an extension-keyed renderer)."""
    api, wf, t = env
    nested = tmp_path / "deep" / "dir"
    nested.mkdir(parents=True)
    src = nested / "evidence.png"
    src.write_bytes(b"data")
    res = wf.attach_file(t["id"], str(src))
    assert res["name"] == "evidence.png"


def test_attach_file_follows_symlink_to_a_real_file(env, tmp_path):
    """A symlink pointing at a REAL file is resolved (realpath) and uploaded — a screenshot dir can
    legitimately be symlinked; only the target's basename is used for the name."""
    api, wf, t = env
    target = tmp_path / "real.png"
    target.write_bytes(b"pngbytes")
    link = tmp_path / "link.png"
    link.symlink_to(target)
    res = wf.attach_file(t["id"], str(link))
    assert res["attached"] is True and res["size"] == len(b"pngbytes")


def test_attach_file_needs_no_ownership_so_a_reviewer_can_attach(tmp_path):
    """Unlike advance/call_human, attach_file does NOT require the task be yours: a reviewer
    attaching a screenshot to SOMEONE ELSE's task in Review must work — only board membership is
    checked, symmetric with download_attachment."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    other = {"id": 999, "username": "someone-else"}
    t = api.add_task("не моя", "Review", assignee=other)      # assigned to another user
    src = tmp_path / "shot.png"
    src.write_bytes(b"png")
    res = wf.attach_file(t["id"], str(src))                   # must not raise "not assigned to you"
    assert res["attached"] is True


def test_attach_file_unknown_task_is_actionable(env, tmp_path):
    api, wf, t = env
    src = tmp_path / "shot.png"
    src.write_bytes(b"png")
    with pytest.raises(WorkflowError, match="not found"):
        wf.attach_file(987654, str(src))


# --- вложения: журнальный след аплоада в комментах (#184) -----------------------------


def test_attach_file_journals_the_upload_as_an_attach_comment(env, tmp_path):
    """#184: a successful upload leaves a TRACE in the comment journal — the human browsing the
    comments sees '[attach] shot.png (image/png, 2.0 KB)' in the stream instead of having to
    discover the file in the attachments widget. Name, mime and human-readable size are all in
    the comment; without a note there is no dangling ' - ' separator."""
    api, wf, t = env
    src = tmp_path / "shot.png"
    src.write_bytes(b"x" * 2048)
    res = wf.attach_file(t["id"], str(src))
    assert res["journal_comment"] is True
    journal = [c for c in api.comments_text(t["id"]) if c.startswith("[attach]")]
    assert journal == ["[attach] shot.png (image/png, 2.0 KB)"]


def test_attach_file_note_lands_in_the_journal_comment(env, tmp_path):
    """The agent says WHAT the file shows via note= — it rides in the SAME journal comment, so
    the human reads 'бот приложил board.png - доска после reconcile' as part of the story, not
    as two disconnected entries."""
    api, wf, t = env
    src = tmp_path / "board.png"
    src.write_bytes(b"png")
    wf.attach_file(t["id"], str(src), note="доска после reconcile")
    journal = [c for c in api.comments_text(t["id"]) if c.startswith("[attach]")]
    assert len(journal) == 1
    assert "board.png" in journal[0]
    assert journal[0].endswith("- доска после reconcile")


def test_attach_file_blank_note_is_ignored(env, tmp_path):
    """A whitespace-only note is not a note: the journal line stays clean (no trailing ' - ')."""
    api, wf, t = env
    src = tmp_path / "s.png"
    src.write_bytes(b"png")
    wf.attach_file(t["id"], str(src), note="   ")
    journal = [c for c in api.comments_text(t["id"]) if c.startswith("[attach]")]
    assert journal == ["[attach] s.png (image/png, 3 B)"]


def test_attach_file_journal_comment_failure_never_fails_the_upload(env, tmp_path, monkeypatch):
    """The journal comment is posted AFTER the upload has already landed, so its failure must NOT
    surface as a tool error: {'error': ...} reads as 'the attach failed' and provokes a blind
    retry that would DUPLICATE the attachment. Instead the result keeps attached=True, flags
    journal_comment=False, and the note says exactly what to do (don't re-upload; comment()
    manually if the trace matters)."""
    api, wf, t = env

    def boom(task_id, text):
        raise VikunjaError(500, "comments down")

    monkeypatch.setattr(api, "add_comment", boom)
    src = tmp_path / "shot.png"
    src.write_bytes(b"png")
    res = wf.attach_file(t["id"], str(src))          # must not raise
    assert res["attached"] is True
    assert res["journal_comment"] is False
    assert "re-upload" in res["note"]                # actionable: the file IS there, don't retry
    dossier = wf.get_task(t["id"])
    assert [a["name"] for a in dossier["attachments"]] == ["shot.png"]


def test_human_size_units():
    """Journal sizes are human-readable (B/KB/MB) — a human reads '1.4 MB', not 1468006. The
    units are ASCII since #1164: this string is rendered into the [attach] card comment, and
    every string the TOOL ITSELF authors onto a card is ASCII (an agent's own note is not, and
    the test two rows down still passes a Russian one through). The pin that would catch a
    regression here is tests/unit/test_card_text_is_ascii.py's runtime assert, not its source
    scan — the scan cannot follow a value across a function boundary."""
    assert _human_size(512) == "512 B"
    assert _human_size(2048) == "2.0 KB"
    assert _human_size(5 * 1024 * 1024) == "5.0 MB"


# --- вложения: hardening (#146) — sanitize имени, post-read caps, id-confusion --------


def test_safe_attachment_name_strips_nul_and_controls():
    """A server-controlled attachment name can carry a NUL/C0-control/DEL byte (which makes open()
    raise ValueError) or run past the filesystem's ~255-byte limit (open() -> OSError); the sanitizer
    neutralizes both while keeping the traversal-stripping (basename only) and the extension."""
    dirty = _safe_attachment_name("he\x00l\x01lo\x7f\n.png", "fallback.bin")
    assert not any(c in dirty for c in "\x00\x01\x7f\n")     # control bytes gone
    assert _safe_attachment_name("shot.png", "fallback.bin") == "shot.png"   # normal untouched
    assert _safe_attachment_name("../../etc/evil.png", "fallback.bin") == "evil.png"  # traversal
    assert _safe_attachment_name("\x00", "fallback.bin") == "fallback.bin"   # empty after strip
    long = _safe_attachment_name("a" * 300 + ".png", "fallback.bin")
    assert len(long.encode("utf-8")) <= _MAX_ATTACHMENT_NAME_BYTES            # within the budget
    assert long.endswith(".png")                             # extension preserved


def test_download_attachment_server_name_with_nul_does_not_crash(env, att_root):
    """A server attachment name carrying a NUL byte must NOT crash the download — open() raises
    ValueError on a NUL in a path. The byte is stripped and the EXACT bytes still land on disk."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nbody"
    att = api.add_attachment(t["id"], "he\x00llo.png", "image/png", data)
    res = wf.download_attachment(t["id"], att["id"])         # must not raise
    base = os.path.basename(res["path"])
    assert "\x00" not in base
    with open(res["path"], "rb") as fh:
        assert fh.read() == data                             # exact bytes despite the dirty name


def test_download_attachment_server_name_over_255_bytes_is_truncated(env, att_root):
    """A pathologically long server name (open() would OSError 'File name too long') is truncated
    to the byte budget while keeping the extension, so the file is actually written to disk."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "a" * 300 + ".png", "image/png", b"pngbytes")
    res = wf.download_attachment(t["id"], att["id"])         # must not OSError
    base = os.path.basename(res["path"])
    assert len(base.encode("utf-8")) <= _MAX_ATTACHMENT_NAME_BYTES
    assert base.endswith(".png")
    assert os.path.isfile(res["path"])                       # proves open() did not fail


def test_download_attachment_post_read_cap_catches_lying_metadata(env, att_root, monkeypatch):
    """Second-line defense: the METADATA size is a cheap pre-check but can under-report (or be
    missing/0). After the bytes are actually pulled, len(data) is re-checked against the cap. Here
    metadata lies (5 < cap) yet the real payload is 50 -> refused POST-read. Without the post-read
    check the oversized file would simply be written to a temp file and reported as fine."""
    api, wf, t = env
    monkeypatch.setattr("vikunja_mcp.workflow._MAX_ATTACHMENT_BYTES", 10)
    att = api.add_attachment(
        t["id"], "liar.bin", "application/octet-stream", data=b"x" * 50, size=5
    )
    with pytest.raises(WorkflowError, match="cap"):
        wf.download_attachment(t["id"], att["id"])


def test_attach_file_nul_in_path_is_actionable(env, tmp_path):
    """os.path.realpath raises ValueError on a NUL byte in the path; attach_file must surface an
    actionable WorkflowError naming the bad path, never a raw ValueError the agent can't act on."""
    api, wf, t = env
    with pytest.raises(WorkflowError):
        wf.attach_file(t["id"], "/tmp/x\x00y.png")


def test_attach_file_vanishes_between_size_and_read_is_actionable(env, tmp_path, monkeypatch):
    """A TOCTOU window: the file passes the isfile guard, getsize runs, then the file is removed
    before open() -> FileNotFoundError. attach_file must turn that (and any OSError from the
    getsize/open region) into an actionable WorkflowError, not a raw traceback."""
    api, wf, t = env
    src = tmp_path / "shot.png"
    src.write_bytes(b"png-bytes")

    def vanishing_getsize(_path):
        os.remove(str(src))          # simulate the race: file gone after the size check
        return 5

    monkeypatch.setattr("vikunja_mcp.workflow.os.path.getsize", vanishing_getsize)
    with pytest.raises(WorkflowError):
        wf.attach_file(t["id"], str(src))


def test_attach_file_post_read_cap_catches_lying_getsize(env, tmp_path, monkeypatch):
    """Mirror of the download post-read cap: getsize is a cheap pre-check but can lie (the file
    grows between stat and read). After reading, len(data) is re-checked against the cap. Here
    getsize reports 5 (passes the pre-check) but the file is really 50 -> refused POST-read, and
    nothing is uploaded."""
    api, wf, t = env
    monkeypatch.setattr("vikunja_mcp.workflow._MAX_ATTACHMENT_BYTES", 10)
    src = tmp_path / "grower.bin"
    src.write_bytes(b"x" * 50)
    monkeypatch.setattr("vikunja_mcp.workflow.os.path.getsize", lambda _p: 5)  # lie: 5 < cap
    calls = {"n": 0}
    orig = api.upload_attachment

    def spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(api, "upload_attachment", spy)
    with pytest.raises(WorkflowError, match="cap"):
        wf.attach_file(t["id"], str(src))
    assert calls["n"] == 0                                   # refused before any upload persisted


def test_get_task_attachment_id_is_attachment_id_not_file_id(env, att_root):
    """get_task must surface the ATTACHMENT id (task["attachments"][].id), NOT the inner file.id.
    On a real server the two DESYNC (the `files` table advances on any upload); download_attachment
    keys off this id, so emitting file.id would hand the agent an id the download endpoint 404s on.
    GREEN with correct code; mutating get_task to emit a["file"]["id"] makes it RED (proves it
    isn't blind — the #118/#125 lesson)."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "shot.png", "image/png", b"png", file_id=999000)
    assert att["id"] != 999000 and att["file"]["id"] == 999000       # the desync is real
    dossier = wf.get_task(t["id"])
    assert dossier["attachments"][0]["id"] == att["id"]             # the attachment id...
    assert dossier["attachments"][0]["id"] != 999000                # ...never the file id
    res = wf.download_attachment(t["id"], att["id"])                # and it's the downloadable id
    assert res["name"] == "shot.png"


def test_download_attachment_keys_off_attachment_id_not_file_id(env, att_root):
    """download_attachment keys off the ATTACHMENT id, never file.id — the two desync on a real
    server, so a file.id-keyed fetch would pull the wrong file or 404. Downloading by the
    attachment id yields the bytes; the file.id is NOT a valid attachment id -> actionable
    'no attachment'. GREEN with correct code; mutating download to match a["file"]["id"] makes it
    RED."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "shot.png", "image/png", b"png", file_id=999000)
    res = wf.download_attachment(t["id"], att["id"])               # by attachment id -> the bytes
    with open(res["path"], "rb") as fh:
        assert fh.read() == b"png"
    with pytest.raises(WorkflowError, match="no attachment"):
        wf.download_attachment(t["id"], 999000)                    # file.id is not an attachment id


# --- #705: an OWNERLESS card bounced out of Review must not become unreachable ---------------

def _ownerless_card_in_review(api, wf):
    """The card #705 is about, built the way it actually arises: work goes through the pipeline
    normally, then a HUMAN clears the assignee while it sits in Review (a plain web-UI edit).
    Measured at 3a0ee77: next_task then OFFERS it for independent review — it is not assigned to
    me and it carries a [worklog] — so the state is reached through a TOOL, not only by a human
    hand-placing a card. Deliberately not called "the blessed path": in solo the offer goes to
    the very token that just implemented the card, because the never-review-your-own-work guard
    keys off `my_id in assignees` and clearing the assignee is exactly what defeats it (measured:
    with the assignee still in place the same call answers "the queue is empty"). That hole is
    older than #705 and is not what this test is about; what matters here is only that a reviewer
    plausibly ARRIVES at this card and casts needs_work on it. Its own claim: that offer."""
    t = api.add_task("real work", "Queue")
    wf.claim(t["id"])
    wf.advance(t["id"], to="build", spec="approach")
    wf.advance(t["id"], to="review", worklog="did it", evidence="abc123")
    api.remove_assignee(t["id"], api.me_user["id"])
    offered = wf.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"], offered
    return t


def test_needs_work_bounces_an_OWNERLESS_card_to_Queue_so_it_stays_reachable():
    """#705 gate 1 — review_task(verdict='needs_work') routes on the ASSIGNEE.

    Measured at 3a0ee77, before the fix: the bounce put the card in Build still ownerless, and
    from there no agent tool could MOVE it or make it anyone's (reading and commenting stayed
    open — those need no ownership) — claim answered "task is in 'Build', you can only claim
    from Queue", call_human "not assigned to you — claim it first", advice claim provably cannot
    honour, and next_task offered nothing at all ("the queue is empty"). The reviewer's report —
    which may be a QUESTION FOR THE HUMAN, the #590/#628 escalation channel — then sat on a card
    nobody would ever come back for.

    The whole chain is asserted, not just the destination, because "lands in Queue" is only worth
    anything if Queue actually reopens the pipeline. Remove the split in review_task (always
    _move to "Build") and this goes RED on the stage assert, and again on every step after it."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = _ownerless_card_in_review(api, wf)

    res = wf.review_task(t["id"], verdict="needs_work", report="question for the human: A or B?")
    assert (res["moved_to"], api.stage_of(t["id"])) == ("Queue", "Queue"), res
    # the verdict's own effects are unchanged — the label and the report ride along, because the
    # next owner recognises WHAT this bounce was only by reading them
    assert "review-failed" in [lb["title"] for lb in api.tasks[t["id"]]["labels"]]
    assert any(c.startswith("[review] NEEDS WORK") for c in api.comments_text(t["id"]))
    assert api.tasks[t["id"]]["assignees"] == []      # the bounce never assigns anyone

    # ...and the card is reachable again by the ORDINARY pump: offered as free queue work,
    # claimable, and the escalation channel the card was filed about is open to its new owner.
    offered = wf.next_task()
    assert (offered.get("resume"), offered.get("stage")) == (False, "Queue"), offered
    assert offered["task"]["id"] == t["id"], offered
    wf.claim(t["id"])
    assert api.stage_of(t["id"]) == "Design"
    assert wf.call_human(t["id"], "A or B?")["moved_to"] == "Your Call"


def test_needs_work_still_sends_an_ASSIGNED_card_back_to_its_own_implementer():
    """#705 gate 1, the half that must NOT move. The redirect keys off "no assignee at all", so a
    card with an owner — INCLUDING one owned by somebody else — still goes back to Build for THAT
    implementer, note and all. This is the invariant the fix is easiest to break: routing every
    bounce through Queue would hand a reviewer someone else's work as claimable, and "assigned to
    another" has to keep meaning "not yours". Widen the condition to `if False:` (always Queue)
    and both halves go RED — the stage, and the refusal below."""
    other = {"id": 99, "username": "someone-else"}
    api = FakeAPI(buckets=STAGES)
    me = dict(api.me_user)
    # "shared" is the case a one-assignee reading would get wrong: the split asks "no assignee
    # AT ALL", not "not mine" and not "exactly one" — a card I co-own still has an implementer.
    for owner, assignees in (("me", [me]), ("other", [other]), ("shared", [me, other])):
        api = FakeAPI(buckets=STAGES)
        wf = Workflow(api, project_id=3)
        t = api.add_task("assigned work", "Review")
        api.tasks[t["id"]]["assignees"] = [dict(a) for a in assignees]
        res = wf.review_task(t["id"], verdict="needs_work", report="fix it")
        assert (res["moved_to"], api.stage_of(t["id"])) == ("Build", "Build"), (owner, res)
        assert res["note"] == "the task went back to the implementer — they'll see it in next_task"
        assert [a["id"] for a in api.tasks[t["id"]]["assignees"]] == [a["id"] for a in assignees]

    # and the card owned by SOMEBODY ELSE stays out of my reach: in Build, not claimable, and
    # next_task hands me nothing — "assigned to another" still means "not yours"
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    theirs = api.add_task("their work", "Review", assignee=other)
    wf.review_task(theirs["id"], verdict="needs_work", report="fix it")
    with pytest.raises(WorkflowError, match="you can only claim from Queue"):
        wf.claim(theirs["id"])
    assert not wf.next_task().get("task")


# #742: the clause a card owned by SOMEBODY ELSE gets outside Queue, since a HUMAN reversed the
# decision #705 and #734 each took to leave that refusal bare. Both tests below pin it on STRING
# EQUALITY, and the string is spelled out HERE rather than imported from `vikunja_mcp.workflow`
# deliberately: an equality pin that imports the very constant it pins passes on any rewording of
# it, which is the fake-pin shape this repo has measured itself into twice already. Keep the two
# copies in step by hand — that divergence is the whole thing being pinned. (`_BARE`, the message
# this is appended to, is spelled out the same way further down, in the #734 block.)
_OTHER_OWNER_CLAUSE = (
    " — and claim() would refuse here anyway: it works only from Queue, and this card already "
    "has an owner, so claim() refuses it from Queue too (`already taken`). Leave it to its owner "
    "and take the next task; a finding about it goes in file_task(…, related_task_id=…). Whether "
    "it ever becomes claimable is a human's call, not a promise this refusal can make."
)


def test_ownerless_card_in_an_active_stage_gets_a_refusal_it_can_act_on():
    """#705 gate 2 — the residual case, and the one thing every tool used to say about it.

    review_task no longer PRODUCES an ownerless card in Design/Build, and claim's vanish-window
    guard refuses before its own move, but a human can still hand-place one, and there
    `_require_mine`'s "claim it first" names the single call that is guaranteed to refuse
    (claim works only from Queue). Measured at 3a0ee77: advance, call_human, return_task and
    decompose all answered exactly that and nothing else, so an agent had no true statement to
    act on and no reason to stop trying the next tool. Both stages are swept below because the
    repo's own pre-existing sweep only ever measured Build; re-measured here, an ownerless card
    in DESIGN is moved by nothing either.

    Narrow on purpose, and both conditions are asserted — but only ONE of them still ends in the
    bare message, and that is #742. In QUEUE "claim it first" is correct advice for an ownerless
    card (claim from there succeeds), so that refusal is still pinned byte for byte. SOMEONE
    ELSE'S card used to be pinned bare too, on the reasoning that "not assigned to you" is
    already an accurate diagnosis; a HUMAN reversed that on VMCP-202 (742) and it now carries
    `_OTHER_OWNER_CLAUSE` outside Queue, so this test pins the NEW string byte for byte instead
    of the old one. Not a wording tidy-up: it overrides a choice #705 and #734 each made on
    purpose, bought by two measurements those cards did not have (claim refuses an owned card
    from Queue TOO, and the refusal an agent then gets outside Queue never mentions the owner).

    Delete the `if stage in ACTIVE_STAGES and not assignees` clause and the first loop goes RED;
    drop either conjunct from it and one of the byte-for-byte asserts below goes RED. Delete the
    `elif` that appends `_OTHER_OWNER_CLAUSE` and the foreign-card assert goes RED; let it fire
    in Queue as well and the sibling test's Queue assert goes RED."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    bare = "task {id} is not assigned to you — claim it first"

    for stage in ("Design", "Build"):
        orphan = api.add_task(f"hand-placed in {stage}", stage)
        for label, call in (
            ("advance", lambda tid: wf.advance(tid, to="review", worklog="w", evidence="e")),
            ("call_human", lambda tid: wf.call_human(tid, "q")),
            ("return_task", lambda tid: wf.return_task(tid, reason="r")),
            ("decompose", lambda tid: wf.decompose(tid, [{"title": "a"}, {"title": "b"}])),
        ):
            with pytest.raises(WorkflowError) as exc:
                call(orphan["id"])
            msg = str(exc.value)
            assert "UNFOLLOWABLE" in msg and "claim() works only from Queue" in msg, \
                f"{label} from {stage} still sends the agent to a call that cannot work: {msg}"
            assert "Only a human can move it back" in msg, f"{label}/{stage}: no real exit: {msg}"

    # unassigned in QUEUE: "claim it first" is exactly right, so the message must not grow
    queued = api.add_task("free work", "Queue")
    with pytest.raises(WorkflowError) as in_queue:
        wf.call_human(queued["id"], "q")
    # (call_human's own stage gate fires first here — that refusal is about the stage, and the
    # point is only that no #705 clause reaches a Queue card; advance is the ownership path)
    assert "UNFOLLOWABLE" not in str(in_queue.value), in_queue.value
    with pytest.raises(WorkflowError) as queue_own:
        wf.advance(queued["id"], to="build", spec="s")
    assert str(queue_own.value) == bare.format(id=queued["id"]), queue_own.value

    # somebody ELSE's card in Build: the diagnosis is still accurate, and since #742 it no longer
    # stops there — the human's reversal appends what to DO about it. Byte for byte, because the
    # pin it replaces was byte for byte and the point of that is unchanged: a reworded refusal
    # must be a decision somebody took, not a drift nobody noticed.
    theirs = api.add_task("their work", "Build", assignee={"id": 99, "username": "someone-else"})
    with pytest.raises(WorkflowError) as other:
        wf.call_human(theirs["id"], "q")
    assert str(other.value) == bare.format(id=theirs["id"]) + _OTHER_OWNER_CLAUSE, other.value


def test_needs_work_routes_on_a_FRESH_read_not_the_board_snapshot():
    """#705, found by this card's own second pass: the ownerless/assigned decision must not be
    made from the board snapshot `_find_task` took at the top of review_task.

    Measured sequence of that method — view_tasks, add_comment, get_or_create_label, add_label,
    buckets, move_task — so the snapshot is up to four API calls stale by the time the card
    moves, and a human clearing the assignee in the web UI inside that window put the card in
    Build ownerless: #705 reproduced BY the method that exists to prevent it. claim closes the
    same window with two get_task re-reads before its own move (the vanish-window guard); this
    closes it with one.

    Both directions are asserted, because a re-read that only looked for disappearing assignees
    would be half a fix: an assignee APPEARING mid-call means there IS an implementer now, and
    the card must go to Build. Revert the routing expression to `self._assignee_ids(task)` and
    both halves go RED."""
    class _RaceAPI(FakeAPI):
        """Mirrors the server: the mid-call edit REBINDS the assignee list, so the snapshot
        handed out earlier by view_tasks stays stale exactly as a frozen JSON body would."""
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.race_to = None      # what the human sets assignees to, mid-call
            self.race_on = None

        def add_comment(self, task_id, text):
            out = super().add_comment(task_id, text)
            if self.race_on == task_id:
                self.race_on = None
                self.tasks[task_id]["assignees"] = self.race_to
            return out

    # a human CLEARS the assignee mid-call -> no implementer any more -> Queue
    api = _RaceAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("work", "Review", assignee=api.me_user)
    api.race_on, api.race_to = t["id"], []
    res = wf.review_task(t["id"], verdict="needs_work", report="q")
    assert (res["moved_to"], api.stage_of(t["id"])) == ("Queue", "Queue"), res
    assert wf.next_task()["task"]["id"] == t["id"]          # reachable, not stranded in Build
    wf.claim(t["id"])

    # a human ASSIGNS it mid-call -> there IS an implementer now -> Build, theirs
    api = _RaceAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("work", "Review")
    api.race_on, api.race_to = t["id"], [{"id": 99, "username": "someone-else"}]
    res = wf.review_task(t["id"], verdict="needs_work", report="q")
    assert (res["moved_to"], api.stage_of(t["id"])) == ("Build", "Build"), res
    assert not wf.next_task().get("task")                   # not mine, not offered to me


# --- #734: the same dead end in the OTHER stages claim refuses from -------------------------
# #705 shipped its clause for Design/Build and said in the same breath that Backlog, Your Call
# and Done were the identical dead end, left uncovered. These two tests are that half. They are
# two because they check different KINDS of thing: the first pins the TEXT an agent reads, the
# second pins the BOARD FACTS those texts assert — a message can be reworded without lying, but
# it must never outlive the behaviour it describes.

_BARE = "task {id} is not assigned to you — claim it first"
# what the shared prefix must say wherever the clause fires at all
_PREFIX_MARKS = ("UNFOLLOWABLE", "NO assignee at all", "claim() works only from Queue")


def test_ownerless_card_gets_a_TRUE_exit_in_every_stage_claim_refuses_from():
    """#734. Six stages refuse `claim`; #705 gave an actionable refusal in two of them. Measured
    at 7121dcf (the tip this work forked from, #705 already in it) on the real Workflow over
    FakeAPI, ownerless card, 7 stages x 5 ownership-gated forms: Backlog, Your Call and Done
    answered the BARE "claim it first" — advice that cannot be followed, since claim works only
    from Queue and (measured in the sibling test) no agent tool moves the card either. Review
    answered it too.

    The exit sentence is PER STAGE, and that is the whole point of the card rather than a style
    choice — one text is measurably false in at least one stage, in THREE independent ways:

      * #705's own parenthetical, "advance, call_human, return_task and decompose all refuse it
        identically", is true ONLY in Design/Build. Elsewhere call_human refuses with its own
        stage gate (and from Review/Done so do return_task and decompose), so copying it outward
        would tell an agent four tools say one thing when three say another.
      * "Only a human can move it back" is true in Backlog/Design/Build/Your Call/Done and FALSE
        in Review, the one non-Queue stage an agent moves an ownerless card out of.
      * "so no call of yours can make it yours" is true in those same five and FALSE in Review
        for the same reason — measured, review_task(needs_work) then claim(), two of the agent's
        own calls, leave the card in Design assigned to me. It began life in the SHARED prefix,
        where the Review entry contradicted its own opening clause two lines later; this card's
        own second independent pass caught that, which is why it is now a per-stage tail.

    All three are asserted below as NEGATIVE pins, because a future "let's unify the wording" is
    exactly how this regresses and it regresses silently — a wrong exit still reads like help. The
    positive side is pinned too, and that was NOT true of the first draft: the second pass deleted
    #705's parenthetical and the Review universal outright and the whole 865-test suite stayed
    green, so a phrase can easily have its absence pinned and its presence pinned nowhere.

    QUEUE is asserted byte for byte, because there "claim it first" is simply correct. SOMEBODY
    ELSE'S card used to be asserted byte for byte AS THE BARE MESSAGE, in every stage, on the
    reasoning that "not assigned to you" is the accurate diagnosis and "leave it alone" the
    unchanged right action. #742 reversed that half — the reversal is a HUMAN's, not this
    module's — so outside Queue the foreign card is now pinned byte for byte as the bare message
    PLUS `_OTHER_OWNER_CLAUSE`. The direction of the pin did not change and neither did its
    reason: whatever the refusal says, it says because somebody chose it.

    MUTATION-CHECKED — see the record in the sibling test below; the rounds are shared."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)

    # the exit each stage must NAME. Design/Build's parenthetical and Review's universal are
    # pinned POSITIVELY here because this card's second pass proved they were not: deleting
    # either left the whole 865-test suite green, while workflow.py claimed Design/Build carry
    # #705's wording "byte for byte". A phrase whose only test mention is a `not in` assert has
    # its ABSENCE pinned elsewhere and its PRESENCE pinned nowhere.
    expected = {
        "Backlog": ("Backlog is the human's triage zone",
                    "return_task parks a card here unassigned BY DESIGN",
                    # ...and does NOT promise the card becomes claimable — measured, it may not
                    "the ordinary queue's business"),
        "Design": ("advance, call_human, return_task and decompose all refuse it identically",
                   "Only a human can move it back into the pipeline"),
        "Build": ("advance, call_human, return_task and decompose all refuse it identically",
                  "Only a human can move it back into the pipeline"),
        "Review": ("you do not need to OWN a card to review it", "review_task(task_id",
                   "needs_work sends an ownerless card to Queue",
                   "subject to the ordinary Queue gates",
                   "Review is the only non-Queue stage an agent can move this card out of"),
        "Your Call": ("Only a human moves a card out of Your Call",
                      "call_human KEEPS the assignee", "next_task offers it to nobody"),
        # DONE IS NOT HERE ANY MORE (#662), and it left this map because the CODE stopped
        # having a Done row, not because the assertion got inconvenient. Human-only Done is
        # now ONE rule in `_find_task`, which refuses before `_require_mine` runs — so the
        # ownerless exit for Done was unreachable data, and a stale row in a table a reader
        # trusts is worse than none. The replacement is stronger, not weaker: the shared
        # refusal answers for an OWNED card too, which this row never covered, and the
        # meta-test over `server._DEFERRED_TOOLS` asks it of every tool. Asserted below.
    }
    for stage, must_say in expected.items():
        orphan = api.add_task(f"ownerless in {stage}", stage)
        with pytest.raises(WorkflowError) as exc:
            wf.advance(orphan["id"], to="build", spec="s")
        msg = str(exc.value)
        for mark in _PREFIX_MARKS:
            assert mark in msg, f"{stage}: the refusal no longer says the advice is dead: {msg}"
        for phrase in must_say:
            assert phrase in msg, f"{stage}: exit sentence lost its own wording ({phrase!r}): {msg}"

    # NEGATIVE PIN 1 — the "all four refuse identically" enumeration is a Design/Build fact, and
    # saying it anywhere else would be false: call_human answers about the STAGE in the other four
    for stage in ("Backlog", "Review", "Your Call", "Done"):
        orphan = api.add_task(f"ownerless in {stage}", stage)
        with pytest.raises(WorkflowError) as exc:
            wf.advance(orphan["id"], to="build", spec="s")
        assert "refuse it identically" not in str(exc.value), \
            f"{stage}: claims four tools answer alike, but call_human refuses by STAGE there"
        if stage == "Done":
            continue    # from Done nothing reaches call_human's stage gate any more (#662)
        # ...and the stage gate really is what call_human answers with, so the pin is about a
        # real divergence and not about a phrase nobody would have written
        with pytest.raises(WorkflowError) as ch:
            wf.call_human(orphan["id"], "q")
        assert "call_human works only from Design/Build" in str(ch.value), ch.value

    # NEGATIVE PIN 2 — Review must NOT inherit "only a human", the one sentence that is a LIE
    # there. The sibling test measures why: review_task(needs_work) walks it to Queue.
    in_review = api.add_task("ownerless in Review", "Review")
    with pytest.raises(WorkflowError) as rev:
        wf.advance(in_review["id"], to="build", spec="s")
    assert "Only a human can move it back" not in str(rev.value), \
        f"Review now tells the agent only a human can move a card it can move itself: {rev.value}"

    # NEGATIVE PIN 3 — nor may Review say "no call of yours can make it yours". That clause used
    # to live in the SHARED prefix, where it contradicted the Review exit standing two lines
    # below it; this card's own second pass caught that. It is a tail, not a prefix, for exactly
    # this reason, so the pin is on the SPLIT and not merely on one stage's prose. The sibling
    # test measures the counter-example: needs_work + claim leaves the card mine.
    assert "no call of yours can make it yours" not in str(rev.value), \
        f"Review contradicts itself: the clause is back in the shared prefix: {rev.value}"
    for stage in ("Backlog", "Design", "Build", "Your Call"):   # Done: see the note above
        orphan = api.add_task(f"ownerless in {stage}", stage)
        with pytest.raises(WorkflowError) as exc:
            wf.advance(orphan["id"], to="build", spec="s")
        assert "no call of yours can make it yours" in str(exc.value), \
            f"{stage} lost the clause that IS true there: {exc.value}"

    # UNCHANGED 1 — Queue keeps the bare message, byte for byte: there the advice is correct
    queued = api.add_task("free work", "Queue")
    with pytest.raises(WorkflowError) as queue_own:
        wf.advance(queued["id"], to="build", spec="s")
    assert str(queue_own.value) == _BARE.format(id=queued["id"]), queue_own.value

    # CHANGED BY #742 — somebody ELSE'S card, still byte for byte, in EVERY stage, but no longer
    # bare outside Queue. This block used to assert `== _BARE` everywhere, on the deliberate #705/
    # #734 choice that an accurate diagnosis needs no exit; a HUMAN reversed that on VMCP-202
    # (742) and it is rewritten here EXPLICITLY rather than loosened to a substring, so the next
    # reader can see a decision was overridden and by whom.
    #
    # Both halves stay pinned as equalities, and the SPLIT is the pin: in Queue the message must
    # not grow at all (claim's own refusal there already names the owner and the next move —
    # `already taken (…) — grab the next one via next_task`), outside Queue it must grow by
    # exactly this clause and nothing else. Together they catch the two mutations that matter in
    # opposite directions: drop the clause, and every non-Queue row goes RED; fire it everywhere,
    # and the Queue row goes RED.
    #
    # DONE IS STILL THE ONE STAGE THAT NEVER GETS HERE, since #662, and it is the RIGHT
    # direction: there the card is refused by STAGE before ownership is consulted at all, so a
    # Done card belonging to someone else reads "this is the human's transition" instead of
    # "claim it first" — which for an accepted card was never an answer that could be acted on.
    # That is also why the foreign-card clause is reachable in SIX stages, not seven, even though
    # `claim` refuses such a card from all seven. Both halves are asserted, because "not the bare
    # message" alone would pass on any wording at all.
    for stage in STAGES:
        theirs = api.add_task(f"their work in {stage}", stage,
                              assignee={"id": 99, "username": "someone-else"})
        with pytest.raises(WorkflowError) as other:
            wf.advance(theirs["id"], to="build", spec="s")
        if stage == "Done":
            msg = str(other.value)
            assert "Done" in msg and "file_task" in msg, msg
            assert "not assigned to you" not in msg, msg
            continue
        want = _BARE.format(id=theirs["id"])
        if stage != "Queue":
            want += _OTHER_OWNER_CLAUSE
        assert str(other.value) == want, (stage, other.value)


def test_the_per_stage_ownerless_exits_state_only_what_the_board_really_does():
    """#734, the other half: every measurable claim those refusals make, re-measured against the
    real Workflow. A refusal that outlives its behaviour is worse than a bare one — it reads like
    help and sends the agent somewhere that no longer works.

    Four claims, one per stage that got a new text:
      * Backlog "return_task parks a card here unassigned BY DESIGN ... a human triages it into
        Queue and only THEN does claim work" — driven through the REAL return_task, not asserted
        about it. This is why Backlog is the REACHABLE half of this card: an agent produces the
        state itself, daily, where Design/Build needs a human's hand.
      * Your Call "call_human KEEPS the assignee, so a parked card is not supposed to be
        ownerless" — driven through the real call_human; and "next_task offers it to nobody"
        once the human moves it back to Design/Build.
      * Review "needs_work sends an ownerless card to Queue, where claim does work" and "the only
        non-Queue stage an agent can move this card out of" — the second is a universal, so it is
        measured as one: all 8 card-moving calls, from all 6 non-Queue stages.
      * Done, and Backlog and Your Call with it: nothing an agent calls moves the card, which is
        what makes "only a human" true in those three.

    MUTATION-CHECKED. Method, because the numbers mean nothing without it: `__pycache__` deleted
    AND PYTHONDONTWRITEBYTECODE=1 in every round (the flag alone does not stop a stale .pyc being
    READ), `vikunja_mcp.__file__` printed every round, source restored from a pristine copy and
    verified by sha256 with `git status` clean after each, no `set -e` around pytest (it exits 1
    on red and would abort before the restore). Selection = these two tests plus #705's two:
    `collected 89 items / 85 deselected / 4 selected` in EVERY round, cross-checked against the
    count of `^FAILED` lines. CONTROL ROUND FIRST and repeated between batches: `control 0 failed`
    every time. The whole set below was RE-RUN against the code as it ships, after the second pass
    forced four wording changes — a record measured against a draft describes the draft, not the
    module. Every row below is a DELTA against that `control 0 failed`, and each
    names the assertion actually read out of `--tb=line` rather than the one it seemed obvious it
    would hit (no blank line before the rows: the control has to sit in the same paragraph as the
    numbers it baselines — the contract test's unit is the paragraph, not the record):
      * drop the "Backlog" entry from `_OWNERLESS_EXITS` -> 1 failed, in the SIBLING test's PREFIX
        assert ("the refusal no longer says the advice is dead") — not its wording assert, which
        is never reached. Same for "Your Call", "Done" and "Review": 1 failed each.
      * give every stage `_ACTIVE_OWNERLESS_EXIT` — the "let's unify the wording" mutant this
        card exists to prevent -> 1 failed, and it lands in a POSITIVE assert (Backlog's own
        wording), NOT in the negative pins: the positive loop runs first, so on this mutant the
        negative pins are never reached at all. Crediting them here would be false.
      * copy Done's text into the Backlog entry (a plausible copy-paste rather than a deletion)
        -> 1 failed, that same positive assert.
      * so the two NEGATIVE pins were attacked at exactly their own width, since nothing above
        exercises them: append the "refuse it identically" enumeration to Backlog's text, keeping
        every phrase the positive asserts want -> 1 failed, and ONLY in negative pin 1. Append
        "Only a human can move it back into the pipeline" to Review's text, likewise -> 1 failed,
        ONLY in negative pin 2. Both pins hold on their own.
      * put "so no call of yours can make it yours" back into the SHARED prefix, where the first
        draft had it -> 1 failed, ONLY in negative pin 3. That draft shipped a message whose
        Review entry contradicted its own opening clause; the round exists so the SPLIT is what
        is pinned, not one stage's prose.
      * make Backlog's text promise the card is claimable once triaged -> 1 failed, its positive
        assert. The first draft said "only THEN does claim work" as a flat promise.
      * add "Queue" to the map -> 2 failed: this card's byte-for-byte Queue assert AND #705's.
      * drop the `not assignees` conjunct -> 2 failed: this card's byte-for-byte foreign-card
        assert (it names the stage, "Backlog") AND #705's.
      * make review_task(needs_work) leave an ownerless card in Review -> 2 failed: this test's
        Review claim (`assert 'Review' == 'Queue'`) AND #705's own bounce test — the round that
        shows the Review text is pinned to BEHAVIOUR and not only to itself.
      * make call_human clear the assignee -> 1 failed: this test's Your Call anomaly claim.
      * TWO rounds that were 0 failed until this card's second independent pass ran them, and are
        1 failed now — recorded as the fake pins they were, since a round that changed verdict is
        the only evidence the fix was real. Strip #705's "(advance, call_human, return_task and
        decompose all refuse it identically…)" from `_ACTIVE_OWNERLESS_EXIT` -> was 0 failed on
        BOTH this selection and the full suite, while workflow.py claimed Design/Build carry
        #705's wording byte for byte; its only test mention was a `not in` assert, so its ABSENCE
        elsewhere was pinned and its PRESENCE nowhere. Now 1 failed, the Design positive assert.
        Delete the "Review is the only non-Queue stage…" sentence -> likewise 0 failed before
        (the BEHAVIOUR was pinned by the mover sweep, the SENTENCE by nothing), 1 failed now.
      * one NO-OP round, recorded because it is NOT a fake pin: `_OWNERLESS_EXITS.get(stage or "")`
        -> `.get(stage)` -> 0 failed. `stage` is `str | None` and a dict lookup of None behaves
        exactly like "" on a str-keyed map, so the `or ""` is defensive cosmetics with no
        behaviour behind it — there is nothing there for a test to hold.

    MUTATION-CHECKED AGAIN FOR #742, with its own control, because that card rewrote the
    foreign-card rows above and an inherited record is not evidence about new code. Method: a
    `git clone --no-hardlinks` of the work tree with its own `uv sync` (two writers in one
    directory is the hazard the rulebook's second-pass section measures), `__pycache__` deleted
    AND PYTHONDONTWRITEBYTECODE=1 in every round, `vikunja_mcp.__file__` printed every round and
    pointing inside the clone, source restored from a pristine copy and verified by sha256 with
    `git status` clean after each. ONE selection, `-k "ownerless or FRESH_read"` over this file,
    reporting `collected 102 items / 97 deselected / 5 selected` in EVERY round including both
    controls. Rounds are read by COUNTING lines that begin `FAILED `, with lines beginning
    `ERROR ` counted SEPARATELY (0 in every round) — never off the first `N failed` in stdout,
    which in this file is a docstring being quoted back by a traceback. Control ran FIRST and
    again LAST: `control 0 failed` both times, and every row is a delta against it:
      * delete the `elif` that appends `_OTHER_OWNER_EXIT` -> 2 failed: this test's foreign-card
        equality row and #705's foreign-card assert. The clause's PRESENCE is pinned in both.
      * let the clause fire in Queue as well -> 1 failed, and only here: the Queue row of the
        foreign-card loop below. #705's Queue assert uses an OWNERLESS card, so it cannot see
        this mutant at all — which is why the SPLIT had to be pinned in this test and not there.
      * drop the clause's closing sentence, the one #734's refusal-to-promise argument put there
        ("it ever becomes claimable is a human's call") -> 2 failed, both equality rows. So the
        WORDING is pinned, not merely the presence of some clause.
      * two INHERITED rounds re-run, because #742 could have changed their verdict and a record
        that goes stale in silence is worse than none: add "Queue" to `_OWNERLESS_EXITS` -> 2
        failed, and drop the `not assignees` conjunct -> 2 failed. Both still 2, as recorded
        above — but the second now goes red for a DIFFERENT reason: an owned card takes the
        ownerless exit and therefore MISSES `_OTHER_OWNER_EXIT`, where before it merely grew a
        clause it was supposed not to have."""
    # --- Backlog: the agent's own tool produces this state, and the exit really is Queue ---
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    mine = api.add_task("my work", "Design", assignee=api.me_user)
    wf.return_task(mine["id"], reason="upstream is down")
    assert api.stage_of(mine["id"]) == "Backlog"
    assert not api.tasks[mine["id"]]["assignees"], "return_task no longer unassigns"
    with pytest.raises(WorkflowError, match="you can only claim from Queue"):
        wf.claim(mine["id"])                      # ...so the bare advice really is dead there
    # and the text stops at "a human triages it into Queue" rather than promising the card is
    # claimable from there — because measured, the SAME everyday card is not offered when it
    # gets there: return_task also leaves the `blocked` label, which next_task filters out.
    assert [lb["title"] for lb in api.tasks[mine["id"]]["labels"]] == ["blocked"]
    api.move_task(3, api.view["id"], api.bucket_id("Queue"), mine["id"])   # the human's triage
    assert not wf.next_task().get("task"), "a `blocked` Queue card is not supposed to be offered"
    assert wf.claim(mine["id"])["claimed"] is True       # claim BY ID still works — offer != gate
    assert api.stage_of(mine["id"]) == "Design"

    # the Backlog text names three producers of this everyday state, not one; all three measured
    for label, call in (
        ("return_task", lambda w, tid: w.return_task(tid, reason="r")),
        ("decompose", lambda w, tid: w.decompose(tid, [{"title": "a"}, {"title": "b"}])),
    ):
        api = FakeAPI(buckets=STAGES)
        wf = Workflow(api, project_id=3)
        card = api.add_task("mine", "Build", assignee=api.me_user)
        call(wf, card["id"])
        assert (api.stage_of(card["id"]), api.tasks[card["id"]]["assignees"]) == ("Backlog", []), \
            f"{label} no longer parks an ownerless card in Backlog"
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    filed = wf.file_task("a finding")["filed"]["id"]
    assert (api.stage_of(filed), api.tasks[filed]["assignees"]) == ("Backlog", [])

    # --- Your Call: call_human KEEPS the assignee, so ownerless there is an anomaly ---
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    parked = api.add_task("parked work", "Design", assignee=api.me_user)
    wf.call_human(parked["id"], "A or B?")
    assert api.stage_of(parked["id"]) == "Your Call"
    assert [a["id"] for a in api.tasks[parked["id"]]["assignees"]] == [api.me_user["id"]], \
        "call_human no longer keeps the assignee — the Your Call text calls that the anomaly"
    # and the consequence the text warns about: moved back ownerless, it is offered to nobody
    orphan_yc = api.add_task("ownerless parked", "Your Call")
    for back_to in ("Design", "Build"):
        api.move_task(3, api.view["id"], api.bucket_id(back_to), orphan_yc["id"])
        assert not wf.next_task().get("task"), f"an ownerless card in {back_to} was offered"

    # --- Review: needs_work + claim really do make an OWNERLESS card MINE, in two calls ---
    # This is the counter-example to "no call of yours can make it yours", which is why that
    # clause is a per-stage tail and not part of the shared prefix.
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    in_review = api.add_task("ownerless under review", "Review")
    assert wf.review_task(in_review["id"], verdict="needs_work", report="r")["moved_to"] == "Queue"
    assert api.stage_of(in_review["id"]) == "Queue"
    assert wf.claim(in_review["id"])["claimed"] is True
    assert api.stage_of(in_review["id"]) == "Design"
    assert [a["id"] for a in api.tasks[in_review["id"]]["assignees"]] == [api.me_user["id"]]

    # ...and the QUALIFICATION the Review text carries is real, not defensive padding. The first
    # draft of this card said "to Queue, where claim does work" full stop; the second pass broke
    # it in three reachable ways, all re-measured here. #705's own comment already recorded the
    # non-universality ("'Reopens the ordinary path' is measured … and it is not universal"), so
    # the unqualified promise reproduced this card's own defect class one stage over.
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    epic = api.add_task("ownerless epic", "Review", labels=("epic",))
    wf.review_task(epic["id"], verdict="needs_work", report="r")
    with pytest.raises(WorkflowError, match="epic CONTAINER"):
        wf.claim(epic["id"])                     # (1) an epic container: never claimable

    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    head = api.add_task("head", "Build", assignee=api.me_user)
    succ = api.add_task("ownerless successor", "Review")
    api.add_relation(succ["id"], head["id"], "follows")
    wf.review_task(succ["id"], verdict="needs_work", report="r")
    with pytest.raises(WorkflowError, match="unfinished predecessor"):
        wf.claim(succ["id"])                     # (2) the sequence gate

    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    for i in range(3):                           # (3) the DEFAULT wip limit, so this is routine
        api.add_task(f"mine {i}", "Build", assignee=api.me_user)
    saturated = api.add_task("ownerless under review", "Review")
    wf.review_task(saturated["id"], verdict="needs_work", report="r")
    with pytest.raises(WorkflowError, match="WIP limit reached"):
        wf.claim(saturated["id"])

    # --- the universal: Review is the ONLY non-Queue stage an agent moves it out of ---
    movers = (
        ("review_task(needs_work)",
         lambda w, tid: w.review_task(tid, verdict="needs_work", report="r")),
        ("review_task(approve)", lambda w, tid: w.review_task(tid, verdict="approve", report="r")),
        ("claim", lambda w, tid: w.claim(tid)),
        ("advance(build)", lambda w, tid: w.advance(tid, to="build", spec="s")),
        ("advance(review)", lambda w, tid: w.advance(tid, to="review", worklog="w", evidence="e")),
        ("call_human", lambda w, tid: w.call_human(tid, "q")),
        ("return_task", lambda w, tid: w.return_task(tid, reason="r")),
        ("decompose", lambda w, tid: w.decompose(tid, [{"title": "a"}, {"title": "b"}])),
    )
    moved_from = {}
    for stage in [s for s in STAGES if s != "Queue"]:
        for label, call in movers:
            api = FakeAPI(buckets=STAGES)
            wf = Workflow(api, project_id=3)
            card = api.add_task(f"ownerless in {stage}", stage)
            try:
                call(wf, card["id"])
            except (WorkflowError, VikunjaError):
                pass
            if api.stage_of(card["id"]) != stage:
                moved_from.setdefault(stage, []).append(f"{label}->{api.stage_of(card['id'])}")
    assert moved_from == {"Review": ["review_task(needs_work)->Queue"]}, \
        ("exactly one agent call moves an ownerless card out of a non-Queue stage, and the "
         f"per-stage exits are written around that: {moved_from}")


# --- #693: who clears a stale verdict, expressed as a GRID rather than as four call sites ------

# Every mutating Workflow tool, graded by what it must do to a verdict label the card already
# carries. The grid is the deliverable of #693, not the two calls it added: its dossier asked for
# "one rule for who clears verdict labels", and the reason the previous answer rotted is that the
# rule lived as an ENUMERATION of call sites (`advance` x2, `decompose`) that nothing held. The
# test below drives this table off the LIVE tool surface, so a thirteenth mutating tool fails it
# until someone grades it here on purpose. That is the same shape workspace_cmd's
# `_keep_is_expected` grid uses, and for the same reason: the failure mode is not a wrong entry,
# it is a MISSING one, and only a registry-driven sweep can see a missing entry.
#
# CLEARS   — the card (re-)enters the active pipeline, or stops being work at all. A prior
#            verdict has stopped describing it.
# KEEPS    — deliberate. `call_human` parks a card that is STILL the agent's own work in flight
#            behind the same assignee and comes back to Build; `review-failed` + Your Call is not
#            a contradiction, and #693's dossier measured that and graded it not-a-defect.
# SETS     — `review_task` is where a verdict comes FROM; clearing there would be circular.
# NO-MOVE  — does not move the card between stages, so it cannot stale a verdict by moving one.
_VERDICT_POLICY = {
    "claim":               "CLEARS",   # Queue -> Design            (#693)
    "advance":             "CLEARS",   # -> Build and -> Review     (#119)
    "decompose":           "CLEARS",   # -> Backlog as an epic      (#673)
    "return_task":         "CLEARS",   # -> Backlog, unassigned     (#693)
    "handoff":             "CLEARS",   # -> Queue, blocked on a neighbour's card (#1179)
    "transfer_task":       "CLEARS",   # -> another project's Backlog           (#1179)
    "call_human":          "KEEPS",    # -> Your Call, still in flight
    "review_task":         "SETS",
    "next_task":           "NO-MOVE",
    "get_task":            "NO-MOVE",
    "comment":             "NO-MOVE",
    "file_task":           "NO-MOVE",  # creates a NEW card; never touches this one's labels
    "attach_file":         "NO-MOVE",
    "download_attachment": "NO-MOVE",
}


def test_every_agent_tool_is_graded_for_what_it_does_to_a_stale_verdict(env):
    """The grid above covers the LIVE tool surface, and each CLEARS row actually clears.

    WHY A GRID AND NOT FOUR TESTS. #693's own dossier names the disease in its option 3: the rule
    "who clears verdict labels" was spread across `_clear_verdict_labels` call sites and "the
    enumeration of tools is held by nothing". Four more assertions would have re-created exactly
    that. What a new mutating tool trips is the COVERAGE half below — it is graded here or the
    suite goes red — and that half is the only part a hand-written list cannot have.

    MEASURED before the fix, real `Workflow` over `FakeAPI`, both routes from #693's dossier:
    approve -> a human hand-drags the approved card back to Build -> `return_task` left Backlog
    holding `['blocked', 'reviewed']` at once; approve -> a human hand-places the card in Queue
    with the assignee cleared -> `claim` left Design holding `reviewed`. Both are red-first for
    the rows below: with either call removed the matching row fails.

    EVERY CLEARS ROW IS DRIVEN HERE, and that is #693's REWORK rather than its first shape. The
    first version opened on the sentence above while driving only the two rows #693 itself added
    (`return_task`, `claim`): removing BOTH of `advance`'s calls, or `decompose`'s one, left THIS
    test GREEN — measured, 1 passed both times — and only the neighbouring #119/#673 tests
    reddened, 4 of them for `advance` and 1 for `decompose`. Those two rows were graded but
    UNEXERCISED, which is the "guard oversold" this repo refuses: a reader who deletes a call
    BECAUSE the grid claims to hold the rule has to see the grid go red. `advance` needs BOTH of
    its forms driven rather than one, and that is MEASURED rather than reasoned: with only the
    to='build' half of route 3 present, dropping the to='review' call leaves THIS test green
    (1 passed) — the same oversell in miniature. Both halves are driven, so both removals redden.

    Driving them here does NOT retire the #119/#673 tests and is not meant to. The counts below
    (5 catchers for `advance`, 2 for `decompose`, against 1 each if this test stood alone) are
    OVERLAP, and overlap alone is not a measure of what those tests add. What they add is ROUTES:
    they reach the same call sites through the needs_work cycle, the manual-bounce route and the
    first-submit no-op, none of which this test walks. THREE of the five assert strictly more
    than a label — `test_manual_bounce_of_approved_card_clears_reviewed_on_resubmit` checks the
    card stops being OFFERED for review, and `test_advance_to_build_clears_stale_verdict_labels`
    and the decompose test assert the STAGE — but the other TWO assert labels ONLY, so "they
    assert more" would be the wrong reason to keep them. The routes are the reason. That count
    said "two" until #693's third round re-derived it from the tests' own asserts: three and two
    PARTITION the five, and the "two" it replaces did not. Under the narrower reading "more than
    the board STATE" it is ONE (only the review-offering check), so the number moves with the
    reading — which is why the reading is written beside it.

    The KEEPS row is not a weaker CLEARS. It is asserted in the POSITIVE direction — the label
    must SURVIVE `call_human` — so a future edit that "tidies up" by clearing everywhere reddens
    this test rather than passing it. A grid whose exceptions are only ever unasserted is a list
    with extra steps.

    NOT claimed here, and the bound is deliberately narrow, because this is the sentence a reader
    consults before deleting something. Every CLEARS row is driven, but one route per CALL SITE,
    not per stage: five routes over four tools, since `advance` holds two calls and needs both.
    It is NOT claimed that a CLEARS tool is REACHABLE from every stage carrying a verdict — the
    stage gates that decide that are pinned by their own tests above. Nor is every ROW of the grid
    driven: `review_task` (SETS) and the SIX NO-MOVE rows are graded and never called here,
    because what holds them is the COVERAGE sweep, not a route. Six, not the seven this sentence
    claimed until #693's third round: the grid is 12 rows = 4 CLEARS + 1 KEEPS + 1 SETS + 6
    NO-MOVE (re-derived twice, by regex over the literal and by importing the module, and 12
    agrees with the live `@_mcp_tool` surface). Seven is the count of UNDRIVEN rows — 12 minus
    the 5 driven — so naming `review_task` separately counted it twice. This test is about the
    label, never about the gate.

    MUTATION-CHECKED, selection `tests/unit/test_workflow_gates.py`, `__pycache__` deleted and then
    PYTHONDONTWRITEBYTECODE=1, every round restored from a byte copy and the final file confirmed
    sha256-identical to the pristine one. Each mutation is applied by a script that refuses to run
    unless its target matches EXACTLY ONCE — needed rather than tidy, because
    `self._clear_verdict_labels(...)` is not a unique string in this module: FIVE call sites —
    `advance` two, `claim`, `decompose` and `return_task` one each — so a naive text mutation
    silently hits the wrong one or none. The rounds below therefore address a call site by LINE
    and assert its content before touching it, and that content check is what fired on the
    rework's first run: `claim`'s call passes `fresh`, not `task`, so a target written for
    `(task)` did not match there. A multiplicity check alone would NOT have caught that — it is
    the per-site content assertion that did.
    Control round: 0 failed. Every round OF THIS SWEEP fails THIS test; the number given is the
    selection's total, larger where a #119/#673 test catches the same mutation too.
      * drop ONLY the new call in `return_task` -> 1 failed, on the `['blocked']` assert
      * drop ONLY the new call in `claim` -> 1 failed, on the Design assert
      * drop BOTH -> 1 failed (one test carries every CLEARS row and the KEEPS row, so counts do
        not add up across rounds — what changes is which row fails first, not how many tests do)
      * drop ONLY `advance`'s to='build' call -> 2 failed; ONLY its to='review' call -> 4; BOTH
        -> 5. Before the rework that two-call round was 4 failed WITHOUT this test among them
      * drop `decompose`'s call -> 2 failed. Before the rework: 1 failed, without this test
      * drop all four CLEARS tools at once -> 6 failed
      * the OTHER direction, because a grid whose exceptions never fire is a list with extra
        steps: add `_clear_verdict_labels` to `call_human` too -> 1 failed, on the KEEPS assert
    Only the CALL is removed in each round, never the comment above it: mutating prose would
    measure the docstring rather than the behaviour.
    """
    from vikunja_mcp import server

    exposed = {fn.__name__ for fn in server._DEFERRED_TOOLS}
    ungraded = exposed - set(_VERDICT_POLICY)
    assert not ungraded, (
        f"{sorted(ungraded)} are exposed as agent tools but carry no verdict-label grade. Add a "
        "row to _VERDICT_POLICY deliberately — CLEARS if the tool moves a card into or out of the "
        "active pipeline, KEEPS with a reason, SETS, or NO-MOVE. #693 exists because this "
        "enumeration used to be held by nothing"
    )
    stale = set(_VERDICT_POLICY) - exposed
    assert not stale, (
        f"{sorted(stale)} are graded here but no longer exposed as agent tools — drop the rows"
    )

    api, wf, task = env

    # CLEARS, route 1 — return_task out of an OPEN stage, the strong case from the dossier.
    api.tasks[task["id"]]["labels"].append({"id": 901, "title": "reviewed"})
    wf.return_task(task["id"], reason="upstream service is gone")
    assert api.stage_of(task["id"]) == "Backlog"
    assert _label_titles(api, task["id"]) == ["blocked"], (
        "return_task left a stale verdict beside `blocked` — the board would claim approved AND "
        "blocked at once, the pair #626's pin above measured coming out of Done. The Done refusal "
        "no longer SPELLS THAT PAIR OUT, and this very call is why: with the verdict cleared "
        "first it names the ERASED acceptance instead"
    )

    # CLEARS, route 2 — claim into Design off a hand-placed, verdict-carrying Queue card.
    other = api.add_task("hand-placed", "Queue")
    api.tasks[other["id"]]["labels"].append({"id": 902, "title": "review-failed"})
    wf.claim(other["id"])
    assert api.stage_of(other["id"]) == "Design"
    assert _label_titles(api, other["id"]) == [], "claim walked a stale verdict into Design"

    # CLEARS, route 3 — `advance`, and BOTH of its forms, because one row covers two call sites:
    # driving only to='build' leaves the removal of the to='review' call green here (measured, 1
    # passed) — the same oversell in miniature. These are #119's routes, driven here so the row is
    # not decoration in the grid that claims to hold the rule.
    back = api.add_task("hand-dragged back", "Design", assignee=api.me_user)
    api.tasks[back["id"]]["labels"].append({"id": 904, "title": "reviewed"})
    wf.advance(back["id"], to="build", spec="доделать по замечаниям")
    assert api.stage_of(back["id"]) == "Build"
    assert _label_titles(api, back["id"]) == [], (
        "advance(to='build') walked a stale verdict into Build — a human can hand-drag an "
        "APPROVED card back here, and `reviewed` must not survive the re-entry"
    )
    api.tasks[back["id"]]["labels"].append({"id": 905, "title": "review-failed"})
    wf.advance(back["id"], to="review", worklog="доделал", evidence="deadbeef")
    assert api.stage_of(back["id"]) == "Review"
    assert _label_titles(api, back["id"]) == [], (
        "advance(to='review') resubmitted with a stale verdict still standing — the resubmit is "
        "what invalidates the previous review, so the label cannot ride along into the new one"
    )

    # CLEARS, route 4 — `decompose`: the card stops being work at all and becomes a container.
    # #673's route, driven here for the same reason as route 3.
    big = api.add_task("слишком крупная задача", "Build", assignee=api.me_user)
    api.tasks[big["id"]]["labels"].append({"id": 906, "title": "review-failed"})
    wf.decompose(big["id"], [{"title": "часть A"}, {"title": "часть B"}])
    assert api.stage_of(big["id"]) == "Backlog"
    assert _label_titles(api, big["id"]) == ["epic"], (
        "decompose left a stale verdict beside `epic` — the parent is a container now, and a "
        "verdict on a container is a claim about code it no longer holds"
    )

    # CLEARS, route 5 — `handoff`: the card leaves the active pipeline to wait on another
    # project's card. Same shape as return_task above, and driven for the same reason.
    neighbour = api.add_project("neighbour", buckets=STAGES, identifier="NB")
    cross = Workflow(api, project_id=3, siblings={"neighbour": neighbour["id"]})
    waiting = api.add_task("needs the other repo", "Build", assignee=api.me_user)
    api.tasks[waiting["id"]]["labels"].append({"id": 907, "title": "review-failed"})
    cross.handoff(waiting["id"], to="neighbour", title="the other half")
    assert api.stage_of(waiting["id"]) == "Queue"
    assert _label_titles(api, waiting["id"]) == [], (
        "handoff parked a card in Queue still carrying a verdict — the board would show it "
        "waiting on a dependency AND review-failed, with nothing to say which is live"
    )

    # CLEARS, route 6 — `transfer_task`: the card lands on a board where that verdict was
    # never earned, so it must not travel with it.
    misfiled = api.add_task("wrong board", "Build", assignee=api.me_user)
    api.tasks[misfiled["id"]]["labels"].append({"id": 908, "title": "reviewed"})
    cross.transfer_task(misfiled["id"], to="neighbour", reason="belongs over there")
    assert api.stage_of(misfiled["id"]) == "Backlog"
    assert _label_titles(api, misfiled["id"]) == [], (
        "transfer_task carried a verdict onto another project's board, where nobody cast it"
    )

    # KEEPS — asserted positively, so "clear everywhere" cannot pass by accident.
    parked = api.add_task("in flight", "Build", assignee=api.me_user)
    api.tasks[parked["id"]]["labels"].append({"id": 903, "title": "review-failed"})
    wf.call_human(parked["id"], question="which option do you want?")
    assert api.stage_of(parked["id"]) == "Your Call"
    assert _label_titles(api, parked["id"]) == ["review-failed"], (
        "call_human cleared a verdict it is graded to KEEP: the card is still the agent's own "
        "work in flight and returns to Build, so `review-failed` there is not a contradiction"
    )


def test_claim_clears_a_verdict_that_appeared_AFTER_the_board_read(env):
    """#786: `claim` hands `_clear_verdict_labels` the FRESH read, not the board copy — and until
    this test that CHOICE was held by a comment and nothing else.

    WHY IT WAS UNPINNABLE, which is the whole card. `_remove_label` DELETEs only links it can see
    on the snapshot it was handed, so the argument decides what gets cleared. Measured on #693:
    swapping `fresh` -> `task` left the ENTIRE unit suite green. The cause was not a coverage gap
    anyone could close by writing another test — `FakeAPI.get_task` returned a SHALLOW copy, so
    every snapshot shared ONE `labels` list with the store and with each other. "Snapshot A is
    older than snapshot B" was not a state the fake could hold, so no test on it could tell a
    correct argument from a stale one. #786 deepened the copy (`FakeAPI._snapshot`, and the reason
    it is a fidelity fix rather than a convenience is written there); this is what the deepening
    BUYS, and without it the label assert below passes with either argument.

    THE STATE IT BUILDS is the one the comment in `claim` is about: a human labels the card in the
    window between the pump's board read and claim's own verify read. `add_assignee` sits exactly
    there — `_board()`, then the gates, then `add_assignee`, then the fresh `get_task` — so
    hooking it puts the label in that window with no reach into `Workflow` at all.

    TWO INDEPENDENT DETECTORS, and they answer different questions, which is why both are here.
    The label assert is the product one: passing the board snapshot leaves `reviewed` riding into
    Design — the #693 failure it was supposed to prevent, arriving one read later. The aliasing
    assert above it is the FAKE one: it fails the moment `FakeAPI` starts sharing its store again,
    which is the condition under which the label assert silently stops meaning anything. NOT
    claimed here: that `fresh` is safer on the REAL client for any other reason. Real
    `VikunjaAPI.get_task` reparses JSON per request, so it cannot alias; that is why the fake had
    to be corrected to match rather than the production code.

    MUTATION-CHECKED, selection `tests/unit/test_workflow_gates.py`, `__pycache__` deleted then
    PYTHONDONTWRITEBYTECODE=1, every mutation asserted to have LANDED before its round and the
    sources restored and sha256-verified after. Control round: 0 failed.
      * `claim`'s `_clear_verdict_labels(fresh)` -> `(task)` -> 1 failed, this test, on the LABEL
        assert (`['reviewed'] == []`). That single delta is the deliverable of #786.
      * the SAME swap with `_snapshot` reverted to a shallow copy -> 1 failed, and the round is
        recorded because it refuted its own prediction: the draft above expected 0 failed, and it
        is 1, dying on the ALIASING assert rather than the label one. Which is the detectors
        working as split — so the blind half had to be measured on its own.
      * that same pre-#786 world with the aliasing assert deleted -> 0 failed. THAT is the
        card's symptom reproduced by construction: the label assert alone cannot see the swap
        until the fake stops aliasing. Both halves are needed — the deepening without this test
        pins nothing, and this test without the deepening is green on the bug.
    """
    api, wf, _seed = env

    card = api.add_task("hand-placed in Queue by a human", "Queue")
    real_add_assignee = api.add_assignee

    def assign_then_a_human_labels_it(task_id, user_id):
        real_add_assignee(task_id, user_id)
        api.tasks[task_id]["labels"].append({"id": 907, "title": "reviewed"})

    api.add_assignee = assign_then_a_human_labels_it

    board_copy = next(
        t for b in api.view_tasks(api.project["id"], api.view["id"]) for t in b["tasks"]
        if t["id"] == card["id"]
    )
    wf.claim(card["id"])

    assert board_copy["labels"] == [], (
        "the board snapshot moved under the reader — `FakeAPI` is aliasing its store again, and "
        "with it the label assert below goes blind: a stale snapshot stops being representable"
    )
    assert api.stage_of(card["id"]) == "Design"
    assert _label_titles(api, card["id"]) == [], (
        "claim walked a verdict into Design that appeared AFTER its board read — it must clear "
        "off the FRESH snapshot, since `_remove_label` only DELETEs links present on the copy it "
        "is handed and the board copy is one read older"
    )


# --- #718: root_cause is gated for a bug, and only for a bug --------------------------------

def _bug_in_build(api, wf, extra_labels=()):
    t = api.add_task("job", "Design", assignee=api.me_user, labels=["bug", *extra_labels])
    wf.advance(t["id"], to="build", spec="s")
    return t


def test_a_bug_cannot_reach_review_without_a_root_cause(env):
    """#718: the field two shipped surfaces call MANDATORY is finally one.

    MEASURED before the fix, real `Workflow` over `FakeAPI`: a card labelled `bug` advanced to
    Review with no cause at all, and the SAME payload answered `review_kind: 'bug'` — so the tool
    had the information and did not use it. `advance`'s tool docstring said "for bug fixes
    root_cause is MANDATORY" and SKILL.md said ОБЯЗАТЕЛЕН; both promised a gate that did
    not exist. The cost was not cosmetic: the reviewer's 'bug' rubric is "confirm the fix
    closes the CAUSE from the report", and the report could contain no cause.

    The refusal names the FIELD and its STATE, which is #657's shape rather than a new one — an
    agent whose argument was dropped in transit and an agent who left the field blank need
    different next moves, and a single sentence for both is what #657 removed.

    MUTATION-CHECKED, selection `tests/unit/test_workflow_gates.py`, `__pycache__` deleted and
    then PYTHONDONTWRITEBYTECODE=1, each round restored from a byte copy and the file confirmed
    sha256-identical afterwards; the mutation script refuses to run unless the gate matches
    exactly once. Control round: 0 failed.
      * drop the gate (the pre-#718 body) -> 2 failed, this test and the null-vs-blank one
      * gate EVERY card, not just bugs -> 10 failed. The blast radius IS the finding: eight of
        those ten are ordinary review-flow tests that label a card `bug` only to reach the bug
        branch, which is how a gate widened past its slice announces itself
      * drop the epic exemption -> 1 failed, and it is the epic row alone — so that row is a
        real pin and not decoration
    The three rounds fail DIFFERENT tests, which is what says the rows below divide the gate's
    behaviour rather than restating one assertion three times.
    """
    api, wf, _ = env
    t = _bug_in_build(api, wf)
    with pytest.raises(WorkflowError) as exc:
        wf.advance(t["id"], to="review", worklog="fixed it", evidence="a" * 40)
    assert "root_cause" in str(exc.value)
    assert api.stage_of(t["id"]) == "Build", "the refusal must not move the card"


def test_the_root_cause_refusal_tells_null_apart_from_blank(env):
    """The two states are not one. `arrived as null` means no text reached the tool — the #657
    class, where a retry of the same call is not the fix; `empty or whitespace-only` means the
    agent left it blank and should write it. Asserted separately so a future edit cannot collapse
    them into one sentence again."""
    api, wf, _ = env
    absent = _bug_in_build(api, wf)
    with pytest.raises(WorkflowError) as e1:
        wf.advance(absent["id"], to="review", worklog="w", evidence="e")
    assert "root_cause — arrived as null" in str(e1.value)

    blank = _bug_in_build(api, wf)
    with pytest.raises(WorkflowError) as e2:
        wf.advance(blank["id"], to="review", worklog="w", evidence="e", root_cause="   ")
    assert "root_cause — passed, but empty or whitespace-only" in str(e2.value)


def test_a_bug_with_a_root_cause_advances_and_the_cause_lands_in_the_journal(env):
    api, wf, _ = env
    t = _bug_in_build(api, wf)
    wf.advance(t["id"], to="review", worklog="fixed it", evidence="a" * 40,
               root_cause="the state was never subscribed to event X")
    assert api.stage_of(t["id"]) == "Review"
    journal = [c["comment"] for c in api.comments(t["id"]) if "[worklog]" in c["comment"]]
    assert any("Root cause: the state was never subscribed" in c for c in journal)


def test_the_root_cause_gate_does_not_spread_beyond_bugs(env):
    """The gate asks the SAME question that computes `review_kind` — `_has_label(task, bug)` —
    so a change card is untouched. This is the row that fails if the condition is dropped."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="did the change", evidence="a" * 40)
    assert api.stage_of(t["id"]) == "Review"


def test_an_epic_container_is_exempt_from_the_root_cause_gate(env):
    """Exempt for the reason the push-nudge exempts it: an epic's code lives in its children and
    no reviewer is ever offered the container, so a cause demanded here would have no consumer.
    Positive assert, so removing the exemption reddens this rather than passing silently."""
    api, wf, _ = env
    t = _bug_in_build(api, wf, extra_labels=("epic",))
    wf.advance(t["id"], to="review", worklog="container", evidence="a" * 40)
    assert api.stage_of(t["id"]) == "Review"


def test_decompose_hands_back_a_ref_for_every_child(env):
    """VMCP-206 (749): the same key #735 added to `file_task`, on the sibling that lacked it.

    Not cosmetic. SKILL.md tells agents to name a card human-readably and FORBIDS composing a
    ref: the per-project index follows from nothing about the global id, so an invented one does
    not look broken — it points at an unrelated LIVE card, which is a mistake this repo has
    already shipped once. Until this change `decompose` was the one surface where the rulebook's
    own advice cost a `get_task` per child, and the value was on hand the whole time: `child` is
    the `create_task` response and already carries `identifier`.

    Compared against `_ref` OF THE CARD AS THE BOARD HOLDS IT, not against a string this test
    builds — a test that concatenates the expected ref itself would pass on a wrong prefix as
    happily as on a right one.

    MUTATION-CHECKED, selection `tests/unit/test_workflow_gates.py`, caches cleared and then
    PYTHONDONTWRITEBYTECODE=1, restored from a byte copy. Control round: 0 failed.
      * drop the `ref` key -> 1 failed
      * keep the key but COMPOSE its value as `#<id>` -> 1 failed. That round is the point of
        the third assertion: a pin that only asked "is there a ref" would call an invented one a
        pass, and an invented ref is the failure mode — it resolves to a real, unrelated card
    """
    api, wf, task = env
    res = wf.decompose(task["id"], [{"title": "one"}, {"title": "two"}])

    assert [c["id"] for c in res["created"]], "decompose returned no children"
    for child in res["created"]:
        assert set(child) == {"id", "ref", "title"}, child
        assert child["ref"] == Workflow._ref(api.get_task(child["id"])), (
            f"child {child['id']} came back with ref {child['ref']!r}, which is not what the "
            "board says its identifier is"
        )
        assert child["ref"].startswith("HGI-"), (
            f"{child['ref']!r} is not the project-prefixed form — a bare '#<id>' here would mean "
            "the identifier was lost and the fallback fired, which is the state 749 is about"
        )
