"""One run of every comment-writing path in `workflow.py` — shared by two pins (#1168).

NOT A TEST MODULE. It defines no test and is never collected; it exists because TWO files need
the same driver and neither may import it from the other. `test_card_language.py` imports
`_MARKERS` from `test_card_text_is_ascii.py`, so the gate file importing the driver back out of
`test_card_language.py` would close a cycle. The driver moved here rather than being duplicated:
two copies of a comment-site driver drift, and the whole value of the thing is that it is
exhaustive over the sites that carry the product's own prose.

WHY THE GATE NEEDS IT AT ALL. `test_card_text_is_ascii.py` reads `workflow.py` as SOURCE and
cannot follow a value across a function boundary — that is stated there and is deliberate. #1168
measured what that costs at the gate's own selection (itself alone, which is that file's stated
sweep rule so no collateral test can stand in for the pin), ON THE TREE BEFORE THE FIX: a
module-level constant carrying an em dash into the `[attach]` body, and a same-module sibling
helper returning one into the `[decompose]` body, each gave control 0 failed / 0 errors,
4 collected; mutation 0 failed. Both are ordinary refactors, not constructed shapes.
Driving those sites and reading what LANDS is the check that does not care which shape the
code took, and this module is what makes it available to the gate without a cycle and without a
second copy.
"""
from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow


def wf_for(language):
    """A fresh FakeAPI board plus a `Workflow` writing its card text in `language`."""
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, language=language)


def drive_every_comment_site(api, wf, tmp_path):
    """Eleven of `workflow.py`'s twelve `add_comment` call sites, run once each, on one board.

    ELEVEN AND NOT TWELVE, counted rather than asserted: the one left out is `Workflow.comment`,
    which posts the agent's own text with no marker and no literal of the product's, so there is
    nothing there for an ASCII pin to be right or wrong about. It is named because "every path"
    was the wording this function carried before #1168's second pass counted them, and because
    `comment` is exactly the shape that would matter if it ever grew a prefix.

    Returns the comment stream of the whole board — GROUPED BY TASK ID, in creation order within
    each task, which is not the same as board-wide creation order (the epic parent has a lower id
    than its child, so its `[epic-ready]` line precedes a `[worklog]` written before it). Any
    stable order does for the pins, which either compare two runs of this same function or scan
    the stream; the ordering is named because a later reader would otherwise assume the wrong one.
    The neighbouring project a cross-project `file_task` writes to is included. It is one function
    rather than a test per site because the flip pin compares two RUNS of it: what matters is that
    the two streams line up position for position, which only holds if both runs took the same
    path. Completeness is asserted rather than trusted — `test_the_driver_reaches_every_marker`
    walks the result against `_MARKERS`, which `test_card_text_is_ascii.py` owns. READ THAT CHAIN
    AT ITS REAL STRENGTH: the check is `_MARKERS` SUBSET of what this function writes, and the pin
    next door asserts `_MARKERS` is a subset of the brackets actually in `workflow.py` — neither
    direction says a bracket in the source must be in `_MARKERS`. The two sets are equal today
    (derived: ten tokens each), which is a fact about the tree and not an invariant, so a marker
    invented tomorrow reaches this driver only once somebody adds it to `_MARKERS` by hand.

    EVERY AGENT-SUPPLIED VALUE HERE IS ASCII ON PURPOSE, and that is a constraint on edits to this
    function rather than an accident of how it was typed. The gate's runtime pin asserts the whole
    stream is ASCII in the default language; the tool does not rewrite what an agent hands it, so
    a Russian `spec` or note passed in here would fail that pin while proving nothing about the
    product's own prose. Keep new inputs ASCII, and pin non-ASCII passthrough where it is already
    pinned — `test_workflow_gates.py` for the `[attach]` note, `advance`'s report tests for a
    Russian `worklog` and `root_cause`.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    queued = api.add_task("queued", "Queue")
    wf.claim(queued["id"])                                        # [claim]
    wf.advance(queued["id"], to="build", spec="the approach")     # [spec]
    wf.advance(                                                   # [worklog]
        queued["id"], to="review", worklog="what was done",
        evidence="deadbeef", root_cause="why it happened",
    )
    wf.review_task(queued["id"], verdict="needs_work", report="not yet")   # [review] NEEDS WORK
    # needs_work sends the card back to Build, so the rework has to be re-submitted before the
    # second verdict can be cast — that is the real cycle, and it puts a SECOND [worklog] on the
    # card, which is what the offering branch next door compares timestamps against.
    wf.advance(queued["id"], to="review", worklog="reworked", evidence="c0ffee")
    wf.review_task(queued["id"], verdict="approve", report="good now")     # [review] APPROVE

    parked = api.add_task("parked", "Build", assignee=api.me_user)
    wf.call_human(parked["id"], question="which option?")         # [needs-human]

    blocked = api.add_task("blocked", "Build", assignee=api.me_user)
    wf.return_task(blocked["id"], reason="waiting on infra")      # [blocked]

    big = api.add_task("big", "Build", assignee=api.me_user)
    wf.decompose(big["id"], [{"title": "part one"}, {"title": "part two"}], ordered=True)

    wf.file_task("a finding", description="found it", related_task_id=big["id"])
    wf.file_task("a queued finding", queue=True)
    wf.file_task("a plain finding")
    neighbour = api.add_project("neighbour", buckets=STAGES)
    wf.file_task("a cross-project finding", project_id=neighbour["id"])

    # a card crossing a project boundary, both shapes (#1179): handoff writes [handoff] here
    # and [filed-by-agent] over there, transfer_task writes [moved] on the card it moves.
    handing_off = api.add_task("needs the other repo", "Build", assignee=api.me_user)
    cross = Workflow(
        api, project_id=3, language=wf.language, siblings={"neighbour": neighbour["id"]},
    )
    cross.handoff(handing_off["id"], to="neighbour", title="the other half")   # [handoff]
    misfiled = api.add_task("wrong board", "Build", assignee=api.me_user)
    cross.transfer_task(misfiled["id"], to="neighbour", reason="belongs there")  # [moved]

    with_attachment = api.add_task("with an attachment", "Build", assignee=api.me_user)
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"x" * 2048)
    wf.attach_file(with_attachment["id"], str(blob), note="a screenshot")   # [attach]

    # the epic container's assembled notice: one child, taken to Review by the same agent
    epic = api.add_task("epic parent", "Backlog", labels=("epic",))
    child = api.add_task("only child", "Build", assignee=api.me_user)
    api.add_relation(child["id"], epic["id"], "parenttask")
    wf.advance(child["id"], to="review", worklog="child done", evidence="cafe")

    return [
        text
        for task_id in sorted(api.tasks)
        for text in api.comments_text(task_id)
    ]


def attach_line(wf, api, tmp_path, size):
    """The one comment whose body crosses a function boundary: `_human_size`'s units render
    inside the `[attach]` line from another function, so it needs its own driver."""
    target = api.add_task("with an attachment", "Build", assignee=api.me_user)
    blob = tmp_path / f"blob-{size}.bin"
    blob.write_bytes(b"x" * size)
    wf.attach_file(target["id"], str(blob), note="a screenshot")
    return [c for c in api.comments_text(target["id"]) if c.startswith("[attach]")][0]


def marker(comment):
    """The bracket at the head of a comment, `[` through `]` inclusive."""
    assert comment.startswith("["), f"comment does not open with a marker: {comment!r}"
    return comment[: comment.index("]") + 1]
