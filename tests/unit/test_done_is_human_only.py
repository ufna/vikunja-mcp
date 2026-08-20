"""human-only Done, asked of EVERY registered tool at once — tracker #662.

WHAT WAS BROKEN WITHOUT THIS FILE, and it was not a bug: a sweep of all 12 tools against a card
in Done came back clean. What was missing is that nothing ASKED. Human-only Done was an
ENUMERATION — four tools refused because Done is not their starting stage, two carried a personal
`if stage == "Done"` (#626 `return_task`, #649 `decompose`) — and every pin checked its own tool.
So the next mutating tool that moved a card without looking at its stage reopened the hole
silently, which is exactly what `decompose` had done once already and what #649 filed this card
to settle. #662 closed it in two moves a human weighed and chose together: the RULE moved into
`_find_task` as one fail-closed guard, and this file makes a new tool ask the question out loud.

WHY BOTH HALVES ARE PINNED HERE, and why the guard could not ship without them. #649 rejected a
bare guard in `_find_task` on the grounds that it would make an accepted card UNREADABLE — a
regression worse than the hole. That objection is correct and the `allow_done=True` opt-ins are
the whole of what answers it. But it was MEASURED (on #662, before this file existed) that the
property "an accepted card stays readable" was pinned by NOTHING: making the guard unconditional
broke get_task, comment, attach_file and download_attachment all four, and the entire suite
stayed GREEN. Landing the guard alone would therefore have traded a documented objection for an
undefended invariant. So the two halves are asserted together, in one place, of one tool list.

WHERE THE TOOL LIST COMES FROM, because "enumerate the mutating tools" was this card's own open
question and the answer turned out to be mechanical. `server._DEFERRED_TOOLS` already exists for
an unrelated reason (the MCP SDK is imported lazily, so `@_mcp_tool` collects and `_server()`
registers) and a new tool joins it BY DECORATION, with nothing to remember. Whether a tool can be
POINTED AT A CARD is read from its SIGNATURE — a `task_id` parameter — not from a list. What is
left by hand is one set of four READING names and one map of the other arguments each call needs,
both right here where they are visible, and both guarded: the map's keys must equal the pointable
set exactly, so a new pointable tool FAILS this file until its author decides which half it is in.
That is the fail-closed shape the guard has, applied to the test that defends it.

WHAT THIS FILE DOES NOT DO. It does not make the hole inexpressible — the guard can still be
deleted, and a reading tool can still opt in wrongly. It makes both LOUD. And it says nothing
about stages other than Done: `decompose` turned out to have TWO stage holes (Done #649, Review
#663), and a boolean cannot express "the set of stages THIS tool may start from", which is the
wider shape #662's own follow-up comment describes and which nobody has been asked to build.

MUTATION SWEEP, selection this file plus tests/unit/test_workflow_gates.py, caches cleared and
PYTHONDONTWRITEBYTECODE=1, every round collecting the same 107 items as the control, read by
counting `FAILED `/`ERROR ` lines rather than the first `N failed` in stdout: control 0 failed, 0
errors, and a second control after the last restore 0 failed. Guard made UNCONDITIONAL, i.e. the
regression #649 named -> 1 failed, the reading half here, which before this file was the round
that stayed GREEN. Done branch deleted outright, back to the enumeration -> 7 failed. Shared
message loses its `file_task` token -> 5 failed, including both #626 and #649 pins, which is what
makes their token asserts a CONSTRUCTION rather than luck. Shared message loses `Done` from its
OPENING -> 1 failed only, and that is an honest bound rather than a good number: the word survives
elsewhere in the text, so the token pins do not notice, and what catches it is the contrast test.
One read opt-in removed (`comment`) -> 1 failed, naming it. Guard widened to Your Call as well ->
7 failed. A personal Done gate put back in `return_task`, i.e. the dead code restored -> 1 failed,
the AST test, which is the only thing that can see a difference behaviour cannot.
"""
import inspect

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import server
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

# The ONE hand-maintained list, and the price of the guard's fail-closed default: a READING tool
# has to declare itself. Getting this wrong is loud in both directions — drop a name and the tool
# starts refusing an accepted card (caught below), add one and a mutating tool walks a Done card
# out (also caught below).
_READING_TOOLS = frozenset({"get_task", "comment", "attach_file", "download_attachment"})

# Everything besides `task_id` each pointable tool needs in order to REACH its Done decision.
# Values are the cheapest that get past the argument validation standing in front of the stage
# check — this file is about what happens AT Done, not about those validations.
# The keys are checked against the mechanically-derived pointable set below, so this map cannot
# quietly fall behind the tool roster.
_OTHER_ARGS = {
    "claim": {},
    "advance": {"to": "build", "spec": "an approach"},
    "review_task": {"verdict": "approve", "report": "a report"},
    "call_human": {"question": "a question"},
    "return_task": {"reason": "a reason"},
    "decompose": {"subtasks": [{"title": "A"}, {"title": "B"}]},
    "get_task": {},
    "comment": {"text": "a note"},
    "attach_file": {"path": None},              # filled per-call with a real temp file
    "download_attachment": {"attachment_id": None},   # filled per-call with a real attachment
    # `to` is a bare project id rather than a configured sibling name deliberately: this
    # Workflow has no registry, and an id only has to survive _resolve_sibling (positive, not
    # our own) to reach the Done decision, which _find_task makes BEFORE the target board is
    # ever touched. Both are mutating, so neither belongs in _READING_TOOLS.
    "handoff": {"to": 999, "title": "the other half"},
    "transfer_task": {"to": 999, "reason": "filed on the wrong board"},
}


def _pointable_tools():
    """The registered tools that can be AIMED AT A CARD, read from their signatures rather than
    from a list. `next_task` and `file_task` are the two that cannot: they take no task_id."""
    out = set()
    for fn in server._DEFERRED_TOOLS:
        if "task_id" in inspect.signature(fn).parameters:
            out.add(fn.__name__)
    return out


def _accepted_card(api, wf, title):
    """A card in Done by the ONLY route that puts one there: driven the normal way to Review,
    approved, and then moved by a HUMAN — no tool can perform that last step, which is the whole
    rule under test. Returns the task dict."""
    t = api.add_task(title, "Queue")
    wf.claim(t["id"])
    wf.advance(t["id"], to="build", spec="approach")
    wf.advance(t["id"], to="review", worklog="did it", evidence="abc123")
    wf.review_task(t["id"], verdict="approve", report="looks right")
    api.task_bucket[t["id"]] = api.bucket_id("Done")     # the human's move
    assert api.stage_of(t["id"]) == "Done"
    return t


def test_the_pointable_tool_roster_is_derived_and_the_argument_map_covers_it_exactly():
    """The self-check that makes the rest fail-closed. A new tool decorated with `@_mcp_tool`
    joins `_DEFERRED_TOOLS` by itself; if it takes a `task_id` it lands in the pointable set, and
    then it must appear in `_OTHER_ARGS` — otherwise this file would SKIP it and report green
    about a tool nobody classified. Skipping is the failure mode the card was filed about, one
    level up.

    Both directions are asserted: a pointable tool missing from the map, and a stale map entry for
    a tool that no longer exists. The two tools that take no task_id are named positively so that
    the split stays a measurement rather than an assumption."""
    pointable = _pointable_tools()
    registered = {fn.__name__ for fn in server._DEFERRED_TOOLS}

    assert pointable <= registered
    missing = pointable - set(_OTHER_ARGS)
    assert not missing, (
        f"new tool(s) {sorted(missing)} can be pointed at a card but this file does not know how "
        f"to call them — add them to _OTHER_ARGS and decide whether they belong in _READING_TOOLS"
    )
    stale = set(_OTHER_ARGS) - pointable
    assert not stale, (
        f"_OTHER_ARGS names tool(s) that are gone or no longer take a task_id: {stale}"
    )
    assert _READING_TOOLS <= pointable, sorted(_READING_TOOLS - pointable)
    assert registered - pointable == {"next_task", "file_task"}, sorted(registered - pointable)

    # every pointable tool is also a Workflow method of the same name — that identity is what
    # lets this file drive the real gate instead of a mock of it
    for name in pointable:
        assert callable(getattr(Workflow, name, None)), name


def test_every_mutating_tool_refuses_an_accepted_card_and_moves_nothing(tmp_path):
    """HALF ONE — the rule. Each pointable tool that is not a reader is aimed at a card a human
    accepted, and must refuse; the card must still be in Done afterwards, because a guard that
    fires after the side effects is not a guard (which is why `_find_task` and not `_move`).

    Driven by NAME off the derived roster, so this covers a tool nobody has written yet. Delete
    the Done branch from `_find_task` and this goes RED on every mutating tool at once."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    mutating = sorted(_pointable_tools() - _READING_TOOLS)
    assert mutating, "the roster derived nothing — the split is broken, not the rule"

    for name in mutating:
        card = _accepted_card(api, wf, f"accepted, then aimed at by {name}")
        labels_before = [lb["title"] for lb in api.tasks[card["id"]]["labels"]]
        assignees_before = [a["id"] for a in api.tasks[card["id"]]["assignees"]]
        with pytest.raises(WorkflowError) as exc:
            getattr(wf, name)(card["id"], **_OTHER_ARGS[name])
        msg = str(exc.value)
        assert "Done" in msg and "file_task" in msg, f"{name}: {msg}"
        # ownership is never the answer for a Done card — the stage check has to run FIRST, or a
        # card belonging to someone else reads "claim it first", advice nobody can act on here
        assert "not assigned to you" not in msg, f"{name} answered about ownership: {msg}"
        assert api.stage_of(card["id"]) == "Done", f"{name} moved an accepted card"
        assert [lb["title"] for lb in api.tasks[card["id"]]["labels"]] == labels_before, name
        assert [a["id"] for a in api.tasks[card["id"]]["assignees"]] == assignees_before, name


def test_every_reading_tool_still_works_on_an_accepted_card(tmp_path):
    """HALF TWO — the safety catch, and the half that was pinned by NOTHING before this file.

    Measured on #662: make the guard unconditional (drop `and not allow_done`) and all four of
    these break, while the whole suite stays green. That is the regression #649 called worse than
    the hole it would close, so the objection is now defended by a test rather than by a comment.

    Real calls, not smoke: the file really uploads, the dossier really reads it back, and the
    download really returns bytes — so an `allow_done=True` that reaches the guard but breaks the
    call itself does not pass. Remove any one of the four opt-ins and this goes RED naming it."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    card = _accepted_card(api, wf, "accepted work a human may still want to read")

    assert wf.get_task(card["id"])["stage"] == "Done"
    assert wf.comment(card["id"], "a note on accepted work")["commented"] == card["id"]

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
    uploaded = wf.attach_file(card["id"], str(shot), note="what it shows")
    att_id = wf.get_task(card["id"])["attachments"][0]["id"]
    assert uploaded and att_id is not None

    got = wf.download_attachment(card["id"], att_id)
    assert got["path"].endswith("shot.png")

    assert api.stage_of(card["id"]) == "Done"
    # and the READERS are exactly the tools that may do this: the split above is not decorative
    assert _READING_TOOLS == frozenset(
        {"get_task", "comment", "attach_file", "download_attachment"}
    )


def test_the_guard_leaves_every_other_stage_alone(tmp_path):
    """The control the two halves above cannot supply between them: a guard that refused
    EVERYWHERE would satisfy half one and a guard that refused NOWHERE would satisfy half two,
    and neither test alone notices. So drive one ordinary tool from every non-Done stage and
    require that whatever it answers, it is not the Done refusal.

    Not "must succeed": most of these stages refuse `advance(to='build')` for their own reasons
    (wrong from_stage, no assignee). The claim is narrower and is the one that matters — the Done
    rule fires from Done and from nowhere else. Widen the guard to `stage != "Queue"` or drop the
    stage test entirely and this goes RED."""
    for stage in STAGES:
        if stage == "Done":
            continue
        api = FakeAPI(buckets=STAGES)
        wf = Workflow(api, project_id=3)
        card = api.add_task(f"a card in {stage}", stage, assignee=api.me_user)
        try:
            wf.advance(card["id"], to="build", spec="approach")
        except WorkflowError as exc:
            assert "is in Done" not in str(exc), f"{stage} answered with the Done rule: {exc}"

    # ...and the same tool from Done DOES answer with it, so the loop above is a contrast and not
    # a vacuous pass
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    accepted = _accepted_card(api, wf, "the contrast")
    with pytest.raises(WorkflowError, match="is in Done"):
        wf.advance(accepted["id"], to="build", spec="approach")


def test_the_two_personal_gates_are_gone_rather_than_shadowed():
    """#626 and #649 put an `if stage == "Done"` in `return_task` and `decompose`. Under the
    shared guard those never execute again — measured on #662 with trace.Trace — and the human's
    answer required removing them, because a gate that cannot fire is code claiming to decide
    something it does not. This asserts the removal at the SOURCE, since behaviour cannot see the
    difference between a dead gate and a deleted one: that is the whole reason a reader is misled
    by one, and the whole reason a behavioural test would pass either way.

    It reads the AST and NOT the text, which is the difference between a pin and a grep: the
    comments that replaced those gates necessarily QUOTE the shape they replaced, and a text
    search cannot tell a live gate from prose about a dead one. That is the same use/mention trap
    the sweep-record scanner had to grow a vocabulary for."""
    import ast
    import textwrap

    import vikunja_mcp.workflow as wfmod

    def stage_literals_compared_in(method):
        """Every string literal this method's own `if` statements compare `stage` against."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            # walk the WHOLE test, not just a bare Compare: the live guard reads
            # `stage == "Done" and not allow_done`, whose test node is a BoolOp
            for sub in ast.walk(node.test):
                if not isinstance(sub, ast.Compare):
                    continue
                if isinstance(sub.left, ast.Name) and sub.left.id == "stage":
                    for comparator in sub.comparators:
                        if isinstance(comparator, ast.Constant) and isinstance(
                            comparator.value, str
                        ):
                            found.add(comparator.value)
        return found

    for name in ("return_task", "decompose"):
        compared = stage_literals_compared_in(getattr(Workflow, name))
        assert "Done" not in compared, (
            f"{name} still carries the personal Done gate #662 made dead — under the shared guard "
            f"in _find_task it can never fire, so it decides nothing and misleads a reader"
        )
        # the tool's OWN live stage gate is untouched: both still refuse from Review (#590/#663)
        assert "Review" in compared, f"{name} lost its Review gate"

    # ...and the live one really is in _find_task, so the assertions above mean "moved", not "gone"
    assert "Done" in stage_literals_compared_in(wfmod.Workflow._find_task)
