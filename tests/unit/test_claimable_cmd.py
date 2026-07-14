"""`vikunja-mcp claimable` — the sibling-exported claimable verdict.

The JSON printed here is a CROSS-REPO CONTRACT consumed by hgdev-acp's repo-agent
loop pre-launch idle check. The key set and the exit-code split (0 = the check RAN,
1 = the check FAILED) are public API: renaming a key or repurposing an exit code
breaks the hub's check.
"""
import copy
import json

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import claimable_cmd
from vikunja_mcp.claimable_cmd import classify_next, run_claimable
from vikunja_mcp.workflow import STAGES, Workflow


@pytest.mark.parametrize("result,expected", [
    ({"review": True, "review_kind": "bug", "task": {"id": 5}},
     {"claimable": True, "kind": "review", "task_id": 5}),
    ({"resume": True, "stage": "Build", "task": {"id": 6}},
     {"claimable": True, "kind": "resume", "task_id": 6}),
    ({"resume": True, "stage": "Design", "task": {"id": 6}},
     {"claimable": True, "kind": "resume", "task_id": 6}),
    ({"resume": True, "stage": "Queue", "task": {"id": 7}},
     {"claimable": True, "kind": "stuck_claim", "task_id": 7}),
    ({"resume": False, "task": {"id": 8}},
     {"claimable": True, "kind": "queue", "task_id": 8}),
    ({"task": None, "message": "the queue is empty — no work for the agent"},
     {"claimable": False, "kind": "empty", "task_id": None}),
    ({"task": None, "starving": True, "waiting_count": 2, "waiting": []},
     {"claimable": False, "kind": "starving", "task_id": None}),
    ({"task": None, "cycle": True, "cycle_tasks": []},
     {"claimable": False, "kind": "cycle", "task_id": None}),
])
def test_classify_next_covers_every_next_task_shape(result, expected):
    assert classify_next(result) == expected


def test_dogfood_review_bucket_full_of_my_tasks_is_not_claimable():
    """THE 2026-07-14 dogfood regression, pinned at the source: Queue/Design/Build
    empty, Review holds 25 tasks ALL assigned to the caller (done work awaiting a
    HUMAN's Done move). The hub's old bucket-presence heuristic read that board as
    "work!" forever, while next_task correctly offers nothing (you never review your
    own work) — ~144 no-op agent boots/day ≈ $105/day for zero work. The exported
    verdict MUST therefore be claimable=false.

    Every card carries a [worklog] ON PURPOSE — that is what the real board looked like,
    because advance(to='review') hard-requires a report and posts one. Without it the
    worklog-freshness guard would filter these cards on its own and this test would pass
    even with the own-work guard deleted: vacuous. With it, `my_id in assignees` is the
    ONLY thing between this board and a claimable=true, so the pin bites if it's removed."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    for i in range(25):
        t = api.add_task(f"shipped {i}", "Review", assignee=api.me_user)
        api.add_comment(t["id"], f"[worklog]\nСделано: shipped {i}\n\nEvidence: sha{i}")
    assert classify_next(wf.next_task()) == {
        "claimable": False, "kind": "empty", "task_id": None,
    }


def test_free_queue_task_is_claimable():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("free work", "Queue")
    assert classify_next(wf.next_task()) == {
        "claimable": True, "kind": "queue", "task_id": t["id"],
    }


def test_someone_elses_review_with_worklog_is_claimable():
    """The OVER side must survive: independent-review work still launches the agent."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    other = {"id": 77, "username": "agent-other"}
    t = api.add_task("their change", "Review", assignee=other)
    api.add_comment(t["id"], "[worklog]\nСделано: X\n\nEvidence: sha")
    assert classify_next(wf.next_task()) == {
        "claimable": True, "kind": "review", "task_id": t["id"],
    }


def test_my_unfinished_build_task_is_claimable_as_resume():
    """The other OVER lane: an agent killed mid-Build leaves its task assigned to it in
    Build. next_task hands it back (resume) — the hub MUST relaunch, or unfinished work
    is stranded until a human notices."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("half-done", "Build", assignee=api.me_user)
    assert classify_next(wf.next_task()) == {
        "claimable": True, "kind": "resume", "task_id": t["id"],
    }


def test_the_check_makes_no_writes():
    """READ-ONLY CONTRACT PIN: the hub polls this per loop tick — a side effect added to
    next_task would silently become a per-poll tracker mutation. Snapshot EVERY piece of
    mutable FakeAPI state a write could land in — tasks (incl. their assignees/labels),
    bucket placement, comments, the label registry, relations, AND the board surface
    itself (buckets, the kanban view config, shares, attachments) — and prove it is
    untouched. The board half matters: a stray bucket/view write would otherwise pass
    unseen, and it is exactly the kind of "harmless" reconcile that creeps into a read."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    api.add_task("free", "Queue")
    other = {"id": 77, "username": "agent-other"}
    r = api.add_task("their change", "Review", assignee=other)
    api.add_comment(r["id"], "[worklog]\nСделано: X\n\nEvidence: sha")

    def snapshot():
        return copy.deepcopy((
            api.tasks, api.task_bucket, api._comments, api._labels, api.relations,
            api._buckets, api.view_config, api.shares, api._attachments,
        ))

    before = snapshot()

    classify_next(wf.next_task())

    assert before == snapshot()


def test_run_claimable_prints_exactly_one_json_line_exit_0(monkeypatch, capsys, tmp_path):
    api = FakeAPI(buckets=STAGES)
    t = api.add_task("free", "Queue")
    monkeypatch.chdir(tmp_path)  # no repo toml — the env layer alone, as the hub supplies it
    monkeypatch.setenv("VIKUNJA_URL", "https://tracker.example.com")
    monkeypatch.setenv("VIKUNJA_TOKEN", "tok-value")
    monkeypatch.setenv("VIKUNJA_PROJECT_ID", "3")
    monkeypatch.setattr(claimable_cmd, "VikunjaAPI", lambda url, token: api)

    assert run_claimable() == 0

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1, "stdout IS the machine contract: exactly one JSON line"
    assert json.loads(lines[0]) == {
        "claimable": True, "kind": "queue", "task_id": t["id"],
    }


def test_run_claimable_failure_is_one_json_error_line_exit_1(monkeypatch, capsys, tmp_path):
    """A FAILED check (bad/missing config, tracker down) must be loud and distinguishable
    from a clean "no work": exit 1 + an {"error"} line, never a false claimable=false."""
    monkeypatch.chdir(tmp_path)
    # belt-and-braces over the autouse isolated_user_env_file fixture: without config the
    # body must NOT be able to build a real client and reach out to a live tracker
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", tmp_path / "nope")
    for var in ("VIKUNJA_URL", "VIKUNJA_TOKEN", "VIKUNJA_PROJECT_ID"):
        monkeypatch.delenv(var, raising=False)

    assert run_claimable() == 1

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    err = json.loads(out[0])
    assert "ConfigError" in err["error"]


def test_run_claimable_error_line_never_leaks_the_token(monkeypatch, capsys, tmp_path):
    """The hub logs this line verbatim on failure — it must stay credential-free even
    when the failure happens with a token loaded."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIKUNJA_URL", "https://tracker.example.com")
    monkeypatch.setenv("VIKUNJA_TOKEN", "super-secret-token")
    monkeypatch.setenv("VIKUNJA_PROJECT_ID", "3")

    def boom(url, token):
        raise RuntimeError(f"connection refused to {url}")

    monkeypatch.setattr(claimable_cmd, "VikunjaAPI", boom)

    assert run_claimable() == 1

    out = capsys.readouterr().out
    assert "super-secret-token" not in out
    assert json.loads(out.strip())["error"] == (
        "RuntimeError: connection refused to https://tracker.example.com"
    )
