"""`add_label` on a label the task ALREADY carries — against REAL Vikunja 2.3.0 (#1216).

Why this must be an integration test, in one sentence: the whole card is a claim about a SERVER
RESPONSE, and the unit suite structurally could not see it — `FakeAPI.add_label` appended a second
copy and stayed green, so the four reachable duplicate-add routes in `workflow` were invisible to
a whole green unit suite (1367 at `f7de8d7`, the commit this fix was cut from). That is the "more
generous than the server" mode `FakeAPI._read_task`'s docstring calls the #125 failure mode, and
this repo has paid it before: in the #118 Part 2 rework the fake returned a hollowed sub-dict's
labels fully populated and twelve unit tests were green over an epic marker that was dead in
production. (That count is quoted from `fakes.py`, not re-measured here.)

Two things are pinned here and they are different in kind. `test_duplicate_add_label_is_a_400`
pins the SERVER's answer — it is the source the fake now mirrors, so if a later Vikunja turns the
duplicate PUT into a no-op (or into a different status), this goes red and the fake's mirror is
known to be stale. The four route tests pin the WORKFLOW: each drives, with agent tools only, a
sequence that answered 400 before #1216.

#1456 ADDED A THIRD KIND: the exact BOUNDARY of that 400, which is what decides which idempotency
guard `Workflow._add_label` should carry. `test_a_duplicate_label_TITLE_is_accepted_by_the_server`
and `test_a_SECOND_row_of_one_key_is_accepted_onto_a_card_already_wearing_the_first` say that the
refusal is keyed on the `label_id` and on nothing else — a duplicate TITLE mints another row, and
that other row goes onto a card already wearing the first. Both had to be asked here rather than of
`FakeAPI`, because #1216 chose the guard against a real 2.3.0 and answering the follow-up on a fake
would have traded a measured decision for an unmeasured one; the full reading is in
`Workflow._add_label`'s docstring and in `docs/dossier/workflow.md`.
"""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI, VikunjaError, label_key
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import (
    LABEL_BLOCKED,
    LABEL_EPIC,
    LABEL_REVIEW_FAILED,
    LABEL_REVIEWED,
    Workflow,
)

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def dupproj(boss_jwt, agent_jwts):
    """Isolated project + canonical board (mirrors test_epic_marker). The boss (full perms) plays
    the HUMAN — it is a human hand that puts `blocked`/`epic`/`review-failed` on a card in three of
    the four routes — and the agent's scoped-token Workflow is the subject under test."""
    boss = VikunjaAPI(BASE, boss_jwt)
    pid = reconcile(boss, f"dup-{uuid.uuid4().hex[:8]}", shares=[("agent1", 1)])
    view = boss.kanban_view(pid)
    buckets = {b["title"]: b["id"] for b in boss.buckets(pid, view["id"])}
    jwt1, _ = agent_jwts
    wf = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid)
    return boss, pid, view, buckets, wf


def _labels(api, task_id):
    return [lb["title"] for lb in (api.get_task(task_id).get("labels") or [])]


def test_duplicate_add_label_is_a_400(dupproj):
    """THE SERVER FACT the whole card rests on, and the one `FakeAPI.add_label` mirrors.

    `PUT /tasks/{id}/labels` with a `label_id` the task already carries answers
    `400 {"code":8001,"message":"This label already exists on the task."}`. Asserted on the STATUS
    plus the code, because status is what a production `except` branches on and the body is the
    only thing telling this 400 from any other. A third add is asserted too: the refusal is a
    STATE, not a once-off, so it does not decay into a no-op on repetition.
    """
    boss, pid, view, buckets, _ = dupproj
    task = boss.create_task(pid, "dup-label subject")
    boss.move_task(pid, view["id"], buckets["Backlog"], task["id"])
    label = boss.get_or_create_label("dup-probe")

    boss.add_label(task["id"], label["id"])          # first add: fine
    for _attempt in range(2):
        with pytest.raises(VikunjaError) as err:
            boss.add_label(task["id"], label["id"])
        assert err.value.status == 400
        assert "8001" in err.value.message
    # and the first add is still the only one — the refusals changed nothing
    assert _labels(boss, task["id"]).count("dup-probe") == 1


def test_second_approve_does_not_400_and_leaves_one_reviewed(dupproj):
    """ROUTE 1, the sharpest: two `review_task(..., 'approve')` on one card, agent tools only.

    An approve leaves the card in Review and nothing rejects a second verdict, so an orchestrator
    that dispatches two reviewers onto one piece of work (SKILL.md warns since #991 that a card
    without a verdict is re-offered WITHIN a tick) drove the second `reviewed` add straight into
    the 400 — AFTER the `[review] APPROVE` comment had already landed. Both halves are pinned: the
    call succeeds, and BOTH reports are on the card with exactly one `reviewed`.
    """
    boss, pid, view, buckets, wf = dupproj
    task = boss.create_task(pid, "two approves")
    boss.move_task(pid, view["id"], buckets["Review"], task["id"])

    wf.review_task(task["id"], "approve", "first reviewer")
    wf.review_task(task["id"], "approve", "second reviewer, re-offered within the tick")

    assert _labels(boss, task["id"]).count(LABEL_REVIEWED) == 1
    bodies = [c.get("comment") or "" for c in boss.comments(task["id"])]
    assert sum("first reviewer" in b for b in bodies) == 1
    assert sum("second reviewer" in b for b in bodies) == 1


def test_needs_work_on_a_card_already_carrying_review_failed(dupproj):
    """ROUTE 2: a human hand-drags a bounced card back to Review, so it arrives still wearing
    `review-failed` (`advance` would have cleared it; a hand-drag fires no tool), and the reviewer
    fails it again."""
    boss, pid, view, buckets, wf = dupproj
    task = boss.create_task(pid, "already failed")
    boss.add_assignee(task["id"], boss.me()["id"])   # assigned -> the bounce goes to Build
    boss.add_label(task["id"], boss.get_or_create_label(LABEL_REVIEW_FAILED)["id"])
    boss.move_task(pid, view["id"], buckets["Review"], task["id"])

    wf.review_task(task["id"], "needs_work", "still broken")

    assert _labels(boss, task["id"]).count(LABEL_REVIEW_FAILED) == 1


def test_return_task_on_a_card_a_human_already_labelled_blocked(dupproj):
    """ROUTE 3, and one of the two that bypassed `_add_label` entirely: `return_task` INLINED
    `get_or_create_label` + `api.add_label`, so a guard on the helper could never have reached
    it."""
    boss, pid, view, buckets, wf = dupproj
    task = boss.create_task(pid, "already blocked")
    boss.add_label(task["id"], boss.get_or_create_label(LABEL_BLOCKED)["id"])
    boss.move_task(pid, view["id"], buckets["Queue"], task["id"])
    wf.claim(task["id"])

    wf.return_task(task["id"], "waiting on an external dependency")

    assert _labels(boss, task["id"]).count(LABEL_BLOCKED) == 1


def test_decompose_on_a_card_a_human_already_labelled_epic(dupproj):
    """ROUTE 4, the second bypass. `claim` refuses a card ALREADY labelled `epic` (a container is
    not a unit of work), so the label goes on AFTER the claim — which is exactly what a human
    labelling an in-flight card does."""
    boss, pid, view, buckets, wf = dupproj
    task = boss.create_task(pid, "already an epic")
    boss.move_task(pid, view["id"], buckets["Queue"], task["id"])
    wf.claim(task["id"])
    boss.add_label(task["id"], boss.get_or_create_label(LABEL_EPIC)["id"])

    wf.decompose(task["id"], [{"title": "part one"}, {"title": "part two"}])

    assert _labels(boss, task["id"]).count(LABEL_EPIC) == 1


def test_a_title_variant_does_not_slip_past_the_guard(dupproj):
    """The leak the guard's FIRST draft had — pinned HERE because the fake could not have shown it.

    `Workflow._has_label` compared titles EXACTLY when this was written; `api.get_or_create_label`
    resolves case- and whitespace-insensitively on purpose. A guard written with the first and a
    resolution written with the second disagree about what "this label" means, and the server
    settles the argument: measured on this container before the fix, a card carrying a capitalised
    title with no lowercase twin gave `_has_label -> False`, `get_or_create_label -> that same
    label`, and the PUT `400 code 8001`. The title here is generated so no lowercase twin can
    exist — that is the whole premise, and the assertions check it rather than assuming it.

    #1256 MOVED ONE OF THOSE ASSERTIONS TO ITS NEGATION, and that is the point of this note rather
    than a footnote to it: `_has_label` now resolves through `api.label_key`, so it SEES the
    variant, and the premise that survives is the one about the RAW titles — no lowercase twin is
    on the card, so a byte-exact check would still miss. What is still pinned here is the thing
    only a real server can say: the guard makes `_add_label` a NO-OP rather than a `400 code 8001`.
    """
    boss, pid, view, buckets, wf = dupproj
    title = "vari" + uuid.uuid4().hex[:6]
    task = boss.create_task(pid, "variant carrier")
    variant = boss.create_label(title.capitalize())
    boss.add_label(task["id"], variant["id"])
    boss.move_task(pid, view["id"], buckets["Build"], task["id"])

    card = wf.api.get_task(task["id"])
    assert title not in _labels(boss, task["id"]), (
        "premise: no lowercase twin is on the card — a byte-exact title check would MISS it"
    )
    assert Workflow._has_label(card, title), (
        "premise since #1256: `_has_label` resolves the way the server does, so it SEES the "
        "variant. Before #1256 this line was its negation, and that gap WAS the leak"
    )
    assert wf.api.get_or_create_label(title)["id"] == variant["id"], (
        "premise: the client must RESOLVE the lowercase title to the capitalised label"
    )

    wf._add_label(card, title)          # must be a no-op against the server, not a 400

    assert _labels(boss, task["id"]) == [title.capitalize()]


def test_a_duplicate_label_TITLE_is_accepted_by_the_server(dupproj):
    """SERVER FACT ONE of the pair #1456 was filed to measure: `PUT /labels` with a title that
    ALREADY EXISTS is ACCEPTED, byte-identically and in a case variant alike.

    WHY IT DECIDES SOMETHING. `api.get_or_create_label` is read-`labels()`-then-`create_label`
    with nothing atomic between the two, so at `wip_limit > 1` two agents adding the same absent
    label both miss and both create. Whether that race FORKS A ROW or merely 400s is a question
    only the server can answer, and until this test nobody had asked it: `Workflow._add_label`'s
    docstring named the race as a route to a two-row board while resting on an unmeasured premise.
    It forks. So the divergent board is one this package reaches with nobody outside it, which is
    the third of the three grounds on which #1456 returned the guard to the `_has_label` form.

    `FakeAPI.create_label` appends unconditionally, i.e. it was already 1:1 with this — and #1456
    is where that stopped being an assumption and became a measurement. The check is on the ROWS
    rather than on the absence of an exception, because "accepted" here means a second row exists,
    not merely that nothing raised.
    """
    boss, _pid, _view, _buckets, _wf = dupproj
    stem = "dupkey" + uuid.uuid4().hex[:6]

    same = [boss.create_label(stem), boss.create_label(stem)]
    variant = boss.create_label(stem.capitalize())

    assert len({lb["id"] for lb in same + [variant]}) == 3, (
        "three distinct rows: the server minted a second row for the identical title and a third "
        "for the case variant, rather than refusing either or returning the existing one"
    )
    rows = [lb for lb in boss.labels() if label_key(lb["title"]) == label_key(stem)]
    assert sorted(lb["title"] for lb in rows) == sorted([stem, stem, stem.capitalize()])
    assert boss.get_or_create_label(stem)["id"] == same[0]["id"], (
        "and the client resolves to the FIRST of them — which is what makes a card wearing any "
        "OTHER of them the divergent state the guard is chosen on"
    )


@pytest.mark.parametrize("carried", ["same-spelling second row", "capitalised", "lowercase"])
def test_a_SECOND_row_of_one_key_is_accepted_onto_a_card_already_wearing_the_first(
    dupproj, carried
):
    """SERVER FACT TWO: `PUT /tasks/{id}/labels` refuses on the `label_id` ALONE, so a second row
    whose title normalises to the same key is ACCEPTED onto a card already wearing the first, and
    the card comes out wearing BOTH.

    THIS IS THE ONE THE GUARD IS CHOSEN ON. `test_duplicate_add_label_is_a_400` above pins the
    refusal for the SAME id; this pins its exact boundary, and the two together say the refusal is
    keyed on the id and on nothing else. It matters because it is the outcome the resolved-ID
    keying PRODUCED on a divergent board: it resolves to the row the card does not wear, sees a
    different id, sends this PUT — and the server takes it. Two rows for one concept on one card
    is therefore a state the server allows, not an artefact of a too-generous fake, and that is
    what let #1456 decide against the ID keying on a real answer instead of a fake one.

    `FakeAPI.add_label` refuses on `any(x["id"] == label_id ...)`, i.e. on the id alone, so it was
    already 1:1 here too — measured rather than assumed, as above. If a later Vikunja starts
    refusing by TITLE, this goes red and both the fake's mirror and the guard's grounds are known
    to be stale.
    """
    boss, pid, view, buckets, _wf = dupproj
    stem = "dupkey" + uuid.uuid4().hex[:6]
    lower_a, lower_b = boss.create_label(stem), boss.create_label(stem)
    upper = boss.create_label(stem.capitalize())
    first, second = {
        "same-spelling second row": (lower_b, lower_a),
        "capitalised": (upper, lower_a),
        "lowercase": (lower_a, upper),
    }[carried]

    task = boss.create_task(pid, f"two rows, {carried}")
    boss.move_task(pid, view["id"], buckets["Backlog"], task["id"])
    boss.add_label(task["id"], first["id"])

    boss.add_label(task["id"], second["id"])          # must be ACCEPTED, not a 400 code 8001

    on_card = {lb["id"] for lb in boss.get_task(task["id"])["labels"]}
    assert on_card == {first["id"], second["id"]}, (
        "the card wears BOTH rows: the refusal is keyed on the label_id, never on the title"
    )




def test_the_guard_skips_on_a_REAL_two_row_board_and_leaves_one_row(dupproj):
    """The unit pin's server-side counterpart: `_add_label` on a board a REAL 2.3.0 is holding two
    rows of one normalised key, with the card wearing the row the resolution does NOT return, is a
    SKIP — one row after, and no second row for one concept.

    Here rather than only in `tests/unit/test_workflow_duplicate_label.py` because the fake is
    exactly what #1216 refused to decide this on. The two facts above say the server BUILDS such a
    board and ACCEPTS the second row onto such a card; this says what the shipped guard does when
    it meets one, against that same server. Keyed on the resolved label ID it sends the PUT and the
    card comes out wearing BOTH — measured, this test goes RED under that mutation.

    THE FIXTURE HAS A STEP THAT LOOKS REDUNDANT AND IS NOT, and it is worth more than the test.
    The lowercase row is created FIRST so the resolution returns IT while the card wears the
    capitalised one — that is the whole divergence. But the SUBJECT here is `wf`, the agent's
    SCOPED-token `Workflow`, and `GET /labels` does not show that caller every row on the
    instance: measured next door in
    `test_a_label_on_no_readable_task_is_INVISIBLE_to_another_caller`, a row owned by the boss and
    used on no task the agent can read is simply absent from the agent's list, and the agent's
    `get_or_create_label` then resolves to the OTHER row — the one the card wears — and both guard
    forms skip, measuring nothing. So the lowercase row is parked on a second task in this shared
    project first, which is exactly what makes it visible. The premise assertions below check that
    rather than assume it.
    """
    boss, pid, view, buckets, wf = dupproj
    stem = "blk" + uuid.uuid4().hex[:6]
    lower = boss.create_label(stem)                     # created FIRST: this is what resolves
    upper = boss.create_label(stem.capitalize())        # and this is what the card wears
    parked = boss.create_task(pid, "parks the lowercase row where the agent can see it")
    boss.add_label(parked["id"], lower["id"])
    task = boss.create_task(pid, "real two-row board")
    boss.move_task(pid, view["id"], buckets["Build"], task["id"])
    boss.add_label(task["id"], upper["id"])

    card = wf.api.get_task(task["id"])
    assert wf.api.get_or_create_label(stem)["id"] == lower["id"], (
        "the premise: the AGENT resolves to the FIRST matching row it can see, the lowercase one "
        "— which is NOT the row on the card"
    )
    assert [lb["id"] for lb in card["labels"]] == [upper["id"]], (
        "and the second half of it: the card wears exactly the other row"
    )

    wf._add_label(card, stem)           # must SKIP against the server, not mint a second row

    assert _labels(boss, task["id"]) == [stem.capitalize()], (
        "one row, the one the human's spelling put there — not two rows for one concept. Keyed "
        "on the resolved label ID this reads ['Blk…', 'blk…'], which the server accepts"
    )


def test_a_label_on_no_readable_task_is_INVISIBLE_to_another_caller(dupproj):
    """`GET /labels` EXCLUDES a row that is neither the caller's own nor used on any task the
    caller can read — the half of `Workflow._add_label`'s docstring that two cards running marked
    UNPINNED, measured here with a control.

    WHY IT WAS UNPINNED AND WHY THAT MATTERED. `api.get_or_create_label`'s comment states this
    endpoint in the WIDENING direction — it surfaces every label used on a task the caller can read
    "(not just its own)" — and the EXCLUSION was an inference on top of it that nothing measured,
    while `_add_label`'s docstring leaned on that inference as a second route to a two-row board:
    a row the caller cannot see is minted again. The exclusion is now measured; the MINT still
    follows from `get_or_create_label`'s own two lines (no match -> `create_label`) rather than
    from a probe, and this test deliberately does not claim it.

    THE CONTROL IS THE TEST. One variable — whether the row also sits on a task in the SHARED
    project — with everything else held: the same row, the same board, the same two callers, in one
    run. Without it an empty result proves nothing (a row the agent cannot see is indistinguishable
    from a row that is not there), which is why the boss's view is asserted at both ends.
    """
    boss, pid, _view, _buckets, wf = dupproj
    stem = "vis" + uuid.uuid4().hex[:6]
    hidden_row = boss.create_label(stem)
    seen_row = boss.create_label(stem.capitalize())
    boss.add_label(boss.create_task(pid, "wears the visible row")["id"], seen_row["id"])
    private_pid = boss.create_project(f"private-{uuid.uuid4().hex[:8]}")["id"]
    boss.add_label(boss.create_task(private_pid, "wears the hidden row")["id"], hidden_row["id"])

    def rows_for(client):
        return [lb["id"] for lb in client.labels() if label_key(lb["title"]) == label_key(stem)]

    assert rows_for(boss) == [hidden_row["id"], seen_row["id"]], "the boss owns both, sees both"
    assert rows_for(wf.api) == [seen_row["id"]], (
        "the agent sees ONLY the row that is on a task it can read — this is the exclusion"
    )
    assert wf.api.get_or_create_label(stem)["id"] == seen_row["id"], (
        "so the two callers disagree about which row IS this label, at the same moment on the "
        "same board: the boss resolves to the hidden one, the agent to the visible one"
    )

    boss.add_label(boss.create_task(pid, "now parks the hidden row too")["id"], hidden_row["id"])

    assert rows_for(boss) == [hidden_row["id"], seen_row["id"]], "unchanged for the boss"
    assert rows_for(wf.api) == [hidden_row["id"], seen_row["id"]], (
        "THE CONTROL: the same row, now on a task the agent can read, APPEARS. So its absence "
        "above was visibility and not existence"
    )
    assert wf.api.get_or_create_label(stem)["id"] == hidden_row["id"], (
        "and the agent's resolution moves with it"
    )
