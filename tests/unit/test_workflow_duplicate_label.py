"""`add_label` on a label the card ALREADY carries — the guard, the single write path, and the
order the verdict is written in (#1216).

WHAT THESE TESTS REST ON, narrowed by the sweep that measured it rather than by what it looked
like. `FakeAPI.add_label` refuses a duplicate the way real 2.3.0 does (`400 {"code":8001,...}`),
which it did NOT do until this card: it appended a second copy and stayed green, so a whole green
unit suite — 1367 at `f7de8d7`, the commit this fix was cut from — coexisted with four reachable
duplicate-add routes in `workflow`. FOUR counts the reaches whose 400 SURFACES to the caller as a
failed agent tool; the epic-ready site was a fifth reach all along (`_add_label` unguarded, behind
only an exact-title `continue` — `f7de8d7:2172`/`:2186`), and its 400 is swallowed by the marker's
best-effort wrapper, which is why it took the #1216 rework to see it. The obvious thing to say next
— that without the fake's 400 the rest of this module stops measuring anything — is FALSE, and a
round says so: with the mirror removed and the guard left in, only the mirror pin goes red (control
0 failed / 0 errors / 131 collected; that round 1 failed / 0 errors / 131 collected), and with BOTH
removed 7 fail, not 1. The route tests assert the label COUNT, so they see a duplicate append as
readily as an exception. What the mirror actually buys is that the fake stops being more generous
than the server on this endpoint — a 1:1 rule of this repo, and what stopped a route test that
merely DROVE one of these sequences from going green anyway. What made the four routes invisible is
simpler and worth not dressing up: nobody had written such a test. The SERVER end of the mirror is
pinned where only it can be, against a real container:
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

THE SWEEP, ten rounds against ONE control and all of them against the FINAL code — re-run in full
in the #1216 rework, because the pin added there is inside the selection and moved SIX of the ten
rows (the four route tests' rounds are the ones it left alone). Selection every time is this file +
`test_workflow_epic_marker.py` + `test_workflow_gates.py`, in a fresh clone with `__pycache__`
deleted and PYTHONDONTWRITEBYTECODE=1, `vikunja_mcp.__file__` printed each round, rounds read by
COUNTING lines beginning `FAILED ` and `ERROR ` counted separately. Control (unmutated): 0 failed,
0 errors, 131 collected at `d80c174` — every round below reports the same 131 collected and 0
errors, and each names what it killed.
**THAT ANCHOR IS LOAD-BEARING AND NOT DECORATION: every figure below is a property of THAT tree,
and not of the one you are reading — and it was CHECKED, not assumed.** That tree extracted with
`git archive`: control 0 failed / 0 errors / 131 collected, and ALL TEN rows reproduce their
stated figure exactly there — 6, 2, 2, 2, 2, 2, 2, 1, 7, 1, in the order they are written below.
The anchor is therefore not a hedge over rows nobody checked; it is a label on a tree that was
re-run whole.
#1256 then routed `_has_label` through `api.label_key` and MOVED six of the ten. TWO of those six
carry a note at the row itself, because each makes a claim about its own mutation and nowhere else
would hold it: the exact-TITLE row and the hollowed-`parent` one, and this rework had to correct
BOTH of those notes. The other FOUR are given here, once and together, rather than row by row —
what settles the register is the anchor above, and annotating four more rows would only sharpen
the implicature that the rest are current. Those four, at
`57762ef` on this same three-file selection (in a clone of the release bump directly above it,
which touches only the three version files), control run FIRST and 0 failed / 0 errors / 131
collected there too: delete-the-guard 6 -> 5 failed, fake-exact 2 -> 1 failed,
both-of-those-together 2 -> 1 failed reading "both" as the row above reads (byte-exact title guard
+ exact-match fake) and 0 failed reading it via `_has_label` — the same split the exact-TITLE row
carries, and it has to be named for the same reason — and remove-BOTH 7 -> 6 failed. UNCHANGED at
both trees, four rows: the two re-inlined bypasses and the verdict-comment order at 2 each, and
remove-the-fake's-400-and-leave-the-guard at 1.
  * delete the guard inside `_add_label` -> 6 failed: the four route tests and both variant pins
  * re-inline the bypass in `return_task` -> 2 failed: its own route, plus the one-caller pin
  * re-inline the bypass in `decompose` -> 2 failed: the same pair
  * put the verdict comment back BEFORE the labels in the approve branch -> 2 failed: both order
    tests
  * key the guard on the exact TITLE instead of the resolved label ID (the guard's own first
    draft) -> 2 failed: both variant pins — the standalone one on the raised 400, the epic-ready
    one on the marker that same 400 costs it. The epic-ready pin is in there because that site's
    `continue` lets a variant-marked parent REACH the helper, after which a title-keyed guard
    misses there exactly as it does anywhere else.
    **#1256 MOVED THIS ROW, AND SPLIT IT IN TWO — its own annotation said only "to 0", which is
    true of ONE of the two readings this row's words now have.** Before #1256 "the exact TITLE"
    and "the guard's own first draft" (`_has_label`) were the SAME mutation and both give the 2
    above; after it they are different, and the split is measured, each against a control of 0
    failed / 0 errors / 131 collected on this selection: `if self._has_label(task, title)` -> 0
    failed, a byte-exact `lb.get("title") == title` -> 1 failed, the standalone variant pin. Name
    the form or the row is unreproducible from its own words.
    That card routed `_has_label` through `api.label_key`, so a title-keyed guard now answers what
    the id-keyed one answers on every state this package's ORDINARY write path creates, the
    `continue` above no
    longer lets a variant-marked parent reach the helper at all, and the mutation kills nothing —
    re-measured 0 failed against a clean control of 0 on that card's own selection. What still
    tells the two guards apart is written up in `_add_label`'s docstring; nothing in the unit
    suite sees it
  * give `FakeAPI.get_or_create_label` its pre-#1216 exact-match resolution back -> 2 failed: the
    same pair. That answers the obvious worry about it — the variant pins are NOT blind to an
    exact-match fake, they go red, because such a fake mints a second label where the server
    refuses
  * both of those together -> 2 failed
  * remove the fake's 400 and leave the guard -> 1 failed: only the mirror's own pin. This is the
    round that narrowed the paragraph above
  * remove BOTH the guard and the fake's 400, i.e. the pre-#1216 world -> 7 failed
  * hand the epic-ready site the hollowed `parent` sub-dict instead of the re-fetched
    `full_parent` -> 1 failed: the epic-ready variant pin, and nothing else. (#1256 annotated this
    row with a 7 and blamed `_has_label` resolving. BOTH halves were wrong, and what stands here is
    the correction. That 7 belongs to a BROADER swap — hollowing the two `_has_label(full_parent,
    …)` READS as well — and it is 7, the same seven tests, at `d80c174` and at `edbb8e4` too, i.e.
    on trees with no #1256 in them, because `_has_label` iterates `task.get("labels") or []` and a
    hollowed `labels: None` misses however titles are compared; the resolving does no work there,
    so that 7 is not attributable to this card at all. THIS row's own mutation — the argument to
    `_add_label`, nothing else — measures 1 failed at `d80c174`, reproducing the row on the tree it
    was written for, and 0 failed at `57762ef`, each against a control of 0 failed / 0 errors /
    131 collected run first on that same tree.) THIS ROW USED TO READ
    0, and was written up as a blind spot with nothing observable to catch; that reading was wrong
    and the pin now in the selection is what refutes it — the round returned 0 because the
    selection held no parent whose marker was spelled a way `get_or_create_label` resolves and
    `_has_label` does not. Capitalised is one such spelling and the one pinned; both the client and
    the fake resolve on `.strip().casefold()`, so a trailing space is another. The ISOLATING PAIR,
    on the same selection with that pin DELETED: pristine code 0 failed / 0 errors / 130 collected,
    hollowed `parent` 0 failed / 0 errors / 130 collected. So the old 0 reproduces exactly, the pin
    is the only thing in the selection that sees the swap, and 130 — not the 129 two of these
    figures used to carry — is what the control measured before it
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import (
    LABEL_BLOCKED,
    LABEL_EPIC,
    LABEL_EPIC_READY,
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


def _epic_ready(api, task_id):
    """Both halves of the epic-ready marker — the label and its `[epic-ready]` comment — read
    the way the SERVER resolves a label title. `epic-ready` and `Epic-ready` are ONE label to
    `get_or_create_label`, so counting exact titles would miss a duplicate minted under a variant,
    which is the very disagreement this module is about."""
    labels = sum(1 for t in _titles(api, task_id) if t.casefold() == LABEL_EPIC_READY)
    comments = sum(1 for c in api.comments_text(task_id) if c.startswith("[epic-ready]"))
    return labels, comments


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

    WHAT **THIS** TEST DOES NOT PIN, narrowed in the #1216 rework because the sentence that stood
    here was wrong and, worse, told the next agent not to write the pin. Swapping `full_parent`
    for the HOLLOWED `related_tasks` sub-dict `parent` leaves THIS construction green, and that
    round was read as "there is no observable difference to catch", on the grounds that the site
    reaches `_add_label` only after its own `continue` has established the label is absent. THE
    `continue` DOES NOT ESTABLISH THAT: it asks `_has_label`, which compares titles EXACTLY, while
    the guard resolves through `get_or_create_label` and asks by label ID — this card's own
    `root_cause`, surviving one level up in the file that fixed it. An observable case therefore
    exists, and it is pinned directly below. What is true is the narrower claim, and only of THIS
    construction: the parent here carries no epic-ready label under any spelling, so the guard is
    genuinely a no-op and a hollowed snapshot degrades to exactly the pre-#1216 behaviour.
    `full_parent` is the right argument for the reason the docstring already gave two sentences
    on — a real guard rather than a guaranteed-False one — and that half was never in doubt."""
    api, wf, _t = env
    epic = api.add_task("epic parent", "Backlog", labels=[LABEL_EPIC])
    child = api.add_task("only child", "Design", assignee=api.me_user)
    api.add_relation(child["id"], epic["id"], "parenttask")

    wf.advance(child["id"], to="build", spec="s")
    wf.advance(child["id"], to="review", worklog="w", evidence="abc123")

    assert "epic-ready" in _titles(api, epic["id"])
    assert any(c.startswith("[epic-ready]") for c in api.comments_text(epic["id"]))


def test_epic_ready_on_parents_where_a_human_typed_the_marker_CAPITALISED(env):
    """The pin the round above was said to be unable to have (#1216 rework) — this card's own
    defect, surviving one level up in the file that fixed it. **#1256 INVERTED ITS PREMISE, so
    what it pins today is not what it pinned when it was written**, and the history is kept here
    because a reader who only sees the new assertions would conclude the old ones were wrong.

    WHAT IT PINNED AT #1216. On a parent a human marked `Epic-ready` the site's exact-title
    `continue` MISSED, so the site reached `_add_label` with that label genuinely present, and the
    id-keyed guard was what stopped the PUT. Two parents, because one understated the loss:
    measured against the hollowed `parent` sub-dict, a SINGLE parent lost only the `[epic-ready]`
    comment (the label being byte-identical in both worlds), while the 400 aborted the
    `for parent in parents` loop, so a LATER parent lost the LABEL — the half a human reads off
    the board — outright. Both losses were one swallowed stderr line, the marker being
    best-effort.

    WHAT IT PINS AT #1256, and it is the better thing. `_has_label` now resolves through
    `api.label_key`, so the `continue` SEES the human's spelling and the site never reaches
    `_add_label` at all: the hand-marked parent is left exactly as the human left it — one label,
    and NO second `[epic-ready]` comment (measured `(1, 0)`, against `(1, 1)` before). That is
    what the `continue`'s own comment has always claimed it does ("already marked — idempotent"),
    and it was false for every spelling but one. The second parent is the half that did not move:
    it is marked normally, `(1, 1)`, which is what says the first parent did not abort the loop —
    now because nothing raises rather than because a guard caught it.

    AND IT PICKS UP THE HUMAN'S SPELLING, which is worth seeing: the second parent's label reads
    `Epic-ready`, not `epic-ready`, because `get_or_create_label` resolved to the row the human
    minted. `_epic_ready` counts case-insensitively for exactly that reason."""
    api, wf, _t = env
    first = api.add_task("epic one", "Backlog", labels=[LABEL_EPIC])
    _hand_label(api, first["id"], LABEL_EPIC_READY.capitalize())   # the human typed it capitalised
    second = api.add_task("epic two", "Backlog", labels=[LABEL_EPIC])
    child = api.add_task("only child", "Design", assignee=api.me_user)
    api.add_relation(child["id"], first["id"], "parenttask")
    api.add_relation(child["id"], second["id"], "parenttask")
    assert Workflow._has_label(api.get_task(first["id"]), LABEL_EPIC_READY), (
        "the premise since #1256: the site's `continue` must SEE the variant. Before #1256 this "
        "line was its negation, and that miss was the disagreement this test is named for"
    )
    assert LABEL_EPIC_READY not in _titles(api, first["id"]), (
        "and the premise of the premise: no lowercase twin is on the card, so a byte-exact check "
        "would still miss — the resolution is doing the work, not the fixture"
    )

    wf.advance(child["id"], to="build", spec="s")
    wf.advance(child["id"], to="review", worklog="w", evidence="abc123")

    assert _epic_ready(api, first["id"]) == (1, 0), (
        "the human already marked it: one label, and NO second [epic-ready] comment. This read "
        "(1, 1) before #1256, when the `continue` missed and the site re-announced a mark that "
        "was already there"
    )
    assert _epic_ready(api, second["id"]) == (1, 1), "the first parent must not abort the loop"


def test_a_title_VARIANT_does_not_slip_past_the_guard(env):
    """The leak the FIRST draft of the guard had, found by this card's second independent pass and
    then measured on a real 2.3.0 rather than argued.

    `Workflow._has_label` compared titles EXACTLY when this was written; `api.get_or_create_label`
    resolves case- and whitespace-INSENSITIVELY, deliberately (a bot typing `Bug`/`bug ` once
    forked a duplicate label — api.py records the date). So a guard written as
    `_has_label(task, title)` and a resolution written as `get_or_create_label(title)` disagreed
    about what "this label" means, and the gap was the whole defect again: measured on a real
    container, a card carrying `Vari906071` with no lowercase twin gave
    `_has_label(card,'vari906071') -> False`, `get_or_create_label('vari906071') -> that same
    label`, and the PUT `400 code 8001`. The guard resolves FIRST and asks whether that LABEL ID
    is on the snapshot — the same question the server asks — so the disagreement cannot arise.

    #1256 INVERTED THE PREMISE AND, WITH IT, WHAT THIS TEST MEASURES. `_has_label` resolves
    through `api.label_key` now, so the two agree here and a title-keyed guard would skip this
    PUT for the same reason the id-keyed one does. The assertions below are unchanged and still
    true; what is gone is their power to tell the two guards apart — measured, keying the guard on
    the title kills nothing in this file's own sweep selection any more. Where the two still
    differ is a board holding TWO variant rows — and this sentence used to add that nothing in
    this package creates that state, which is WRONG and is corrected rather than kept.
    `api.get_or_create_label` is read-`labels()`-then-`create_label` with nothing atomic between
    the two, so at `wip_limit > 1` two agents adding the same absent label both miss and both
    create; and `GET /labels` surfaces only labels used on a task the caller can READ, so a row
    invisible to one caller gets minted again. `_add_label`'s docstring carries the full reading;
    the state is filed as a question of its own rather than answered here.

    This is only visible here because `FakeAPI.get_or_create_label` was made 1:1 in the same
    change; it was exact-match before, under which this sequence minted a SECOND label and stayed
    green."""
    api, wf, t = env
    variant = api.create_label(LABEL_BLOCKED.capitalize())     # the human typed it capitalised
    api.add_label(t["id"], variant["id"])
    assert LABEL_BLOCKED not in _titles(api, t["id"]), (
        "the premise: no lowercase twin is on the card, so a byte-exact title check would MISS "
        "the variant — that is the gap this test is named for"
    )
    assert Workflow._has_label(api.tasks[t["id"]], LABEL_BLOCKED), (
        "the premise since #1256: `_has_label` resolves the way the server does, so it SEES the "
        "variant. Before #1256 this line was its negation"
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
