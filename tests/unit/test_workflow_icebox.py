"""Icebox — the eighth stage, a "backlog of the backlog" (tracker #1640).

THE ONE DECISION EVERY TEST HERE SERVES: **the COLUMN is the gate, the LABEL is a hint.**

The column gates for free — `next_task` reads only `NEXT_TASK_STAGES`, `claim` takes only
Queue, `AGENT_ADVANCE` knows only Design->Build->Review — so this feature adds NO new gate.
The label deliberately does NOT join `LABEL_BLOCKED`/`LABEL_EPIC` in `offerable_queue`, and
`test_a_queue_card_carrying_the_icebox_label_is_still_offered` is the pin on that: two lines
below that filter, `withheld` is built EXCLUSIVELY from `excluded`, so a label there drops a
card out of the offering with no trace in the payload. A human who drags an iceboxed card
into Queue has said "yes, this one now" — a label gate would revoke that silently.

The second decision, and the one with the wider blast radius: **the column is OPTIONAL.**
`_bucket`'s presence check runs over `REQUIRED_STAGES`, not `STAGES`, because `stable` is a
moving channel — every consumer board that has not run `vikunja-mcp setup` yet would answer
the first call of ANY tool with "run `vikunja-mcp setup`" the moment this lands.

MUTATION SWEEP. Stand: `git clone --no-hardlinks` of the repo with the card's staged diff
applied, `uv sync` in the clone, `vikunja_mcp.__file__` printed and confirmed to resolve INSIDE
it; `__pycache__` removed before every round and `PYTHONDONTWRITEBYTECODE=1`; FAILED counted by
lines beginning `FAILED `, ERROR lines counted separately, SKIPPED recorded, and `collected`
cross-checked at 86 on every round including the control. Selection: this file plus
`test_setup.py` and `test_skill_contract.py`. Control 0 failed, 0 errors, 0 skipped; make the
`icebox` label a gate in `offerable_queue` -> 2 failed; make the column mandatory
(`REQUIRED_STAGES` -> `STAGES`) -> 1 failed; restore the bare `self._buckets_cache[title]` so a
missing optional column raises KeyError -> 2 failed; stop marking a frozen predecessor
`finishable: False` -> 1 failed; silence `_predecessor_frozen` at both call sites -> 2 failed.

TWO ROUNDS OF THAT SWEEP WERE INVALID FIRST, and both failures are the ones this repo keeps
re-learning, so they are recorded rather than quietly fixed. The label-gate mutation anchored on
`and not self._has_label(t, LABEL_EPIC)`, which occurs TWICE — the other is next_task's `stuck`
branch — so the edit never applied and the round scored a FALSE 0 against a control of 0, i.e.
it read exactly like a passing guard. And the two frozen-predecessor pins originally asserted
`"Icebox" in message`, which `_starving_tail` satisfies on its own: it renders every blocker as
`<ref> in '<stage>'`, so with the clause deleted outright the round still scored 0. Both pins
now key on `waiting will not help`, a phrase only the clause carries.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import (
    LABEL_ICEBOX,
    REQUIRED_STAGES,
    STAGES,
    Workflow,
    WorkflowError,
)


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    return api, wf


@pytest.fixture
def unmigrated():
    """A board from BEFORE this feature: every canonical column except Icebox."""
    api = FakeAPI(buckets=REQUIRED_STAGES)
    wf = Workflow(api, project_id=3)
    return api, wf


def label_titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id].get("labels") or []]


# --- the stage itself ---

def test_icebox_is_the_last_canonical_stage_and_the_only_optional_one():
    assert STAGES[-1] == "Icebox"                       # rightmost: setup positions by order
    assert STAGES[-2] == "Done"
    assert REQUIRED_STAGES == STAGES[:-1]               # exactly one stage is optional
    assert "Icebox" not in REQUIRED_STAGES


def test_a_board_without_the_icebox_column_stays_fully_operational(unmigrated):
    """The migration pin. `stable` re-resolves on every session start, so this lands on
    boards nobody has run `setup` on — and `_bucket` raises on the FIRST tool call, whatever
    it is, not on the one that wants Icebox."""
    api, wf = unmigrated
    t = api.add_task("job", "Build", assignee=api.me_user)
    assert wf.next_task()["task"]["id"] == t["id"]
    wf.advance(t["id"], to="review", worklog="did it", evidence="abc123")
    assert api.stage_of(t["id"]) == "Review"
    filed = wf.file_task(title="an ordinary finding")
    assert api.stage_of(filed["filed"]["id"]) == "Backlog"


def test_asking_for_the_icebox_column_where_there_is_none_is_a_workflow_error(unmigrated):
    """A WorkflowError, never a KeyError: `server._tool` converts WorkflowError/ConfigError/
    VikunjaError/httpx.HTTPError and nothing else, so a bare KeyError out of `_bucket` would
    take the stdio server down instead of answering the call."""
    _api, wf = unmigrated
    with pytest.raises(WorkflowError, match="Icebox"):
        wf._bucket("Icebox")


# --- file_task(icebox=True): the one agent-facing entrance ---

def test_file_task_icebox_lands_in_the_column_carrying_the_label(env):
    api, wf = env
    res = wf.file_task(title="legacy: tooltip copy is off by a word", icebox=True)
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Icebox"
    assert res["filed"]["stage"] == "Icebox"
    assert label_titles(api, new_id) == [LABEL_ICEBOX]
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert "Icebox" in marker


def test_file_task_icebox_and_queue_together_are_refused_creating_nothing(env):
    """queue=True means "a human said do this now"; icebox=True means "nobody will ever do
    this". They are not composable, and the refusal comes before anything is created."""
    api, wf = env
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="icebox"):
        wf.file_task(title="x", icebox=True, queue=True)
    assert len(api.tasks) == before


def test_file_task_icebox_on_a_board_without_the_column_refuses_creating_nothing(unmigrated):
    """Fail-fast, the `_target_bucket` rule: resolve the destination BEFORE `create_task`.
    Deliberately NOT a quiet fallback to Backlog — filing into the wrong column while
    reporting success is worse than refusing, because nobody learns the board is behind."""
    api, wf = unmigrated
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="Icebox"):
        wf.file_task(title="legacy nit", icebox=True)
    assert len(api.tasks) == before


def test_file_task_icebox_is_allowed_cross_project(env):
    """The asymmetry with queue=True is the point: another project's QUEUE is not ours to
    fill (it wakes their fleet), while another project's ICEBOX wakes nobody at all."""
    api, wf = env
    other = api.add_project("neighbor", buckets=["Inbox", *STAGES])
    res = wf.file_task(title="their legacy nit", project_id=other["id"], icebox=True)
    new_id = res["filed"]["id"]
    other_view = api.kanban_view(other["id"])
    target = next(
        b for b in api.buckets(other["id"], other_view["id"]) if b["title"] == "Icebox"
    )
    assert api.task_bucket[new_id] == target["id"]
    assert res["filed"]["stage"] == "Icebox"
    assert label_titles(api, new_id) == [LABEL_ICEBOX]


def test_file_task_cross_project_icebox_without_the_column_refuses_creating_nothing(env):
    api, wf = env
    other = api.add_project("neighbor", buckets=REQUIRED_STAGES)
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="Icebox"):
        wf.file_task(title="x", project_id=other["id"], icebox=True)
    assert len(api.tasks) == before


def test_plain_file_task_is_untouched_by_the_new_parameter(env):
    """Back-compat: the default path keeps its column, its marker and its result keys."""
    api, wf = env
    res = wf.file_task(title="an ordinary finding")
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Backlog"
    assert label_titles(api, new_id) == []
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert marker == "[filed-by-agent] filed by an agent for human triage"


# --- the column gates; the label does not ---

def test_next_task_never_offers_a_card_parked_in_icebox(env):
    api, wf = env
    api.add_task("frozen legacy", "Icebox")
    assert wf.next_task()["task"] is None


def test_claim_refuses_a_card_sitting_in_icebox(env):
    api, wf = env
    frozen = api.add_task("frozen legacy", "Icebox")
    with pytest.raises(WorkflowError, match="Queue"):
        wf.claim(frozen["id"])


def test_a_queue_card_carrying_the_icebox_label_is_still_offered(env):
    """NEGATIVE PIN — the decision this whole card is about.

    Delete the decision (add `and not self._has_label(t, LABEL_ICEBOX)` to `offerable_queue`
    beside the LABEL_BLOCKED/LABEL_EPIC clauses) and this test MUST go red. It guards two
    things at once: the label is not a gate, and a human's drag from Icebox into Queue is an
    instruction the tooling obeys rather than silently reverses."""
    api, wf = env
    card = api.add_task("a legacy nit a human decided to do after all", "Queue",
                        labels=[LABEL_ICEBOX])
    offered = wf.next_task()
    assert offered["task"]["id"] == card["id"]
    assert offered["stage"] == "Queue"


def test_the_offer_carries_the_icebox_hint_so_the_agent_spends_less_on_it(env):
    """The label's actual job: it rides in the payload as the "legacy, do the minimum"
    instruction. That is what "don't burn tokens on these" buys — an effort budget for a
    card being worked, never an invisibility that starves the pump."""
    api, wf = env
    card = api.add_task("a legacy nit", "Queue", labels=[LABEL_ICEBOX])
    assert wf.next_task()["task"]["icebox"]
    assert wf.get_task(card["id"])["icebox"]


def test_an_ordinary_card_carries_no_icebox_key(env):
    api, wf = env
    card = api.add_task("ordinary work", "Queue")
    assert "icebox" not in wf.next_task()["task"]
    assert "icebox" not in wf.get_task(card["id"])


# --- a predecessor frozen in Icebox ---

def test_a_predecessor_in_icebox_blocks_and_the_refusal_names_who_can_unfreeze_it(env):
    """The sharp edge of keeping Icebox out of READY_STAGES: the successor is blocked
    FOREVER, and the generic tail ("finish that one first") is the one action nobody can
    take — no agent tool moves a card out of Icebox."""
    api, wf = env
    frozen = api.add_task("frozen predecessor", "Icebox")
    successor = api.add_task("successor", "Queue")
    api.add_relation(successor["id"], frozen["id"], "blocked")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(successor["id"])
    message = str(exc.value)
    assert "finish that one first" not in message   # the unactionable tail is dropped
    # keyed on a phrase only the frozen clause carries, never on "Icebox": the blocker line
    # above the clause already renders `in 'Icebox'`, so asserting the bare stage name passes
    # with the clause deleted outright (measured — that is how the M5 round below scored 0
    # against a control of 0 before this assert was tightened).
    assert "waiting will not help" in message
    assert "follows/blocked link to it is the thing to drop" in message


def test_the_starving_tail_names_a_frozen_predecessor(env):
    """next_task SKIPS a gated card rather than refusing it, so under an ordinary /loop
    drain this message is the only place a human is ever told the chain froze."""
    api, wf = env
    frozen = api.add_task("frozen predecessor", "Icebox")
    successor = api.add_task("successor", "Queue")
    api.add_relation(successor["id"], frozen["id"], "blocked")
    res = wf.next_task()
    assert res["task"] is None
    assert res["starving"] is True
    # same tightening as the sibling above, and this is the test that MEASURED the need for it:
    # `_starving_tail` renders every blocker as "<ref> in '<stage>'", so `"Icebox" in message`
    # was true with `_predecessor_frozen` silenced — a pin that could not fail.
    assert "waiting will not help" in res["message"]


def test_an_ordinary_blocked_chain_keeps_its_wording(env):
    """Back-compat pin beside the two above: with no frozen predecessor in sight, the
    generic tail is exactly what it was."""
    api, wf = env
    head = api.add_task("head", "Build")
    successor = api.add_task("successor", "Queue")
    api.add_relation(successor["id"], head["id"], "blocked")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(successor["id"])
    message = str(exc.value)
    assert message.endswith("A predecessor becomes ready only at Review or Done; "
                            "finish that one first")


# --- ownership advice ---

def test_an_ownerless_icebox_card_reads_as_ordinary_not_as_damage(env):
    """Same shape as Backlog's entry, for the same reason: a card with no assignee is the
    EVERYDAY state of the freezer, so the advice must not send an agent looking for a fix."""
    api, wf = env
    frozen = api.add_task("frozen legacy", "Icebox")
    with pytest.raises(WorkflowError) as exc:
        wf.return_task(frozen["id"], reason="x")
    assert "Icebox" in str(exc.value) or "not damage" in str(exc.value)
