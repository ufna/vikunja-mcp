"""`add_label` on a label the card ALREADY carries — the guard, the single write path, and the
order the verdict is written in (#1216).

WHAT THESE TESTS REST ON, narrowed by the sweep that measured it rather than by what it looked
like. `FakeAPI.add_label` refuses a duplicate the way real 2.3.0 does (`400 {"code":8001,...}`),
which it did NOT do until this card: it appended a second copy and stayed green, so a whole green
unit suite — 1367 at `f7de8d7`, the commit this fix was cut from — coexisted with four reachable
duplicate-add routes in `workflow`. The obvious thing to
say next — that without the fake's 400 the rest of this module stops measuring anything — is
FALSE, and a round says so: with the mirror removed and the guard left in, only the mirror pin
goes red (control 0 failed / 0 errors / 129 collected; that round 1 failed), and with BOTH removed
5 fail, not 1. The route tests assert the label COUNT, so they see a duplicate append as readily
as an exception. What the mirror actually buys is that the fake stops being more generous than the
server on this endpoint — a 1:1 rule of this repo, and what stopped a route test that merely DROVE
one of these sequences from going green anyway. What made the four routes invisible is simpler and
worth not dressing up: nobody had written such a test. The SERVER end of the mirror is pinned
where only it can be, against a real container:
`tests/integration/test_duplicate_label.py::test_duplicate_add_label_is_a_400`.

MEASURED, on a throwaway real 2.3.0 (#1216), with `workflow.py` at the pre-fix commit — this is
what the guard prevents, not a worry about it:
  - two `review_task(..., 'approve')`: the second raised `400 code 8001`, and the second
    reviewer's `[review] APPROVE` report was ON THE CARD afterwards (2 review comments) while the
    caller saw only a failure;
  - `needs_work` on a card a human hand-dragged back to Review still wearing `review-failed`: the
    `[review] NEEDS WORK` comment landed, the card NEVER LEFT Review, and the caller saw only a
    failure. Nobody comes back for that card — `next_task` offers a Review card only while the
    last `[review]` comment is OLDER than the last `[worklog]`, and that comment had just landed.

THE SWEEP, ten rounds against ONE control and all of them against the FINAL code (the earlier
rounds were re-run after the guard changed shape, rather than quoted from before it). Selection
every time is this file + `test_workflow_epic_marker.py` + `test_workflow_gates.py`, in a fresh
clone with `__pycache__` deleted and PYTHONDONTWRITEBYTECODE=1, `vikunja_mcp.__file__` printed each
round, rounds read by COUNTING lines beginning `FAILED ` and `ERROR ` counted separately. Control
(unmutated): 0 failed, 0 errors, 130 collected — every round below reports the same 130 collected
and 0 errors, and each names what it killed.
  * delete the guard inside `_add_label` -> 5 failed: the four route tests and the variant pin
  * re-inline the bypass in `return_task` -> 2 failed: its own route, plus the one-caller pin
  * re-inline the bypass in `decompose` -> 2 failed: the same pair
  * put the verdict comment back BEFORE the labels in the approve branch -> 2 failed: both order
    tests
  * key the guard on the exact TITLE instead of the resolved label ID (the guard's own first
    draft) -> 1 failed: the variant pin
  * give `FakeAPI.get_or_create_label` its pre-#1216 exact-match resolution back -> 1 failed: the
    same pin. That answers the obvious worry about it — the variant pin is NOT blind to an
    exact-match fake, it goes red, because such a fake mints a second label where the server
    refuses
  * both of those together -> 1 failed
  * remove the fake's 400 and leave the guard -> 1 failed: only the mirror's own pin. This is the
    round that narrowed the paragraph above
  * remove BOTH the guard and the fake's 400, i.e. the pre-#1216 world -> 6 failed
  * hand the epic-ready site the hollowed `parent` sub-dict instead of the re-fetched
    `full_parent` -> 0 failed. A blind spot recorded rather than papered over; the test that would
    have been its pin says why there is nothing observable to catch
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import (
    LABEL_BLOCKED,
    LABEL_EPIC,
    LABEL_REVIEW_FAILED,
    LABEL_REVIEWED,
    STAGES,
    Workflow,
)


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = api.add_task("job", "Design", assignee=api.me_user)
    return api, wf, task


def _titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]


def _hand_label(api, task_id, title):
    """A HUMAN's hand puts the label on — the shape three of the four routes need. Goes through
    `api.add_label` rather than poking `tasks[...]["labels"]`, so the fixture itself is subject to
    the same refusal the code under test is."""
    api.add_label(task_id, api.get_or_create_label(title)["id"])


def test_the_fake_refuses_a_duplicate_add_like_the_server(env):
    """The assumption the rest of this module stands on. Real 2.3.0 answers a duplicate
    `PUT /tasks/{id}/labels` with `400 {"code":8001,"message":"This label already exists on the
    task."}` — measured through this package's own client, not read out of the API docs. Asserted
    on the STATUS (what a production `except` branches on) and on the code (the only thing telling
    this 400 from any other), and asserted TWICE, because the refusal is a state and not a
    once-off."""
    api, _wf, t = env
    label = api.get_or_create_label("dup-probe")
    api.add_label(t["id"], label["id"])
    for _attempt in range(2):
        with pytest.raises(VikunjaError) as err:
            api.add_label(t["id"], label["id"])
        assert err.value.status == 400
        assert "8001" in err.value.message
    assert _titles(api, t["id"]).count("dup-probe") == 1


def test_second_approve_leaves_exactly_one_reviewed(env):
    """ROUTE 1, the only one of the four that needs NO human step. An approve leaves the card in
    Review and `review_task` gates on verdict/report/stage/independence — nothing rejects a second
    verdict — so an orchestrator dispatching two reviewers onto one piece of work (SKILL.md warns
    since #991 that a verdict-less card is re-offered WITHIN a tick) drove the second `reviewed`
    add into the 400. Both reports must survive: the fix is idempotence, not swallowing the second
    review."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="abc123")

    wf.review_task(t["id"], "approve", "first reviewer")
    wf.review_task(t["id"], "approve", "second reviewer, re-offered within the tick")

    assert _titles(api, t["id"]).count(LABEL_REVIEWED) == 1
    joined = "\n".join(api.comments_text(t["id"]))
    assert "first reviewer" in joined and "second reviewer" in joined


def test_needs_work_on_a_card_already_carrying_review_failed(env):
    """ROUTE 2. `advance` clears both verdict labels on its way into Review; a human HAND-DRAGGING
    a bounced card back fires no tool, so the card arrives still wearing `review-failed`. Pinning
    the MOVE as well as the label is the point: before the fix the 400 landed between the comment
    and the move, so the card stayed in Review with the rejection already in its journal."""
    api, wf, _t = env
    t = api.add_task("hand-dragged back", "Review", assignee=api.me_user)
    _hand_label(api, t["id"], LABEL_REVIEW_FAILED)

    wf.review_task(t["id"], "needs_work", "still broken")

    assert _titles(api, t["id"]).count(LABEL_REVIEW_FAILED) == 1
    assert api.stage_of(t["id"]) == "Build"


def test_return_task_on_a_card_a_human_already_labelled_blocked(env):
    """ROUTE 3 — and one of the two sites that bypassed `_add_label` ALTOGETHER, inlining
    `get_or_create_label` + `api.add_label`. A guard added to the helper alone would never have
    reached here, which is exactly why the fix routed both bypasses through it."""
    api, wf, t = env
    _hand_label(api, t["id"], LABEL_BLOCKED)

    wf.return_task(t["id"], "waiting on an external dependency")

    assert _titles(api, t["id"]).count(LABEL_BLOCKED) == 1
    assert api.stage_of(t["id"]) == "Backlog"


def test_decompose_on_a_card_a_human_already_labelled_epic(env):
    """ROUTE 4, the second bypass. `claim` refuses a card already labelled `epic` (a container is
    not a unit of work), so this state is reached by a human labelling a card that is ALREADY in
    flight — which is what a human deciding mid-work "this is really an epic" does."""
    api, wf, t = env
    _hand_label(api, t["id"], LABEL_EPIC)

    wf.decompose(t["id"], [{"title": "part one"}, {"title": "part two"}])

    assert _titles(api, t["id"]).count(LABEL_EPIC) == 1
    assert api.stage_of(t["id"]) == "Backlog"


def test_epic_ready_marker_still_fires_through_the_resigned_helper(env):
    """`_add_label` changed signature (id -> task snapshot), so the epic-ready site now hands it
    the full re-fetched parent it already holds. This is a REGRESSION pin on the marker (the label
    AND the comment still land through the resigned helper) and it is worth having, because the
    marker is wrapped in a best-effort `except Exception` that leaves only one stderr line — a
    break here is silent.

    WHAT IT DOES NOT PIN, measured rather than assumed: swapping `full_parent` for the HOLLOWED
    `related_tasks` sub-dict `parent` leaves this suite entirely green (control 0 failed / 0
    errors / 129 collected; that round 0 failed). Not a hole in the test — there is no observable
    difference to catch. That site reaches `_add_label` only after its OWN `continue` has
    established the label is absent, so the helper's guard is belt-and-braces there and a hollowed
    snapshot degrades to exactly the pre-#1216 behaviour. `full_parent` is still the right
    argument (it is a real guard rather than a guaranteed-False one, and it costs nothing — the
    fetch already happened); it is simply not a claim any test here backs."""
    api, wf, _t = env
    epic = api.add_task("epic parent", "Backlog", labels=[LABEL_EPIC])
    child = api.add_task("only child", "Design", assignee=api.me_user)
    api.add_relation(child["id"], epic["id"], "parenttask")

    wf.advance(child["id"], to="build", spec="s")
    wf.advance(child["id"], to="review", worklog="w", evidence="abc123")

    assert "epic-ready" in _titles(api, epic["id"])
    assert any(c.startswith("[epic-ready]") for c in api.comments_text(epic["id"]))


def test_a_title_VARIANT_does_not_slip_past_the_guard(env):
    """The leak the FIRST draft of the guard had, found by this card's second independent pass and
    then measured on a real 2.3.0 rather than argued.

    `Workflow._has_label` compares titles EXACTLY; `api.get_or_create_label` resolves case- and
    whitespace-INSENSITIVELY, deliberately (a bot typing `Bug`/`bug ` once forked a duplicate
    label — api.py records the date). So a guard written as `_has_label(task, title)` and a
    resolution written as `get_or_create_label(title)` disagree about what "this label" means, and
    the gap is the whole defect again: measured on a real container, a card carrying `Vari906071`
    with no lowercase twin gave `_has_label(card,'vari906071') -> False`,
    `get_or_create_label('vari906071') -> that same label`, and the PUT `400 code 8001`. The guard
    now resolves FIRST and asks whether that LABEL ID is on the snapshot — the same question the
    server asks — so the disagreement cannot arise.

    This is only visible here because `FakeAPI.get_or_create_label` was made 1:1 in the same
    change; it was exact-match before, under which this sequence minted a SECOND label and stayed
    green."""
    api, wf, t = env
    variant = api.create_label(LABEL_BLOCKED.capitalize())     # the human typed it capitalised
    api.add_label(t["id"], variant["id"])
    assert not Workflow._has_label(api.tasks[t["id"]], LABEL_BLOCKED), (
        "the premise of this test: the exact-title check must MISS the variant, otherwise the "
        "test is not exercising the gap it is named for"
    )

    wf._add_label(api.tasks[t["id"]], LABEL_BLOCKED)           # must be a no-op, not a 400

    assert [lb["title"] for lb in api.tasks[t["id"]]["labels"]] == [LABEL_BLOCKED.capitalize()], (
        "the guard must resolve the title the way the server does and then skip: neither a 400 "
        "nor a second label on the card"
    )


# --- the ORDER the verdict is written in --------------------------------------------------


def test_a_failed_label_write_leaves_no_verdict_comment_behind(env):
    """The ORDER pin, and the reason the swap was made. Labels are written BEFORE the verdict
    comment, so a label write that fails leaves the card UNTOUCHED — no report, no label — and the
    caller's error is the whole truth. Before #1216 the comment went first, so the same failure
    left the report on the card and the label absent: a verdict the board denies and the journal
    asserts. Driven by making the write fail, not by reading the source, because the ORDER is the
    thing under test and only a failure can see it."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="abc123")

    def explode(task_id, label_id):
        raise VikunjaError(500, "the label write failed")

    api.add_label = explode
    with pytest.raises(VikunjaError):
        wf.review_task(t["id"], "approve", "a report that must NOT reach the card")

    assert not any(c.startswith("[review]") for c in api.comments_text(t["id"]))
    assert LABEL_REVIEWED not in _titles(api, t["id"])


def test_the_new_failure_mode_is_the_recoverable_one(env):
    """THE NEW FAILURE MODE, stated as a test rather than as a promise: the comment now goes last,
    so it is the COMMENT that can fail alone, leaving the verdict label with no report.

    That state is the reason the order was chosen. `next_task`'s review offering reads COMMENTS
    (last `[worklog]` vs last `[review]`) and never a verdict label, so this card is offered
    exactly as it was before the failure and the next tick sends a reviewer who writes the report.
    The old order's orphan state is the mirror of it: measured on the fake before the swap, a card
    carrying a `[review]` comment and no label is NOT offered again. That is "nothing routes a
    reviewer back automatically", not "unrecoverable" — `review_task` gates on stage alone, so a
    human handing someone the id still lands a verdict."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="abc123")
    api.tasks[t["id"]]["assignees"] = []          # a reviewer is not the card's own assignee

    def explode(task_id, text):
        raise VikunjaError(500, "the comment write failed")

    real_add_comment = api.add_comment
    api.add_comment = explode
    with pytest.raises(VikunjaError):
        wf.review_task(t["id"], "approve", "a report that never lands")
    api.add_comment = real_add_comment

    assert _titles(api, t["id"]) == [LABEL_REVIEWED]
    assert not any(c.startswith("[review]") for c in api.comments_text(t["id"]))
    offer = wf.next_task()
    assert offer.get("review") is True and offer["task"]["id"] == t["id"], (
        "a verdict label with no report must stay in the review offering — that recoverability "
        f"is the whole reason the comment goes last; got {offer}"
    )


# --- the invariant lives in ONE place -----------------------------------------------------


def test_api_add_label_has_exactly_one_caller_in_the_package(env):
    """THE ANTI-BYPASS PIN, and the one that closes the CLASS rather than the four instances.

    The defect was not four forgotten checks, it was that there was no single write path where a
    check COULD be stated: `return_task` and `decompose` each inlined `get_or_create_label` +
    `api.add_label`, and the epic-ready guard was that site's own `continue`. So the property
    worth pinning is structural — `api.add_label` is reachable from exactly one place in
    `vikunja_mcp`, `Workflow._add_label`, which is where the guard is. Re-inline it anywhere and
    this goes red with the name of the file that did it.

    It reads SOURCE, deliberately: a behavioural test can only catch the routes someone thought
    of, and the four above are exactly the routes someone thought of. Counting `.add_label(` (not
    `_add_label(`) is what distinguishes the client call from the guarded helper.
    """
    import pathlib

    import vikunja_mcp

    root = pathlib.Path(vikunja_mcp.__file__).parent
    hits = []
    for path in sorted(root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if ".add_label(" in code and "._add_label(" not in code:
                hits.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
    assert len(hits) == 1, (
        "api.add_label must have exactly ONE caller — Workflow._add_label, which carries the "
        "idempotency guard. A second caller is a bypass of it, which is the #1216 defect "
        "itself:\n  " + "\n  ".join(hits)
    )
    assert hits[0].startswith("workflow.py:"), hits
