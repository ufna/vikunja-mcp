"""A filed card's COLUMN and its `[filed-by-agent]` marker — tracker #1167.

WHAT WAS OBSERVED, and why it needed a file rather than a fix. Four cards (VMCP-292 (1166),
VMCP-293 (1167), VMCP-294 (1168), VMCP-295 (1169)) were filed on 2026-08-19 with `queue` at its
default, every one of them placed in bucket 44 (Backlog) by the tool, and every one of them was
later found sitting in Queue — all four still carrying the Backlog variant of the marker, read
back off the cards. The orchestrator read that pair as impossible — `file_task` chooses the
destination and the marker text off the SAME flag, inside one function, with nothing between them
— and filed 1167 asking which of two things was true: something MOVED the card afterwards, or the
pairing is not atomic after all.

IT WAS THE FIRST, and the mover is not this package. The Vikunja container's own HTTP log settles
it: each card entered bucket 44 (Backlog) through `file_task`'s four-call signature, and entered
bucket 45 (Queue) minutes or hours later behind a request this package never issues —
`POST /api/v1/tasks/<id>/position`, 173-178 ms ahead of the bucket write, surrounded by JWT
refreshes, notification polling and avatar fetches. That is a kanban drag from the WEB FRONTEND,
which is exactly as far as the log reads and no further (#1172): the discriminator separates this
PACKAGE from a browser session holding a JWT, and it cannot separate a person's hand from browser
automation — something this repo runs routinely, `PLAYWRIGHT_MCP_ISOLATED=true` being committed
for that reason. A human triaging Backlog is the reading, and a good one, since triaging Backlog
is the entire purpose of Backlog; what is MEASURED is that the mover is outside this package,
which is the whole of what the diagnosis needs. Nothing was broken. The full measurement, with
the log lines and the discriminator, is in `docs/dossier/workflow.md`.

WHAT THIS FILE IS FOR is the half of that diagnosis which is a property of THIS code, so the next
observer does not spend an afternoon re-deriving it from the board. Two pins, one per hypothesis
the card raised:

* the marker DISTINGUISHES its two destinations, in every language — so a marker read off a card
  is evidence about where that card was FILED, and hypothesis "the two destinations share one
  wording" is closed rather than argued;
* no registered tool, pointed at a card in Backlog, moves it to Queue — so a Backlog card found
  in Queue was moved by something outside this package, which is the step the diagnosis turns on.

WHAT IT DOES NOT PIN, deliberately, and one of the three is a measured blind spot rather than a
choice. It says nothing about the marker being a DATED record rather than a statement of current
placement — that is prose, it lives in `server.file_task`'s docstring where the filing agent reads
it, and a test cannot check that a reader drew the right inference. It does not make the board's
history observable: this package still cannot tell who moved a card, and the only thing that could
was the server's log. And it is BLIND to a move that sits behind a guard which refuses from
Backlog — measured, see the second green round below — because a tool that never runs cannot be
caught leaving a card anywhere.

MUTATION SWEEP, run in its own clone (never in the author's worktree — a concurrent writer's
restore under your round is silent), selection this file alone so nothing else can stand in for
it, `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1 each round, `vikunja_mcp.__file__`
printed every round and pointing into the clone, rounds read by counting lines beginning
`FAILED ` rather than the first `N failed` in stdout, every round collecting the same 5 items as
the control. Control (opening) 0 failed, 0 errors, 5 collected. `cardtext.py`'s `filed_backlog`
row given the `filed_queue` wording, i.e. the two destinations sharing one marker -> 2 failed
(both languages). `workflow.file_task`'s `stage = "Queue" if queue else "Backlog"` flipped to a
constant `"Backlog"`, i.e. the queue opt-in silently ignored -> 2 failed. `return_task`'s move
retargeted from Backlog to Queue -> 1 failed. `decompose`'s PARENT move retargeted the same way
-> 1 failed. `review_task`'s Review-only gate widened to `("Review", "Backlog")` -> 2 failed. The
non-vacuity control's own subject changed (the ownerless bounce at the end of `review_task` sent
to Build instead of Queue) -> 1 failed, and that is the round proving the sweep can SEE a move
into Queue rather than asserting a state nothing could reach. A `self._move(task_id, "Queue")`
added to `call_human` ABOVE every one of its guards -> 1 failed. `return_task` dropped from
`_OTHER_ARGS` -> 2 failed, the roster guard naming it. Control (closing, all restored) 0 failed,
0 errors, 5 collected.

TWO ROUNDS CAME BACK GREEN, and they are the pin's REACH rather than its failure — recorded
because a reader who assumes otherwise will trust the sweep further than it goes, and against the
same opening control of 0 failed. (i) `return_task` made to move the card to Queue and then on to
Backlog -> 0 failed: the sweep reads the FINAL column, so a card walked THROUGH Queue and out
again is invisible to it. (ii) The same `call_human` mutation as above, but placed BELOW its
stage guard instead -> 0 failed. The pair is the sharp one: an identical line is caught at the
top of the method and invisible four lines down, because from Backlog `call_human` refuses before
reaching it. What that measures is the sweep's REACH, and the reach is small — of the 24 rows it
drives (12 pointable tools x 2 ownership states) 14 are refusals, 10 run, and only TWO reach a
`_move` at all: `decompose` and `return_task`, both in the assigned state. Those figures were
20 / 10 / 10 / 2 until #1179 added `handoff` and `transfer_task`; RE-MEASURED by the same
instrumentation rather than adjusted by arithmetic, and the two halves moved differently — the
run and `_move` counts did not change at all, because both new tools refuse from Backlog in both
ownership states (`handoff` on its Design/Build stage gate, `transfer_task` on the fail-fast
resolve of a target board this sweep's Workflow cannot reach, which is BEFORE its first write). Measured by
instrumenting `Workflow._move` across every row rather than read off the code. The eight that run
without moving are NOT "the reading tools", which is what this paragraph said until #1172: four of
them WRITE — `comment` twice posts a comment, `attach_file` twice uploads a file AND a journal
line — and four read (`get_task` twice, `download_attachment` twice). What they have in common is
not that they read but that not one of them touches a bucket. The figures were 12 / 8 / 2 until
the same card gave `download_attachment` a real attachment: two of those twelve refusals were this
file's own argument choice rather than a guard's behaviour, and the tool was never exercised at
all. So the honest claim is WHERE A BACKLOG CARD ENDS UP after one tool call from those two states
— not every path a card can take through Queue, and not the behaviour of a tool behind a guard
that refuses from Backlog outright.

#1172 CHANGED THIS SWEEP'S INPUT, so all ten rounds above were RE-RUN rather than assumed to
hold. Giving `download_attachment` a real attachment adds two rows that RUN and none that MOVE,
so no count should move — but "should" is the word this repo distrusts. Re-run in a clone with
the working tree committed inside it, same selection, `__pycache__` cleared and
`PYTHONDONTWRITEBYTECODE=1` per round, `-q` dropped, rounds read by counting lines beginning
`FAILED `: control (opening) 0 failed / 0 errors / 5 collected; marker rows 2 failed;
`file_task`'s stage constant 2 failed; `return_task` retarget 1 failed; `decompose` parent
retarget 1 failed; `review_task` gate widened 2 failed; ownerless bounce to Build 1 failed;
`call_human` `_move` above the guards 1 failed; `return_task` dropped from `_OTHER_ARGS` 2 failed;
both green rounds 0 failed; control (closing) 0 failed / 0 errors / 5 collected. Every figure
above reproduces to the digit.
"""
import inspect

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import server
from vikunja_mcp.config import LANGUAGES
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

# Everything besides `task_id` each pointable tool needs to REACH whatever move it makes. Most
# values are the cheapest that get past the argument validation standing in front of the board
# writes — but READ THE TWO EXCEPTIONS, because "cheapest" was this comment's whole claim until
# #1172 and it was not true of them. `attach_file` and `download_attachment` are given a REAL
# temp file and a REAL attachment, so their rows exercise the tool instead of its argument check.
# `download_attachment` used to be handed a made-up id and refuse on it: two of the sweep's
# refusals were then the test's own argument choice rather than any guard's behaviour, and the
# tool itself was never run at all. Measured by instrumenting `Workflow._move` over all 20 rows:
# with the fabricated id, 12 refusals / 8 run / exactly 2 reaching a `_move`; with a real
# attachment, 10 refusals / 10 run / still exactly 2 reaching a `_move`.
#
# NOT imported from test_done_is_human_only.py, which keeps a map of the same shape, and the
# reason is the one entry that matters here: that file passes `verdict="approve"`, while the
# verdict able to put a card in Queue is `needs_work`. Sharing the map would have made this sweep
# green on the single tool it most needs to exercise. The roster DERIVATION is shared in spirit
# (both read `server._DEFERRED_TOOLS` rather than a list) and the keys are checked against it
# below, so a new tool reddens here too instead of being skipped.
_OTHER_ARGS = {
    "claim": {},
    "advance": {"to": "build", "spec": "an approach"},
    "review_task": {"verdict": "needs_work", "report": "a report"},
    "call_human": {"question": "a question"},
    "return_task": {"reason": "a reason"},
    "decompose": {"subtasks": [{"title": "A"}, {"title": "B"}]},
    "get_task": {},
    "comment": {"text": "a note"},
    "attach_file": {"path": None},                    # filled per-call with a real temp file
    "download_attachment": {"attachment_id": None},   # filled per-call with a REAL attachment
    # `to` is a bare project id, not a configured sibling name: the sweep's Workflow carries no
    # registry, and an id only has to clear _resolve_sibling (positive, not our own) for the row
    # to reach the guard that actually decides. 999 is registered nowhere, which is what makes
    # transfer_task's row a fail-fast refusal at the target-board resolve — before any write.
    "handoff": {"to": 999, "title": "the other half"},
    "transfer_task": {"to": 999, "reason": "filed on the wrong board"},
}


def _pointable_tools():
    """The registered tools that can be AIMED AT A CARD, read from their signatures rather than
    from a list. `next_task` and `file_task` are the two that cannot: they take no task_id."""
    return {
        fn.__name__ for fn in server._DEFERRED_TOOLS
        if "task_id" in inspect.signature(fn).parameters
    }


def _marker(api, task_id):
    """The `[filed-by-agent]` line off a card's journal — the one comment `file_task` writes."""
    return next(c for c in api.comments_text(task_id) if c.startswith("[filed-by-agent]"))


def test_the_argument_map_covers_the_pointable_roster_exactly():
    """The self-check that makes the sweep fail-closed. A tool decorated with `@_mcp_tool` joins
    `server._DEFERRED_TOOLS` by itself; if it takes a `task_id` it becomes pointable, and it must
    then appear in `_OTHER_ARGS` — otherwise the sweep below would SKIP it and report green about
    a tool nobody classified. Skipping is the failure mode this whole file exists to prevent, one
    level up. Both directions are asserted, so a stale entry for a deleted tool is loud too."""
    pointable = _pointable_tools()
    missing = pointable - set(_OTHER_ARGS)
    assert not missing, (
        f"new tool(s) {sorted(missing)} can be pointed at a card but this file does not know how "
        f"to call them — add them to _OTHER_ARGS so the Backlog sweep below actually asks them"
    )
    stale = set(_OTHER_ARGS) - pointable
    assert not stale, (
        f"_OTHER_ARGS names tool(s) that are gone or no longer take a task_id: {sorted(stale)}"
    )
    for name in sorted(pointable):
        assert callable(getattr(Workflow, name, None)), name


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_filed_marker_names_which_destination_the_card_was_filed_into(language):
    """HYPOTHESIS 1, closed: "`queue=True` was used and the marker does not distinguish the two
    destinations". It does, in both languages — so a Backlog-variant marker really is evidence
    that the card was filed to Backlog, and reading one off a card is a legitimate step.

    Asserted as a PROPERTY rather than against the two literals: what a reader needs is that the
    two wordings differ and that the Queue one names its column, which stays true through a
    re-translation. Both languages are swept because the card that raised this carried the `ru`
    spelling, and a table with one column right and one wrong is exactly the shape #1165 warned
    about."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, language=language)

    filed = wf.file_task(title="a finding I discovered myself")["filed"]["id"]
    asked = wf.file_task(title="work a human asked for", queue=True)["filed"]["id"]

    assert api.stage_of(filed) == "Backlog"
    assert api.stage_of(asked) == "Queue"

    backlog_marker, queue_marker = _marker(api, filed), _marker(api, asked)
    assert backlog_marker != queue_marker, (
        f"[{language}] both destinations write the same marker, so a card's journal says nothing "
        f"about where it was filed — which is precisely what #1167 had to rule out by hand"
    )
    assert "Queue" in queue_marker, f"[{language}] the Queue marker does not name Queue"
    assert "Queue" not in backlog_marker, (
        f"[{language}] the Backlog marker names Queue: {backlog_marker!r}"
    )


def test_no_tool_pointed_at_a_backlog_card_moves_it_into_queue(tmp_path):
    """HYPOTHESIS 2, closed: "there is a path where the move and the marker disagree". Every
    registered tool that can be aimed at a card is aimed at one sitting in Backlog, and none of
    them may leave it in Queue. That is the step the #1167 diagnosis turns on — a Backlog card
    found in Queue was moved from OUTSIDE this package.

    WHAT THIS ADDS, said as a delta rather than as a first. Until #1172 this docstring claimed
    that "before this file nothing asked it", and that is measurably false: `test_workflow_gates.py`
    pre-exists, is untouched by #1167's commit, and already reddens on the same mutations.
    Measured on that file ALONE, in a clone, `__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1`
    per round, `-q` dropped, rounds read by counting lines beginning `FAILED `: control (opening)
    0 failed / 0 errors / 102 collected; `file_task`'s `stage = "Queue" if queue else "Backlog"`
    flipped to a constant -> 2 failed; `decompose`'s PARENT move retargeted Backlog->Queue -> 6
    failed; control (closing) 0 failed / 0 errors / 102 collected. It also pins the `en` Backlog
    marker literal outright. Three things are genuinely NEW here and none of them is "asking the
    question at all": (a) the BACKLOG question asked over a DERIVED roster. NEITHER HALF IS NEW
    ALONE, and #1172 got this clause wrong twice before arriving here — its first landing said
    the derivation was new, its first rework said the Backlog question was asked nowhere else,
    and both are false. The pointable-roster derivation
    pre-exists in `test_done_is_human_only.py`, whose `_pointable_tools()` reads the same
    `server._DEFERRED_TOOLS` by signature and asserts both directions against its own
    `_OTHER_ARGS` — the module comment above `_OTHER_ARGS` here says exactly that already, and
    the docstring contradicting it one screen below is how the second wrong version shipped. And
    `test_workflow_gates.py` derives `_VERDICT_POLICY`'s roster the same way, over ALL tools
    rather than the pointable ones. The Backlog question pre-exists too, in
    `test_the_per_stage_ownerless_exits_state_only_what_the_board_really_does`, which loops its
    HAND-WRITTEN 8-form `movers` over every stage but Queue, Backlog included, and asserts the
    only exit anywhere is `review_task(needs_work)` out of Review — ownerless only, which is what
    (c) below is about. What no other file does is put the two together. Measured under the same
    discipline in a clone taken for #1172's rework, with one `@_mcp_tool`-decorated
    `snooze_task(task_id, days=1)` added to `server.py`: on
    `tests/unit/test_workflow_gates.py`, control 0 failed / 0 errors / 102 collected -> 1 failed
    / 0 errors / 102 collected, and it is the DERIVED roster that catches it
    (`test_every_agent_tool_is_graded_for_what_it_does_to_a_stale_verdict`, whose assertion names
    `['snooze_task']`) while the hand-written `movers` test stays GREEN; on THIS file, control
    0 failed / 0 errors / 5 collected -> 2 failed / 0 errors / 5 collected. Read those two rows
    exactly, because they are NOT the new tool being swept: they are the self-check above and a
    `KeyError` on `_OTHER_ARGS` raised before the tool is ever called. What the derivation buys
    is that this file goes RED until a human classifies the tool, and sweeps it against Backlog
    once they do; (b) the `ru` column of the marker property, where the pre-existing test asserts
    only `en`, and `ru` is exactly the spelling the card observed;
    (c) the ownership dimension driven systematically over every tool rather than per-test.

    Run in BOTH ownership states, because most gates route on ownership first: unassigned (how
    `file_task` leaves a card) and assigned to the caller (how a human's triage leaves one). A
    sweep in the unassigned state alone would pass on refusals and never reach a single move.

    `decompose` is the near-miss that makes this non-trivial: pointed at a Backlog card it
    SUCCEEDS and really does create cards in Queue — its children. The claim is about the card
    that was pointed at, which it leaves in Backlog, and the assertion is written that way rather
    than as "nothing new appears in Queue".

    Exceptions are swallowed because a refusal is a perfectly good outcome here — but only
    `WorkflowError`, so a `TypeError` from a wrong entry in `_OTHER_ARGS` reddens instead of
    quietly making a tool's row vacuous."""
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nbytes")

    for name in sorted(_pointable_tools()):
        for assigned in (False, True):
            api = FakeAPI(buckets=STAGES)
            wf = Workflow(api, project_id=3)
            card_id = wf.file_task(title=f"a finding, then aimed at by {name}")["filed"]["id"]
            assert api.stage_of(card_id) == "Backlog"
            if assigned:
                api.add_assignee(card_id, api.me_user["id"])

            kwargs = dict(_OTHER_ARGS[name])
            if name == "attach_file":
                kwargs["path"] = str(shot)
            if name == "download_attachment":
                # a REAL attachment, so this row runs the tool rather than its argument check
                wf.attach_file(card_id, str(shot))
                kwargs["attachment_id"] = wf.get_task(card_id)["attachments"][0]["id"]
                assert api.stage_of(card_id) == "Backlog"
            try:
                getattr(wf, name)(card_id, **kwargs)
            except WorkflowError:
                pass

            assert api.stage_of(card_id) != "Queue", (
                f"{name} moved a Backlog card into Queue (assigned={assigned}). Queue is "
                f"claimable work; Backlog is the human's triage zone, and only the human takes a "
                f"card across that line — see #1167 and docs/dossier/workflow.md"
            )


def test_the_sweep_can_see_a_move_into_queue_when_there_is_one():
    """THE NON-VACUITY CONTROL, without which the sweep above is an assertion about a state
    nothing could reach anyway.

    `review_task(verdict='needs_work')` on an OWNERLESS card in Review is the one place in this
    package that walks an EXISTING card into Queue (#705: there is no implementer to hand it back
    to, so it reopens as free work). Driven here it must land in Queue — same FakeAPI, same
    `api.stage_of` reading the sweep uses. So when the sweep reports "nothing moved", that is a
    fact about the tools and not about a blind observation.

    Note what it also shows about the sweep's own subject: this very tool, aimed at the SAME
    card in Backlog rather than in Review, moves nothing. The stage is what decides."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)

    ownerless = api.add_task("somebody's work, under review, nobody's", "Review")
    wf.review_task(ownerless["id"], verdict="needs_work", report="needs another pass")
    assert api.stage_of(ownerless["id"]) == "Queue"

    parked = api.add_task("the same card, in Backlog instead", "Backlog")
    with pytest.raises(WorkflowError):
        wf.review_task(parked["id"], verdict="needs_work", report="needs another pass")
    assert api.stage_of(parked["id"]) == "Backlog"
