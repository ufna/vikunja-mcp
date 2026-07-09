"""Part 2 of the epic lifecycle (#118): advance→review marks a COMPLETE epic, best-effort.

When the LAST child of an epic reaches Review-or-Done, the agent finishing that child leaves a
visible marker on the EPIC — the `epic-ready` label plus an `[эпик собран]` comment — so the human
sees the container is assembled and can close the whole set (only a human moves anything to Done;
the epic is never moved by an agent). The marker is a deliberately ADDITIVE cross-task write (a
label + comment on a DIFFERENT card than the one being advanced), so it is STRICTLY best-effort:
any failure reaching the epic must NOT fail the child's advance nor change its payload. Keys off the
epic LABEL and the parenttask relation, never structure alone. Idempotent across bounce+re-advance.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import LABEL_EPIC_READY, STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def _epic(api, child_stages):
    """Epic parent (label epic, Backlog) with one child per stage in child_stages; children in an
    active stage (Design/Build) are assigned to me so they're advanceable. Returns (epic, [kids])."""
    epic = api.add_task("epic parent", "Backlog", labels=("epic",))
    kids = []
    for i, stage in enumerate(child_stages):
        assignee = api.me_user if stage in ("Design", "Build") else None
        c = api.add_task(f"child{i}", stage, assignee=assignee)
        api.add_relation(c["id"], epic["id"], "parenttask")
        kids.append(c)
    return epic, kids


def _labels(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]


def _epic_comments(api, epic_id):
    return [c for c in api.comments_text(epic_id) if c.startswith("[эпик собран]")]


# --- happy path: last child completes the epic ---

def test_marks_epic_when_last_child_reaches_review(env):
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Review", "Build"])
    res = wf.advance(c1["id"], to="review", worklog="w", evidence="e")
    assert res["moved_to"] == "Review" and res["review_needed"] is True
    assert LABEL_EPIC_READY in _labels(api, epic["id"])
    assert len(_epic_comments(api, epic["id"])) == 1
    assert _labels(api, epic["id"]).count(LABEL_EPIC_READY) == 1  # exactly one, no dup on a single fire


def test_done_sibling_counts_as_ready(env):
    """Readiness is Review-or-Done (READY_STAGES, reused): a sibling a human already moved to Done
    still counts, so the last child reaching Review completes the epic."""
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Done", "Build"])
    wf.advance(c1["id"], to="review", worklog="w", evidence="e")
    assert LABEL_EPIC_READY in _labels(api, epic["id"])
    assert len(_epic_comments(api, epic["id"])) == 1


# --- must NOT fire ---

def test_no_mark_when_a_sibling_still_below_review(env):
    """Fires only when EVERY sibling is Review-or-Done. One sibling still in Build → no mark."""
    api, wf = env
    epic, (c0, c1, c2) = _epic(api, ["Review", "Build", "Build"])
    wf.advance(c1["id"], to="review", worklog="w", evidence="e")  # c2 still in Build
    assert LABEL_EPIC_READY not in _labels(api, epic["id"])
    assert _epic_comments(api, epic["id"]) == []


def test_no_mark_for_task_without_parenttask(env):
    """A plain task with no parent → advance→review does nothing epic-related and never crashes."""
    api, wf = env
    t = api.add_task("lonesome", "Build", assignee=api.me_user)
    res = wf.advance(t["id"], to="review", worklog="w", evidence="e")
    assert res["moved_to"] == "Review"
    assert not any(lb["title"] == LABEL_EPIC_READY for lb in api._labels)  # marker label never created


def test_no_mark_when_parent_lacks_epic_label(env):
    """An ordinary parent (no epic label) with all children ready is NOT marked — keys off the epic
    LABEL, never the mere presence of children (mirror of the Part 1 guard)."""
    api, wf = env
    parent = api.add_task("ordinary parent", "Backlog")  # NO epic label
    c0 = api.add_task("child0", "Review")
    c1 = api.add_task("child1", "Build", assignee=api.me_user)
    api.add_relation(c0["id"], parent["id"], "parenttask")
    api.add_relation(c1["id"], parent["id"], "parenttask")
    wf.advance(c1["id"], to="review", worklog="w", evidence="e")
    assert LABEL_EPIC_READY not in _labels(api, parent["id"])


def test_marker_not_fired_on_advance_to_build(env):
    """The marker only fires on to='review' — advancing a child to Build never marks its epic."""
    api, wf = env
    epic, (c0,) = _epic(api, ["Design"])
    wf.advance(c0["id"], to="build", spec="approach")
    assert LABEL_EPIC_READY not in _labels(api, epic["id"])


# --- idempotency across a bounce + re-advance ---

def test_idempotent_no_double_mark_on_bounce_and_readvance(env):
    """The marker fired once; a child bounced Review→Build and re-advanced must NOT double-comment
    or double-label the epic (idempotency keyed on the epic-ready label)."""
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Review", "Build"])
    wf.advance(c1["id"], to="review", worklog="w", evidence="e")           # marks
    api.task_bucket[c1["id"]] = api.bucket_id("Build")                     # human/reviewer bounces it
    wf.advance(c1["id"], to="review", worklog="w2", evidence="e2")         # re-advance — must not re-fire
    assert _labels(api, epic["id"]).count(LABEL_EPIC_READY) == 1
    assert len(_epic_comments(api, epic["id"])) == 1


# --- STRICTLY best-effort: an epic write/lookup failure must never fail the child ---

@pytest.mark.parametrize("break_method", ["get_task", "add_comment", "add_label"])
def test_epic_marker_failure_never_fails_the_child(env, break_method):
    """The marker reaches out to a DIFFERENT card, so if the epic lookup, comment, or label raises,
    the CHILD's advance→review must still succeed and return its normal payload (unchanged shape).
    Break each epic-directed call in turn and assert the child is wholly unaffected."""
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Review", "Build"])
    epic_id = epic["id"]
    orig = getattr(api, break_method)

    def boom(task_id, *a, **k):
        if task_id == epic_id:          # break only calls aimed at the epic card
            raise RuntimeError(f"{break_method} boom on epic")
        return orig(task_id, *a, **k)

    setattr(api, break_method, boom)
    res = wf.advance(c1["id"], to="review", worklog="did it", evidence="deadbeef")
    # payload shape unchanged — the marker adds/removes no keys
    assert set(res) == {"moved_to", "task_id", "review_needed", "review_kind", "note"}
    assert res["moved_to"] == "Review" and res["task_id"] == c1["id"]
    assert res["review_needed"] is True and res["review_kind"] == "change"
    assert api.stage_of(c1["id"]) == "Review"   # child advanced despite the epic write failing


def test_child_payload_shape_unchanged_on_successful_mark(env):
    """Even on a SUCCESSFUL mark, the child's own result carries only its usual keys — the marker is
    a pure side effect on the epic, never reported back in the child's payload."""
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Review", "Build"])
    res = wf.advance(c1["id"], to="review", worklog="w", evidence="e")
    assert set(res) == {"moved_to", "task_id", "review_needed", "review_kind", "note"}


# --- Part 1 + Part 2 compose: a marked epic stays a container ---

def test_marked_epic_still_skipped_by_next_task_and_refused_by_claim(env):
    """Adding the epic-ready marker does not make the epic claimable or offerable — it's still an
    epic container. next_task skips it, claim refuses it; it stays reachable to the human,
    untouched by the pump."""
    api, wf = env
    epic = api.add_task("marked epic", "Queue", labels=("epic", LABEL_EPIC_READY))
    free = api.add_task("real", "Queue", priority=1)
    assert wf.next_task()["task"]["id"] == free["id"]      # marked epic not offered
    with pytest.raises(WorkflowError, match="container"):
        wf.claim(epic["id"])                                # marked epic still refused


# --- fake fidelity: the fake must hollow related sub-dicts like the real server (#125 guard) ---

def test_fake_hollows_related_subdicts_like_the_real_server(env):
    """The reason this whole rework happened: real Vikunja 2.3.0 returns tasks embedded in
    related_tasks HOLLOWED (labels/assignees/nested related_tasks = None; only scalars survive), but
    the fake once returned them fully — so the marker's happy-path tests were vacuously green while
    production was dead (#125). Pin the fake honest so a future edit can't silently re-inflate it and
    re-vacuum these tests. The integration test is the end-to-end proof against a real server; this
    is the cheap unit guard on the fake itself."""
    api, wf = env
    epic, (child,) = _epic(api, ["Build"])
    sub = ((api.get_task(child["id"]).get("related_tasks") or {}).get("parenttask") or [{}])[0]
    assert sub.get("id") == epic["id"]        # the relation + scalar id survive (so id-reads still work)
    assert sub.get("labels") is None          # ...but labels/assignees/relations are hollowed, as on the server
    assert sub.get("assignees") is None
    assert sub.get("related_tasks") is None


# --- #134: a marker failure is swallowed but NO LONGER silent (stderr trace, never stdout) ---

@pytest.mark.parametrize(
    "exc",
    [TypeError("marker refactor bug"), VikunjaError(500, "epic card exploded")],
    ids=["TypeError", "VikunjaError"],
)
def test_marker_failure_is_swallowed_but_logged_to_stderr(env, capsys, exc):
    """#134: `except Exception` catches programmer errors (TypeError) as well as server errors
    (VikunjaError). Both must still be swallowed — the child's advance never fails on a cosmetic
    marker for ANOTHER card — but no longer in total silence. Assert: the child reaches Review with
    an unchanged payload, ONE diagnostic line lands on STDERR naming the advancing child and the
    exception class, and NOTHING is written to stdout (the MCP stdio protocol channel)."""
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Review", "Build"])
    epic_id = epic["id"]
    orig = api.get_task

    def boom(task_id, *a, **k):
        if task_id == epic_id:            # break the marker's fetch of the epic parent
            raise exc
        return orig(task_id, *a, **k)

    api.get_task = boom
    res = wf.advance(c1["id"], to="review", worklog="did it", evidence="deadbeef")

    # child wholly unaffected: reached Review, payload shape unchanged (marker adds/removes no keys)
    assert res["moved_to"] == "Review" and api.stage_of(c1["id"]) == "Review"
    assert set(res) == {"moved_to", "task_id", "review_needed", "review_kind", "note"}

    captured = capsys.readouterr()
    assert captured.out == ""                        # never pollute the stdio protocol channel
    assert f"#{c1['id']}" in captured.err            # names the advancing child (actionable anchor)
    assert exc.__class__.__name__ in captured.err    # and the exception class, to act on


def test_successful_mark_writes_nothing_to_stderr_or_stdout(env, capsys):
    """#134: the stderr diagnostic is for FAILURES only. A clean marker run (marker DID fire) must
    leave BOTH channels empty, so stderr stays a real signal and the stdio channel is never touched."""
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Review", "Build"])
    res = wf.advance(c1["id"], to="review", worklog="w", evidence="e")
    assert LABEL_EPIC_READY in _labels(api, epic["id"])   # happy path actually exercised
    assert res["moved_to"] == "Review"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_no_stdout_and_no_stderr_when_marker_is_a_noop(env, capsys):
    """A plain task with no epic parent: the marker is a no-op, and neither channel is written."""
    api, wf = env
    t = api.add_task("lonesome", "Build", assignee=api.me_user)
    wf.advance(t["id"], to="review", worklog="w", evidence="e")
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


# --- #135: an exception whose __str__ itself raises must not escape the except handler ---

class EvilStr(Exception):
    """Pathological exception whose __str__ ITSELF raises, so str(exc) blows up. The marker's
    stderr log formats `{exc}` (-> str(exc)) INSIDE the except handler, so an unguarded log line
    would let this second exception escape advance() (#135)."""

    def __str__(self):
        raise RuntimeError("__str__ itself raises")


def test_marker_exception_with_raising_str_does_not_escape_advance(env, capsys):
    """#135: the marker's LOG path must be as guarded as the marker itself. When the swallowed
    exception's __str__ itself raises, building the stderr line (`{exc}` calls str(exc)) would
    propagate that second exception out of advance() — but by then the child has ALREADY reached
    Review and written its [worklog], so advance would raise for work that genuinely succeeded (a
    state/report divergence, not a lost log). Assert the child reaches Review with an unchanged
    payload, stdout stays empty (the MCP stdio channel), and stderr still names the child and the
    exception class (a silent swallow would undo #134)."""
    api, wf = env
    epic, (c0, c1) = _epic(api, ["Review", "Build"])
    epic_id = epic["id"]
    orig = api.add_label

    def boom(task_id, *a, **k):
        if task_id == epic_id:            # break the marker's label write on the epic card
            raise EvilStr()
        return orig(task_id, *a, **k)

    api.add_label = boom
    # must NOT raise, even though EvilStr.__str__ blows up inside the except handler
    res = wf.advance(c1["id"], to="review", worklog="did it", evidence="deadbeef")

    # child wholly unaffected: reached Review, payload shape unchanged (marker adds/removes no keys)
    assert res["moved_to"] == "Review" and api.stage_of(c1["id"]) == "Review"
    assert set(res) == {"moved_to", "task_id", "review_needed", "review_kind", "note"}

    captured = capsys.readouterr()
    assert captured.out == ""                        # never pollute the stdio protocol channel
    assert captured.err != ""                        # not a silent swallow — #134 stays satisfied
    assert f"#{c1['id']}" in captured.err            # still names the advancing child
    assert "EvilStr" in captured.err                 # and the exception class (str(exc) not needed)
