"""Hard sequence gate (option C, epic #94) against REAL Vikunja 2.3.0 — pins the load-bearing
server behaviour the unit suite can't: writing `precedes` one way makes the server materialise
the inverse `follows` on the successor, and the gate reads THAT.

Why this must be an integration test (#125, filed at retro-review of #104): the gate has an
asymmetry — decompose(ordered=True) WRITES `child[i] precedes child[i+1]`, but the gate READS
`follows`/`blocked`. The only bridge is Vikunja's server-side auto-inverse; no line of our code
performs it. Unit tests drive FakeAPI, which synthesises the same inverse on read, so the fake
merely agrees with the fake. If a future Vikunja stopped auto-inverting, units stay green while
production successors gain no `follows`, the gate reports "no predecessors", and the whole tail
of every ordered chain becomes claimable at once — the hard gate silently degrading to advisory.
This test is the only control point that would go red on that server regression.
"""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI, VikunjaError
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import Workflow, WorkflowError

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def seqproj(boss_jwt, agent_jwts):
    """Isolated project + canonical board + one scoped-token agent Workflow (mirrors
    test_agent_flow.py). Boss (full perms) does setup moves; the agent's Workflow is the
    subject under test — its scoped-token get_task is the exact read the gate keys off."""
    boss = VikunjaAPI(BASE, boss_jwt)
    pid = reconcile(boss, f"seq-{uuid.uuid4().hex[:8]}", shares=[("agent1", 1)])
    view = boss.kanban_view(pid)
    buckets = {b["title"]: b["id"] for b in boss.buckets(pid, view["id"])}

    def enqueue(title, stage="Queue", priority=0):
        t = boss.create_task(pid, title, priority=priority)
        boss.move_task(pid, view["id"], buckets[stage], t["id"])
        return t

    jwt1, _ = agent_jwts
    wf1 = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid)
    return boss, pid, view, buckets, enqueue, wf1


def test_precedes_auto_inverts_to_follows_and_arms_the_gate(seqproj):
    """The core regression pin. Write ONE side ("head precedes tail") — exactly the
    load-bearing line of decompose(ordered=True) — and prove the WHOLE path server → gate:
    the server materialises `follows: head` on the tail, and the sequence gate reads it and
    refuses to start the tail while the head is unfinished. A shape assertion alone would just
    re-test the JSON; the behavioural assertions (through _unfinished_predecessors and claim)
    are what actually catch a server that stopped auto-inverting."""
    boss, pid, view, buckets, enqueue, wf1 = seqproj
    head = enqueue("chain head")
    tail = enqueue("chain tail")

    # (2) write precedes in ONE direction only — the same call decompose(ordered=True) makes:
    #     add_relation(created[i], created[i+1], "precedes"). Nothing writes `follows`.
    wf1.api.add_relation(head["id"], tail["id"], "precedes")

    # (3) shape: the SERVER auto-created the inverse. Read via the agent's scoped-token client —
    #     the exact surface the gate consumes — and confirm the tail carries `follows: head`
    #     while the head carries NO follows (it only has an outgoing precedes).
    tail_related = wf1.api.get_task(tail["id"]).get("related_tasks") or {}
    assert head["id"] in [t["id"] for t in tail_related.get("follows") or []]
    assert "precedes" not in tail_related  # the inverse landed on the tail, not the raw kind
    head_related = wf1.api.get_task(head["id"]).get("related_tasks") or {}
    assert not (head_related.get("follows") or [])

    # (4) THE important one — drive that real data through the gate (server → gate, not shape).
    #     Head sits in Queue (below Review), so it is an UNFINISHED predecessor of the tail:
    #     the gate must SEE it via the server-materialised follows.
    assert wf1._unfinished_predecessors(head["id"]) == []          # head has no predecessor
    tail_blockers = wf1._unfinished_predecessors(tail["id"])
    assert [b["id"] for b in tail_blockers] == [head["id"]]         # tail is gated BY the head
    assert tail_blockers[0]["stage"] == "Queue"

    # claim is the black-box proof: the successor is unclaimable while the head is unfinished,
    # and the refusal names the head. A hard refusal — the tail is neither moved nor assigned.
    with pytest.raises(WorkflowError) as exc:
        wf1.claim(tail["id"])
    assert head["identifier"] in str(exc.value)
    assert (boss.get_task(tail["id"]).get("assignees") or []) == []
    assert wf1.get_task(tail["id"])["stage"] == "Queue"

    # gate tracks the SAME server relation through the ready transition: a predecessor is
    # "ready" at Review (option C, human's choice), so moving the head to Review must free the
    # tail — the gate's read of `follows` now finds the head already ready.
    boss.move_task(pid, view["id"], buckets["Review"], head["id"])
    assert wf1._unfinished_predecessors(tail["id"]) == []
    assert wf1.claim(tail["id"])["claimed"] is True
    assert wf1.get_task(tail["id"])["stage"] == "Design"


def test_unknown_relation_kind_rejected_by_server_400_4007(seqproj):
    """Enum contract (point 5): add_relation takes a free-form string, so a typo/unknown kind is
    caught only by the SERVER. Pin that it fails fast with HTTP 400 code 4007 ("task relation is
    invalid"), not silently — the whole ordered-chain feature relies on the kind being valid."""
    _boss, _pid, _view, _buckets, enqueue, wf1 = seqproj
    a = enqueue("enum a")
    b = enqueue("enum b")
    with pytest.raises(VikunjaError) as exc:
        wf1.api.add_relation(a["id"], b["id"], "preceeds")  # typo — not a valid relation_kind
    assert exc.value.status == 400
    assert "4007" in exc.value.message
