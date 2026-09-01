"""A card wearing TWO board rows that normalise to ONE label, and the helper that took off only
the first of them (#1457).

THE DEFECT IN ONE LINE: `Workflow._remove_label` resolved the title the way the SERVER does
(`api.label_key`, #1256) but then picked the FIRST match with `next(...)` and sent ONE DELETE, so
on a card carrying both `reviewed` and `Reviewed` one row survived every clearing path in the
package.

WHAT THE SURVIVOR COSTS, stated narrowly because the wide version is FALSE and this card's own
second independent pass caught it. NO `_has_label` GATE READS `reviewed` AT ALL — censused on this
tree, the reads name `epic` (8), `bug` (3), `blocked` (1) and `epic-ready` (1) and nothing else —
and `_clear_verdict_labels`' own docstring says so outright: the review offering keys on
`[worklog]`/`[review]` comment freshness, so a stale `reviewed` would NOT suppress a re-review.
What it is, in that docstring's own words, is `ложь на доске` — the badge a HUMAN reads off the
card before moving it to Done, which is the whole reason `_clear_verdict_labels` exists. The
verdict pair is also MUTUALLY EXCLUSIVE and a survivor breaks that: a `needs_work` leaves the card
wearing both at once. The one family where a real gate does act is `blocked`, which `transfer_task`
clears through this same helper — `next_task` withholds a `blocked`-labelled card from the
offering — so the two are not symmetric and this module keeps them apart.

WHERE A TWO-ROW BOARD COMES FROM, and there are TWO of them. The distinction is written out rather
than collapsed because the dossier's #1256 section already had to retract the collapsed version
once. Two SPELLINGS (`reviewed` + `Reviewed`) come from a HUMAN typing one in the web UI; this
package never writes them, since its single production `get_or_create_label` call site is inside
`_add_label` and every caller passes a lowercase `LABEL_*` constant. The SAME spelling twice
(`reviewed` + `reviewed`) is the board the package reaches UNAIDED: `get_or_create_label` is
read-`labels()`-then-`create_label` with nothing atomic between the two, so at `wip_limit > 1` two
agents adding the same absent label both miss and both create — and because both pass that same
constant, the two rows they mint are spelled ALIKE. Both are two-row boards of one normalised key,
the defect leaked on both, and the consequence pin drives both.

REPRODUCED BEFORE ANYTHING CHANGED, on a live `Workflow` over `FakeAPI` with agent tools only,
every row minted with `api.create_label`:

  * card in Build wearing `reviewed` + `Reviewed`, `advance(to='review')`:
    before `['reviewed', 'Reviewed']` -> after `['Reviewed']`; CONTROL, one row: `[]`
  * three rows (`reviewed`/`Reviewed`/`REVIEWED `): after `['Reviewed', 'REVIEWED ']` — one
    DELETE leaves N-1
  * `review_task(verdict='needs_work')` on the two-row card: after
    `['Reviewed', 'review-failed']`, i.e. BOTH mutually-exclusive verdict labels on one card
  * `transfer_task` on a card wearing `blocked` + `Blocked`: after `['Blocked']` — a stale block
    riding onto the neighbour's board

WHY THE LOOP IS NOT A NEW ROUTE INTO THE MEASURED 403, which is the question the card left open.
Real 2.3.0 answers `DELETE /tasks/{id}/labels/{label_id}` with 403 `Forbidden` when the label is
NOT on the task (`FakeAPI.remove_label` is an idempotent no-op instead — a deliberate divergence
whose BEHAVIOUR is pinned by `test_fake_remove_label_idempotent_and_mirrors_client`, while the
rationale for keeping it is prose in `FakeAPI._read_task` and pinned by nothing). Every DELETE the
loop sends names a DISTINCT `label_id` that WAS on the caller's snapshot: two rows are two
different board rows, and the ADD endpoint refuses a repeat of ONE id (400 code 8001, measured
below and mirrored by the fake), so a snapshot the SERVER filled does not carry one id twice.
That is the belt. The brace is the guard, and the guard is what actually carries the safety,
because the loop iterates a CLIENT-SIDE dict and not server storage: the match list is
DE-DUPLICATED BY ID, and `test_one_label_id_is_never_deleted_twice` hand-builds the snapshot no
write path of ours produces, because "it cannot happen" is a claim and not a guard.

TWO THINGS IN THAT PARAGRAPH ARE UNPINNED AND THE SWEEP TABLE SAYS SO — written here too, so the
next reader does not delete either on the strength of a green round. The match list is
MATERIALISED before the first DELETE (a generator over `task["labels"]` is the classic way to
reintroduce exactly this bug), and `x["id"]` is read only on a row that ALREADY MATCHED — the
place `next(...)` read it, so this one is a preservation and not a new guard. Its one measured
consequence needs a snapshot no server produces: a MATCHING row with no `id` key raises `KeyError`
out of the match loop before any DELETE, so on a two-row card one malformed row now aborts the
clearing where the old code would have cleared the other. `KeyError` is not in `server.py`'s
`_tool` catch list, so it would escape as an unhandled tool error rather than `{"error": ...}`.

BOTH SERVER ANSWERS ABOVE WERE RE-MEASURED FOR THIS CARD on a throwaway real 2.3.0 rather than
inherited from #1211/#1216: a DELETE naming a link that is gone answers 403 `Forbidden` (and so
does one for a label never attached, and one for a `label_id` that does not exist — 404 is not any
of those three on this server), while a PUT repeating an id the card already carries answers
400 code 8001. The same container also builds the two-row card this module is about, so the state
is not a fake's artefact. The table is in `docs/dossier/workflow.md`, and since a hand measurement
decays silently it is also pinned, in `tests/integration/test_remove_label_absent.py`.

AND A REFUSED DELETE MUST NOT SHIELD THE OTHER ROW. Every match is attempted; the FIRST
`VikunjaError` is remembered and re-raised after the loop. On a one-row card — every ordinary
board — that is the old behaviour unchanged (one DELETE, the same exception propagating), so no
call site moved: FIVE `_remove_label` calls in THREE methods — `_clear_verdict_labels` x2,
`review_task`'s two branches, and `transfer_task`'s loop over
`blocked`/`reviewed`/`review-failed`/`epic-ready`. It is NOT swallowed, and the reason is that a
403 on this endpoint is not always the benign absent-link one: a task in a project the token
cannot see answers 403 on a DELETE that would otherwise have succeeded (`FakeAPI._read_task`'s
probe table), so swallowing would fail OPEN and silently — the exact mode #1256 is about. A token
merely missing `tasks_labels: delete` answers 401 there rather than 403 — the same table records
that the endpoint was never reached at all — which is a different status and equally not something
to swallow. Sniffing the body to swallow only a "benign" 403 is the shape #1216 rejected for
`add_label`'s 400, on the ground that a sniff swallows a genuinely different error of the same
status.

The sweep table lives in `test_the_sweep_is_recorded`'s docstring at the bottom, so every round
sits in the same paragraph as the control it is a delta against.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError, label_key
from vikunja_mcp.workflow import (
    LABEL_BLOCKED,
    LABEL_REVIEWED,
    LABEL_REVIEW_FAILED,
    STAGES,
    Workflow,
)


class RecordingAPI(FakeAPI):
    """`FakeAPI` that records every DELETE it is asked for and can refuse chosen label ids with
    the 403 real 2.3.0 answers for a link that is not there.

    The refusal is injected HERE, at the `api.remove_label` boundary, rather than by teaching
    `FakeAPI` a new refusal: on the path `_remove_label` actually walks, the server does NOT 403
    (every id it names was on the snapshot), so mirroring one into the fake would make the fake
    LESS faithful, not more. That test pins the divergence's BEHAVIOUR and not the reasoning for
    keeping it (its own docstring warns that its name promises more than it checks); the reasoning
    is prose in `FakeAPI._read_task`. What the server really answers here is pinned instead by
    `tests/integration/test_remove_label_absent.py`, against a real 2.3.0."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deletes: list[tuple[int, int]] = []
        self.refuse: dict[int, str] = {}

    def remove_label(self, task_id, label_id):
        self.deletes.append((task_id, label_id))
        if label_id in self.refuse:
            raise VikunjaError(403, self.refuse[label_id])
        return super().remove_label(task_id, label_id)


def _titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]


def _stand(rows, stage="Build", assign=True):
    """A card wearing every row in `rows`, each MINTED SEPARATELY with `create_label`.

    `create_label` and not `get_or_create_label`: the latter resolves `Reviewed` to the existing
    `reviewed` and would hand back one row, which is precisely why this package's ordinary write
    path cannot build this board and a human's hand (or the `wip_limit > 1` race) can."""
    api = RecordingAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("job", stage, assignee=api.me_user if assign else None)
    for title in rows:
        api.add_label(t["id"], api.create_label(title)["id"])
    return api, wf, t


def test_every_row_of_one_key_comes_off_not_just_the_first():
    """THE DEFECT ITSELF, at two and at three rows, each against the one-row control in the same
    test — because "nothing left" means nothing unless the single-row case is shown to have been
    working all along, which is what made this a residual rather than a regression.

    BOTH two-row boards are driven, and they have different provenance (the module docstring
    separates them): two SPELLINGS is a human typing one in the web UI, the SAME spelling twice is
    the `wip_limit > 1` race in `get_or_create_label` — the only one this package reaches unaided,
    and the one nothing exercised until this line.

    Counted through `label_key`, not by exact title: counting exact titles would read a surviving
    `Reviewed` as an absence and pass."""
    for rows in (
        [LABEL_REVIEWED],                                  # the control: this always worked
        [LABEL_REVIEWED, "Reviewed"],                      # two SPELLINGS: a human's hand
        [LABEL_REVIEWED, LABEL_REVIEWED],                  # the SAME spelling twice: the race
        [LABEL_REVIEWED, "Reviewed", "REVIEWED "],
    ):
        api, wf, t = _stand(rows)
        assert len(_titles(api, t["id"])) == len(rows), "the stand did not build the board"

        wf.advance(t["id"], to="review", worklog="w", evidence="e")

        left = [label_key(x) for x in _titles(api, t["id"])]
        assert LABEL_REVIEWED not in left, (
            f"a stale APPROVE survived advance(to='review') on the board {rows!r}: {left!r}"
        )


def test_a_needs_work_verdict_leaves_no_approve_badge_beside_it():
    """The two verdict labels are MUTUALLY EXCLUSIVE, and on a two-row board they stopped being
    so: `review_task(verdict='needs_work')` added `review-failed` and took off one `reviewed`
    row, leaving `['Reviewed', 'review-failed']` — a card that reads as approved AND rejected at
    once to a human, and as approved to every `_has_label` gate."""
    api, wf, t = _stand([LABEL_REVIEWED, "Reviewed"], stage="Review")

    wf.review_task(t["id"], verdict="needs_work", report="not yet")

    left = [label_key(x) for x in _titles(api, t["id"])]
    assert LABEL_REVIEW_FAILED in left, "the verdict itself must still land"
    assert LABEL_REVIEWED not in left, f"the card carries both verdicts at once: {left!r}"


def test_a_transferred_card_carries_no_stale_block_onto_the_neighbours_board():
    """`transfer_task` clears `blocked`/`reviewed`/`review-failed`/`epic-ready` through this same
    helper, so the leak crossed a project boundary: the neighbour's human triages a card wearing
    a `Blocked` that was about a situation on a board they cannot see."""
    api, wf, t = _stand([LABEL_BLOCKED, "Blocked"])
    neighbour = api.add_project("backend", buckets=STAGES)
    wf = Workflow(api, project_id=3, siblings={"backend": neighbour["id"]})

    wf.transfer_task(t["id"], to="backend", reason="belongs there")

    left = [label_key(x) for x in _titles(api, t["id"])]
    assert LABEL_BLOCKED not in left, f"a stale block rode onto the neighbour's board: {left!r}"


def test_an_ordinary_one_row_board_still_costs_exactly_one_delete():
    """The contract at every call site is unchanged on the board every real project has. Asserted
    on the REQUESTS and not on the resulting labels, because the resulting labels look the same
    whether one DELETE was sent or five."""
    api, wf, t = _stand([LABEL_REVIEWED])
    row = api.tasks[t["id"]]["labels"][0]["id"]

    wf.advance(t["id"], to="review", worklog="w", evidence="e")

    assert api.deletes == [(t["id"], row)], (
        f"one row must cost exactly one DELETE, not {api.deletes!r}"
    )


def test_one_label_id_is_never_deleted_twice():
    """THE DE-DUPLICATION, which is the whole of this change's answer to the 403 question. The
    loop's second DELETE is safe only because it names a DIFFERENT row; the same id sent twice is
    exactly the "link that is not there" real 2.3.0 answers 403 for, so the match list is keyed
    by id.

    The snapshot is handed in directly rather than built through `add_label`, which refuses a
    duplicate (400 code 8001) — the point of the guard is that a MALFORMED or racing snapshot
    cannot turn a benign clearing into an error, and such a snapshot is by construction one no
    write path of ours produces."""
    api, wf, t = _stand([LABEL_REVIEWED])
    row = dict(api.tasks[t["id"]]["labels"][0])
    snapshot = {"id": t["id"], "labels": [row, dict(row), dict(row)]}

    wf._remove_label(snapshot, LABEL_REVIEWED)

    assert api.deletes == [(t["id"], row["id"])], (
        f"the same label id must be DELETEd once, not {len(api.deletes)} times: {api.deletes!r}"
    )


def test_a_refused_delete_does_not_shield_the_other_row():
    """A failure on one row must not leave the other row's stale badge behind — the naive loop's
    real hazard, and the reason the DELETEs are not simply chained.

    Both directions are driven, and their strengths are NOT equal — measured per direction rather
    than assumed, because an earlier draft of this docstring asserted a symmetry that is not there.
    Refusing the FIRST row catches BOTH mutants (a loop that lets the refusal propagate at once
    never reaches the second row; a loop that swallows reports success); refusing the SECOND
    catches only the swallowing one and adds nothing the first direction misses. It is kept for
    what it asserts about the BOARD — that the row which could be removed WAS, whichever one
    failed — not as a second kill. Either way the error still reaches the caller: this is
    containment, not tolerance."""
    for refused_index, survivor in ((0, LABEL_REVIEWED), (1, "Reviewed")):
        api, wf, t = _stand([LABEL_REVIEWED, "Reviewed"])
        rows = [lb["id"] for lb in api.tasks[t["id"]]["labels"]]
        api.refuse = {rows[refused_index]: "Forbidden"}
        snapshot = api.get_task(t["id"])

        with pytest.raises(VikunjaError) as exc:
            wf._remove_label(snapshot, LABEL_REVIEWED)

        assert exc.value.status == 403
        assert api.deletes == [(t["id"], rows[0]), (t["id"], rows[1])], (
            f"every matching row must be attempted, not {api.deletes!r}"
        )
        assert _titles(api, t["id"]) == [survivor], (
            f"only the REFUSED row may survive; the board reads {_titles(api, t['id'])!r}"
        )


def test_the_first_refusal_is_the_one_raised():
    """When more than one row is refused the caller gets the FIRST failure, not the last: it is
    the one that describes the original cause, and it makes the raised value deterministic
    instead of "whichever row the board happened to list last"."""
    api, wf, t = _stand([LABEL_REVIEWED, "Reviewed"])
    rows = [lb["id"] for lb in api.tasks[t["id"]]["labels"]]
    api.refuse = {rows[0]: "first", rows[1]: "second"}
    snapshot = api.get_task(t["id"])

    with pytest.raises(VikunjaError) as exc:
        wf._remove_label(snapshot, LABEL_REVIEWED)

    assert exc.value.message == "first", f"the first failure must be raised, got {exc.value!r}"
    assert len(api.deletes) == 2, "and both rows are still attempted"


def test_a_card_with_no_matching_row_sends_no_delete():
    """The no-op half of the contract: `_clear_verdict_labels` runs on every forward transition,
    and on a card carrying labels that are NOT verdicts it must send nothing — a loop that DELETEd
    every row it iterated would answer 403 on every ordinary advance, and would also strip labels
    nobody asked it to touch.

    The stand carries a NON-matching label rather than none at all, which is what the name
    promises: with an empty `labels` list no loop over it can send anything, so an empty stand
    cannot distinguish "matched nothing" from "iterated nothing"."""
    api, wf, t = _stand([LABEL_BLOCKED])

    wf.advance(t["id"], to="review", worklog="w", evidence="e")

    assert api.deletes == [], f"a card with no verdict label must cost no DELETE: {api.deletes!r}"
    assert _titles(api, t["id"]) == [LABEL_BLOCKED], "and the non-matching row is left alone"


def test_the_sweep_is_recorded():
    """The mutation sweep for this module. Selection every round: this file (9) +
    `test_workflow_label_variants.py` (21) + `test_workflow_duplicate_label.py` (13) +
    `test_workflow_gates.py` (102), run in a `git clone --no-hardlinks` of the worktree with
    `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1` per round, `vikunja_mcp.__file__`
    printed each round and naming the CLONE, `-q` dropped so `collected` prints, rounds read by
    COUNTING lines beginning `FAILED ` with lines beginning `ERROR ` counted separately, and
    `collected` cross-checked against the control's. **Control ran FIRST and LAST, both 0 failed /
    0 errors / 145 collected**, and every round below reports that same 145 collected and 0
    errors — so each figure is a delta against 0, not against noise. The per-file counts are
    written out above because they sum to the 145: a mistyped path selects nothing and prints a
    pass. The whole table was RE-MEASURED after the last rebase, on the tree this commit lands:
    VMCP-316 (1456) landed two tests into `test_workflow_duplicate_label.py` mid-round, which moved
    the control from 143 to 145 and left every kill count unchanged.

      * restore `next(...)`, i.e. take only the FIRST matching row — the defect itself ->
        **5 failed** (control 0): the three multi-row consequence pins
        (`..._comes_off_not_just_the_first`, `..._no_approve_badge_beside_it`,
        `..._no_stale_block_onto_the_neighbours_board`) and both refusal pins
        (`..._does_not_shield_the_other_row`, `..._first_refusal_is_the_one_raised`). The
        one-row pin, the no-matching-row pin and the de-duplication pin stay GREEN, and that is
        the point of writing them separately: `next(...)` satisfies all three, so a suite holding
        only them would call this defect fixed
      * drop the de-duplication, i.e. `if x["id"] not in ids:` deleted so every match is appended
        -> **1 failed** (control 0): `test_one_label_id_is_never_deleted_twice`, the only round in
        the table that sees it — which is why that pin hands the helper a snapshot directly
        instead of trying to build one through a write path that refuses to
      * drop the `try`/`except` and let the first refusal propagate at once -> **2 failed**
        (control 0): both refusal pins. The multi-row consequence pins stay green, which is the
        honest shape — none of them refuses a DELETE
      * swallow instead of re-raising: delete the `if failed:` / `raise failed[0]` pair (the
        `raise` alone is a `SyntaxError`, so the round removes both lines) -> **2 failed**
        (control 0): the same two, this time on the `pytest.raises` rather than on the DELETE
        list. The direction the round above cannot see — a loop that finishes the work and then
        reports success it did not have
      * `raise failed[0]` -> `raise failed[-1]` -> **1 failed** (control 0):
        `test_the_first_refusal_is_the_one_raised` alone, which is exactly the claim it exists for
      * materialise nothing — iterate `task["labels"]` lazily inside the DELETE loop,
        de-duplication and containment both kept -> **0 failed** (control 0). Recorded rather than
        omitted, because the 0 is a property of THIS stand and not of the guard:
        `FakeAPI.remove_label` REBINDS `t["labels"]` to a new list instead of mutating in place,
        and the production client never touches the caller's dict at all, so nothing within reach
        mutates the list under the loop. The materialisation is defence against a future in-place
        mutator and it is UNPINNED — said here so the next reader does not delete it on the
        strength of a green round
      * read `x.get("id")` for EVERY row instead of `x["id"]` on a row that already matched ->
        **0 failed** (control 0). The second UNPINNED property, recorded for the same reason and
        found by this card's second independent pass rather than volunteered. It is a preservation
        of where `next(...)` read the id, and what it preserves shows only on a snapshot no server
        produces: under `.get` a matching row with no `id` sends `DELETE .../labels/None`, under
        `["id"]` it raises `KeyError` before any DELETE
      * drop the MATCH condition, so the loop DELETEs every row on the card -> **19 failed**
        (control 0), including `test_a_card_with_no_matching_row_sends_no_delete`. That pin used
        to build its stand with NO labels at all and could not see this round at all — an empty
        `labels` list makes any loop over it silent, so the stand could not tell "matched nothing"
        from "iterated nothing". It now carries a non-matching row, which is what its name always
        promised. The other 18 are the label gates in the three neighbouring files reacting to
        their labels being stripped
      * compare titles BYTE-EXACTLY in `_remove_label` only, leaving `_has_label` resolving ->
        **9 failed** (control 0): the five multi-row/refusal pins plus four in
        `test_workflow_label_variants.py` —
        `test_a_variant_verdict_badge_is_cleared_on_the_way_back_into_review` at its three
        spellings and `test_a_label_title_is_read_only_through_label_key`. Those four are the same
        four #1256's own table records for this comparison on its own five-file selection, which
        is the cross-check that this module reads through the same `api.label_key` the rest of the
        package does — the loop changes WHICH ROWS are taken off, never what counts as a match
    """
