"""Epic-complete marker (#118 Part 2) against REAL Vikunja 2.3.0 — the control point the unit suite
structurally cannot be.

Why this must be an integration test (the #125 lesson, learned again the hard way here): the marker
reads an epic's `epic` label out of a CHILD's `related_tasks`, and the real server HOLLOWS the tasks
it embeds there — `labels` (and `assignees`, nested `related_tasks`) come back None even when the
task genuinely carries them; only scalars survive. Our FakeAPI once returned those sub-dicts fully
populated, so the fake agreed with the fake: 12 unit tests were green while the marker was a silent
no-op in production. This test drives the whole path server → marker → server: the LAST child of a
real epic reaches Review and the epic must gain `epic-ready` + the `[эпик собран]` comment. Read off
a hollowed sub-dict again and it goes red; the unit tests would not.
"""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import LABEL_EPIC, LABEL_EPIC_READY, Workflow

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def epicproj(boss_jwt, agent_jwts):
    """Isolated project + canonical board (mirrors test_sequence_gate). Boss (full perms) builds the
    epic scenario; the agent's scoped-token Workflow is the subject under test — its get_task is the
    exact hollowed read the marker keys off, and it does the claim/advance that fires the marker."""
    boss = VikunjaAPI(BASE, boss_jwt)
    pid = reconcile(boss, f"epic-{uuid.uuid4().hex[:8]}", shares=[("agent1", 1)])
    view = boss.kanban_view(pid)
    buckets = {b["title"]: b["id"] for b in boss.buckets(pid, view["id"])}
    jwt1, _ = agent_jwts
    wf1 = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid)
    return boss, pid, view, buckets, wf1


def test_epic_marker_fires_against_real_hollowed_related_tasks(epicproj):
    """Last child of a real epic reaches Review → the epic gains `epic-ready` + the `[эпик собран]`
    comment, driven end to end through the server's hollowed related_tasks. The behavioural
    assertions (not a JSON shape) are what catch the sub-dict-label regression the units missed."""
    boss, pid, view, buckets, wf1 = epicproj
    # the production shape decompose makes: an epic parent (label `epic`) with two children linked by
    # parenttask, one already at Review, one still to be worked.
    epic = boss.create_task(pid, "epic parent")
    boss.add_label(epic["id"], boss.get_or_create_label(LABEL_EPIC)["id"])
    boss.move_task(pid, view["id"], buckets["Backlog"], epic["id"])

    reviewed = boss.create_task(pid, "already-reviewed child")
    boss.add_relation(reviewed["id"], epic["id"], "parenttask")
    boss.move_task(pid, view["id"], buckets["Review"], reviewed["id"])

    last = boss.create_task(pid, "last child")
    boss.add_relation(last["id"], epic["id"], "parenttask")
    boss.move_task(pid, view["id"], buckets["Queue"], last["id"])

    # the exact trap: on the REAL server the epic embedded in the child's related_tasks is hollowed —
    # `labels` is None even though the epic genuinely carries `epic`. This is what the fix reads
    # AROUND (by re-fetching the full parent). Pin the shape so a future server change is noticed.
    child_related = wf1.api.get_task(last["id"]).get("related_tasks") or {}
    epic_subdict = (child_related.get("parenttask") or [{}])[0]
    assert epic_subdict.get("id") == epic["id"]                       # relation present...
    assert epic_subdict.get("labels") is None                        # ...but labels hollowed by the server
    assert wf1._has_label(wf1.api.get_task(epic["id"]), LABEL_EPIC)   # the FULL fetch does see the label

    # drive the real agent flow: claim the last child, advance it Design→Build→Review. The last
    # advance is where the marker must fire, end to end, through the hollowed related read.
    wf1.claim(last["id"])
    wf1.advance(last["id"], to="build", spec="do the last piece")
    wf1.advance(last["id"], to="review", worklog="did the last piece", evidence="abc123")

    # THE assertion (behaviour, not shape): the epic now carries `epic-ready` AND the `[эпик собран]`
    # comment — proof the marker worked against the real server. Red if a sub-dict label read returns.
    epic_labels = [lb["title"] for lb in boss.get_task(epic["id"]).get("labels") or []]
    assert LABEL_EPIC_READY in epic_labels
    epic_comments = [c["comment"] for c in boss.comments(epic["id"])]
    assert any("эпик собран" in c for c in epic_comments)

    # the agent NEVER moved the epic — it stays where the human left it (Backlog), only marked
    # (Part 1 skip + "only a human moves to Done" both intact).
    assert wf1.get_task(epic["id"])["stage"] == "Backlog"
