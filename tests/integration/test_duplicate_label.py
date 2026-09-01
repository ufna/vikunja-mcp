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
"""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI, VikunjaError
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
