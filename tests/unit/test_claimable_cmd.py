"""`vikunja-mcp claimable` — the sibling-exported claimable verdict.

The JSON printed here is a CROSS-REPO CONTRACT consumed by hgdev-acp's repo-agent
loop pre-launch idle check. The key set and the exit-code split (0 = the check RAN,
1 = the check FAILED) are public API: renaming a key or repurposing an exit code
breaks the hub's check.
"""
import copy
import io
import json
import os
import signal
import subprocess
import sys
import textwrap

import httpx
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import claimable_cmd
from vikunja_mcp.claimable_cmd import TRACE_OPT_OUT_ENV, classify_next, run_claimable
from vikunja_mcp.workflow import STAGES, Workflow


@pytest.mark.parametrize("result,expected", [
    # the review offer and the free queue carry "stage" too since the rulebook's tick branches
    # on it (workflow.py; pinned in test_workflow_wip) — these shapes mirror the real payloads
    ({"review": True, "review_kind": "bug", "stage": "Review", "task": {"id": 5}},
     {"claimable": True, "kind": "review", "task_id": 5}),
    ({"resume": True, "stage": "Build", "task": {"id": 6}},
     {"claimable": True, "kind": "resume", "task_id": 6}),
    ({"resume": True, "stage": "Design", "task": {"id": 6}},
     {"claimable": True, "kind": "resume", "task_id": 6}),
    ({"resume": True, "stage": "Queue", "task": {"id": 7}},
     {"claimable": True, "kind": "stuck_claim", "task_id": 7}),
    ({"resume": False, "stage": "Queue", "task": {"id": 8}},
     {"claimable": True, "kind": "queue", "task_id": 8}),
    ({"task": None, "message": "the queue is empty — no work for the agent"},
     {"claimable": False, "kind": "empty", "task_id": None}),
    ({"task": None, "starving": True, "waiting_count": 2, "waiting": []},
     {"claimable": False, "kind": "starving", "task_id": None}),
    # #1202: an added KEY, deliberately not an added KIND — the hub's enum is closed and
    # nothing here is claimable, so "empty" is the honest verdict for it.
    ({"task": None, "all_excluded": True, "withheld": [{"id": 9}]},
     {"claimable": False, "kind": "empty", "task_id": None}),
    ({"task": None, "cycle": True, "cycle_tasks": []},
     {"claimable": False, "kind": "cycle", "task_id": None}),
])
def test_classify_next_covers_every_next_task_shape(result, expected):
    assert classify_next(result) == expected


def test_dogfood_review_bucket_of_my_already_reviewed_tasks_is_not_claimable():
    """THE 2026-07-14 dogfood regression, pinned at the source: Queue/Design/Build empty,
    Review holding 25 tasks ALL assigned to the caller, written up at the time as done work
    awaiting a HUMAN's Done move — which is the ONE transition no agent tool can make. The
    hub's old bucket-presence heuristic read that board as "work!" forever — ~144 no-op agent
    boots/day ≈ $105/day for zero work — while the gates themselves offered nothing. The
    exported verdict MUST be claimable=false.

    THE VERDICTS BELOW ARE THIS TEST'S CONSTRUCTION, NOT A MEASUREMENT OF THAT BOARD, and the
    two are worth keeping apart. About the CODE, and checkable: a card in Review carrying a
    verdict is what "awaiting a human's Done move" looks like here — for a normal card, an
    `epic` container being the standing exception, since nobody reviews one at all. That is
    the shape built below. About HISTORY: the same-day write-up in `713bcdf` characterises
    the 25 as done work awaiting that move, and that is the whole of what this tree has —
    no measurement of what the cards actually carried, and the explicit verdict clause was
    back-filled here a month later by the fixture rebuild at `8132e2e`. So this test does not
    claim it, and does not need to: what the incident MEASURED survives either answer — 144
    boots a day did zero work, the hub's guess and the verdict the gates would hand an agent
    being two different things. Asking the gates is the fix either way. (Provenance, and the
    git-log commands with their traps, in docs/dossier/claimable.md.)

    WHICH GUARD HOLDS THIS CHANGED IN #991, AND THE BOARD HAD TO BE BUILT HONESTLY FOR IT.
    Until then the cards here carried a [worklog] and NO verdict, and what filtered them was
    `my_id in assignees` — "you never review your own work". But a card in Review with a
    report and no verdict is, by this product's own definition, a card AWAITING review; the
    old shape was therefore claiming that 25 unreviewed cards were nothing to do, which is
    exactly the defect #991 was filed for (in a solo setup EVERY card in Review is the
    caller's, so review was unreachable and `kind='review'` could never be produced).
    So the [review] comment above is not decoration — it is what "awaiting a human's Done
    move" actually looks like, and the guard that now holds this board quiet is the
    WORKLOG-FRESHNESS one. Non-vacuous either way: delete the freshness check and this
    board goes claimable again (measured).

    The complementary case — the same 25 cards WITHOUT a verdict — is claimable on purpose
    now, and terminates; both are pinned in the two tests below."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    for i in range(25):
        t = api.add_task(f"shipped {i}", "Review", assignee=api.me_user)
        api.add_comment(t["id"], f"[worklog]\nWorklog: shipped {i}\n\nEvidence: sha{i}")
        api.add_comment(t["id"], "[review] APPROVE\nreproduced and checked")
    assert classify_next(wf.next_task()) == {
        "claimable": False, "kind": "empty", "task_id": None,
    }


def test_dogfood_my_own_cards_awaiting_review_are_claimable_as_review():
    """The #991 fix at the exported surface, and the reason `kind='review'` stops being
    unreachable in a solo setup. Same 25 cards, same single identity, no verdicts: this
    board has 25 real reviews owed on it, so the hub SHOULD launch an agent. Before #991
    it read as `kind='empty'` and hgdev-acp never woke anyone for a pending review."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    for i in range(25):
        t = api.add_task(f"shipped {i}", "Review", assignee=api.me_user)
        api.add_comment(t["id"], f"[worklog]\nWorklog: shipped {i}\n\nEvidence: sha{i}")
    out = classify_next(wf.next_task())
    assert (out["claimable"], out["kind"]) == (True, "review"), out


def test_dogfood_the_solo_review_offer_terminates_instead_of_looping():
    """What keeps #991 from re-opening the $105/day hole from the other side. Making a solo
    board claimable is only safe if the work RUNS OUT: an agent that keeps being launched
    against cards it never clears is the 2026-07-14 incident again, just with a different
    kind string. Measured here rather than argued — cast a verdict on whatever is offered
    and the board goes quiet after exactly 25 rounds, one per card, because each verdict
    trips the freshness guard for good.

    THE CONDITION IS THAT VERDICTS ARE ACTUALLY CAST, and that is a RULEBOOK obligation, not
    something this code can enforce: an agent told "your own card is not yours to review"
    would leave every card unfiltered and the loop would never end. That is why #991 rewrote
    the passages in SKILL.md and references/stuck.md that said so."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    for i in range(25):
        t = api.add_task(f"shipped {i}", "Review", assignee=api.me_user)
        api.add_comment(t["id"], f"[worklog]\nWorklog: shipped {i}\n\nEvidence: sha{i}")

    rounds = 0
    while (offer := wf.next_task()).get("task") is not None:
        rounds += 1
        assert rounds <= 25, "the review offer never runs out — the no-op boot loop is back"
        wf.review_task(offer["task"]["id"], verdict="approve", report="checked by running")

    assert rounds == 25
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
    api.add_comment(t["id"], "[worklog]\nWorklog: X\n\nEvidence: sha")
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


def test_the_new_stage_key_leaves_the_exported_kind_untouched():
    """`stage` was added to the free-queue and review-offer results so the rulebook's tick can
    branch on ONE discriminator across every branch (test_workflow_wip pins the payload half).
    `kind` is a CLOSED enum in the hgdev-acp hub, which fail-CLOSES on a value it doesn't know
    and re-resolves @stable within MINUTES of a green push — so a payload edit here that shifted
    a kind would turn every hub loop red before anyone noticed. classify_next reads `stage` only
    inside its resume-truthy branch, so neither new shape can reach it: the free queue is
    resume:False (falls to "queue"), and a review offer matches on `review` BEFORE resume is
    consulted ("review").

    Assert BOTH halves against REAL next_task output: that `stage` is genuinely present, and
    that the verdict is identical to what it was before the key existed. Kind-only assertions
    already exist above and would pass unchanged if `stage` silently vanished — which is the
    other half of this contract, since the rulebook now depends on it."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    free = api.add_task("free work", "Queue")

    result = wf.next_task()
    assert result["stage"] == "Queue", "the free-queue result must carry its stage"
    assert classify_next(result) == {"claimable": True, "kind": "queue", "task_id": free["id"]}

    wf.claim(free["id"])                                   # get it out of the way of the offer
    other = {"id": 77, "username": "agent-other"}
    theirs = api.add_task("their change", "Review", assignee=other)
    api.add_comment(theirs["id"], "[worklog]\nWorklog: X\n\nEvidence: sha")

    result = wf.next_task(exclude=[free["id"]])
    assert result["stage"] == "Review", "the review offer must carry its stage"
    assert classify_next(result) == {"claimable": True, "kind": "review", "task_id": theirs["id"]}


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
    api.add_comment(r["id"], "[worklog]\nWorklog: X\n\nEvidence: sha")

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
    # **_ absorbs event_hooks (the breadcrumb trail, VMCP-85): a FakeAPI has no httpx client
    # and no hooks to fire, so the stub only has to keep accepting the real constructor's call
    monkeypatch.setattr(claimable_cmd, "VikunjaAPI", lambda url, token, **_: api)

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

    def boom(url, token, **_):
        raise RuntimeError(f"connection refused to {url}")

    monkeypatch.setattr(claimable_cmd, "VikunjaAPI", boom)

    assert run_claimable() == 1

    out = capsys.readouterr().out
    assert "super-secret-token" not in out
    assert json.loads(out.strip())["error"] == (
        "RuntimeError: connection refused to https://tracker.example.com"
    )


# --- VMCP-85 (536): the stderr breadcrumb trail -------------------------------------------------
#
# #521 made the MCP SDK import lazy, which incidentally removed `logging.basicConfig(INFO)` from
# this process — and with it the httpx INFO line per tracker call that a WEDGED check used to leave
# behind. The trail below puts that diagnosability back BY DESIGN. What the tests must hold down is
# the pair of properties the frozen cross-repo contract has, and the trail deliberately does NOT:
# stdout stays exactly one JSON line and the exit codes stay 0/1, whatever stderr does.
#
# The board is served over REAL httpx through a MockTransport with the REAL api.py and the REAL
# Workflow, because the hook under test is httpx's own `request` event hook: a FakeAPI has no
# client to fire one. `httpx.Client` is patched (rather than a prebuilt client handed to
# VikunjaAPI) on purpose — `event_hooks` is IGNORED when a caller supplies `client`, so the
# prebuilt route would have tested the test's wiring instead of run_claimable's.


def _board_transport(seen, on_request=None):
    """A minimal live-shaped board: one free task in Queue. Returns (handler, task)."""
    task = {
        "id": 812, "title": "free work", "description": "", "priority": 0, "index": 1,
        "identifier": "VMCP-1", "done": False, "assignees": [], "labels": [],
    }
    views = [{"id": 40, "title": "Kanban", "view_kind": "kanban", "project_id": 3}]
    buckets = [{"id": i + 1, "title": s, "tasks": [task] if s == "Queue" else []}
               for i, s in enumerate(STAGES)]

    def handler(request):
        query = request.url.query.decode()
        seen.append(f"{request.method} {request.url.path}" + (f"?{query}" if query else ""))
        if on_request is not None:
            on_request(request)
        page = int(request.url.params.get("page", 1))
        path = request.url.path
        if path == "/api/v1/info":
            return httpx.Response(200, json={"max_items_per_page": 50})
        if path == "/api/v1/user":
            return httpx.Response(200, json={"id": 1, "username": "agent-me"})
        if path == "/api/v1/projects/3/views":
            return httpx.Response(200, json=views if page == 1 else [])
        if path == "/api/v1/projects/3/views/40/tasks":
            return httpx.Response(200, json=buckets if page == 1 else [])
        if path == f"/api/v1/tasks/{task['id']}":
            return httpx.Response(200, json=task)
        if path.endswith("/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unexpected {path}"})

    return handler, task


def _live_run(monkeypatch, tmp_path, handler, *, token="tok-value", buf=None):
    """Drive run_claimable over `handler`, with stderr swapped for a buffer the caller (and the
    transport, mid-flight) can read. Returns (exit_code, buffer)."""
    monkeypatch.chdir(tmp_path)               # no repo toml — the env layer, as the hub supplies it
    monkeypatch.setenv("VIKUNJA_URL", "https://tracker.example.com")
    monkeypatch.setenv("VIKUNJA_TOKEN", token)
    monkeypatch.setenv("VIKUNJA_PROJECT_ID", "3")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    buf = io.StringIO() if buf is None else buf
    monkeypatch.setattr(sys, "stderr", buf)
    return run_claimable(), buf


def test_the_trail_names_every_request_BEFORE_it_is_sent(monkeypatch, capsys, tmp_path):
    """The design difference from the httpx INFO lines this replaces, and the only reason the
    trail helps a WEDGE at all: the token is written BEFORE the request goes out. Logged after
    the response (as httpx did — its INFO line carries the status code, so it cannot be
    otherwise), the request that never came back is visible only as an absence: you infer where
    it hung from what is missing, and only if you already know the expected sequence.

    So the transport itself is the witness — for every request it handles it snapshots stderr,
    and afterwards each snapshot must ALREADY end with that request's own token. Bracketing is
    asserted too: `cfg/<project>` first (it separates "hung before this code ran" from "hung
    talking to the tracker") and `end/<n>@<elapsed>` plus a NEWLINE last, without which a
    finished run and a run wedged on its last token leave the identical trail."""
    buf, seen, snapshots = io.StringIO(), [], []

    # snapshot stderr as each request is handed to the transport — asserted AFTER the run, never
    # inside the handler: an AssertionError raised in there would be swallowed by run_claimable's
    # catch-all and reported as a mere exit 1, which is not the failure the reader would look for
    def snapshot(request):
        snapshots.append((claimable_cmd._step(request), buf.getvalue()))

    handler, task = _board_transport(seen, on_request=snapshot)
    code, _ = _live_run(monkeypatch, tmp_path, handler, buf=buf)

    assert code == 0, buf.getvalue()
    trail = buf.getvalue()
    assert trail.endswith("\n"), "a finished run terminates its line; a killed one cannot"
    assert trail.count("\n") == 1, "the whole run is ONE line — see the 200-byte budget"
    tokens = trail.split()
    assert tokens[0] == "[claimable]"
    assert tokens[1] == "cfg/3"
    assert tokens[-1].startswith(f"end/{len(seen)}@")
    # one token per request, and the elision is per-request too: `views:1 :2` is two requests
    assert len(tokens) == len(seen) + 3, tokens

    # ...and every one of them was already on stderr when its request left. The snapshots were
    # taken with the buffer the run actually wrote to, so a trail flushed only at exit fails here
    # >= 5 is an anti-vacuum FLOOR, not the count (which moves with the board): the real check
    # pages /info, views, the view's tasks, /user and the offered task, so a fixture that
    # degenerated to a request or two would stop exercising ordering at all. Measured here: 7.
    assert len(snapshots) == len(seen) >= 5
    last = None                        # mirrors the elision rule, so the pin stays exact
    for (name, suffix), snapshot in snapshots:
        token = suffix if name == last and suffix else name + suffix
        assert snapshot.endswith(" " + token), (
            f"{token!r} was not on stderr yet when the request was sent; so far: {snapshot!r}"
        )
        last = name

    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(out) == 1, "stdout IS the machine contract: exactly one JSON line, trail or no trail"
    assert json.loads(out[0]) == {"claimable": True, "kind": "queue", "task_id": task["id"]}


def test_the_whole_trail_fits_the_consumers_200_byte_head_cut(monkeypatch, capsys, tmp_path):
    """THE CONSTRAINT THAT SHAPED THE FORMAT, and it is a measurement in the OTHER repo rather
    than a preference here: hgdev-acp puts the child's stderr on a run row through `detail()`,
    which passes it to `snippet()` — capped at `snippetCap = 200` BYTES, keeping the HEAD
    (internal/hub/vikunja/vikunja.go, read 2026-08-02). That budget is NOT the trail's alone —
    `detail()` is stderr+stdout and uvx's own stderr goes first, so the real room is ~168 B (the
    module docstring derives it); overflow silently costs the TAIL, i.e. the only part that says
    where it hung. The first shape of this feature — one verbose line per request — cost 727 B
    on the live board and would have been cut after four lines.

    This is a HEADROOM test, not a promise: a pathological board can still overflow (the
    docstring says so), and the assertion is that an ordinary run leaves room to spare."""
    seen = []
    handler, _ = _board_transport(seen)
    code, buf = _live_run(monkeypatch, tmp_path, handler)
    capsys.readouterr()

    assert code == 0
    trail = buf.getvalue().encode()
    assert len(seen) >= 5
    assert len(trail) <= 120, f"{len(trail)} B for {len(seen)} requests: {trail!r}"
    # ...and the room left over is what survives a bigger board. Two corrections live in these
    # numbers, both from the audit. (1) The cap is NOT ours alone: detail() is stderr+stdout and
    # the child runs under `uvx`, whose own stderr goes FIRST and so is never the part cut —
    # measured 27 B for a trivial package, 32 B in the hub's own test — so budget against ~168 B.
    # (2) The bar sits well BELOW the measurement, not on it: this fixture gives 71 B / 7
    # requests -> 6.0 B per step -> ~16 steps against that budget, and a bar at 15 would be one
    # longer token away from failing as a puzzle rather than as a finding.
    # WHICH MUTANT THIS BITES, stated exactly, because "drop the compression" was ambiguous
    # enough to be unreproducible: replacing the last-segment NAMING with the whole api/v1-
    # stripped path ("/".join(parts), no id/page split) gives 102 B / 10.4 B per step / 6.3
    # steps -> RED on THIS line. Not stripping `api/v1` either gives 137 B, which trips the size
    # assert above INSTEAD (the audit reported 142 B for its own variant of that mutant — the
    # figure here is the one this fixture produces, re-measured rather than copied). Dropping
    # only the repeat-elision stays GREEN on every assertion in this test — that one is pinned
    # by the ELISION-MIRRORING LOOP in the BEFORE test (`snapshot.endswith(" " + token)`), not
    # by this budget and not by that test's token COUNT, which does not move at all: it is one
    # token per request either way. Measured, because the wrong assertion was named here first.
    budget = 200 - 32                          # snippetCap minus uv's own head-of-buffer noise
    per_step = (len(trail) - len("[claimable] cfg/3 end/0@0.0s\n")) / len(seen)
    assert (budget - len(trail)) / max(per_step, 1) >= 10, (len(trail), per_step)


@pytest.mark.parametrize("method,url,expected", [
    # the five paths a real check walks, measured off the live board
    ("GET", "https://t/api/v1/info", ("info", "")),
    ("GET", "https://t/api/v1/projects/10/views?page=2", ("views", ":2")),
    ("GET", "https://t/api/v1/projects/10/views/40/tasks?page=3", ("tasks", ":3")),
    ("GET", "https://t/api/v1/user", ("user", "")),
    ("GET", "https://t/api/v1/tasks/628", ("tasks", "/628")),
    # ...and the ones it does not, because a generic rule has to answer for them too
    ("GET", "https://t/api/v1/tasks/628/comments", ("comments", "")),
    ("POST", "https://t/api/v1/tasks/628", ("POST tasks", "/628")),
    ("GET", "https://t/api/v1/labels?page=1&per_page=50", ("labels", ":1")),
    ("GET", "https://t/somewhere/else", ("else", "")),
    ("GET", "https://t/", ("/", "")),
    ("GET", "https://t/api/v1/7", ("?", "/7")),
])
def test_step_compresses_a_request_into_a_token(method, url, expected):
    """The compression is generic (last segment; a numeric last segment is an id, so the one
    before it names the step) rather than a table of this module's endpoints — an unfamiliar
    path must still produce SOMETHING rather than raise inside an httpx hook. The two sigils
    are what separate the collision the live board actually contains: `tasks:3` is a page of
    the board read, `tasks/628` is one task. A non-GET keeps its verb; nothing on this path
    sends one today, which is exactly why it is pinned rather than left to be discovered."""
    assert claimable_cmd._step(httpx.Request(method, url)) == expected


def test_the_opt_out_restores_the_old_silence_byte_for_byte(monkeypatch, capsys, tmp_path):
    """VIKUNJA_MCP_NO_TRACE=1 buys back exactly the state #521 left behind — an EMPTY stderr,
    not a quieter one — while stdout and the exit code do not move at all."""
    monkeypatch.setenv(TRACE_OPT_OUT_ENV, "1")
    seen = []
    handler, task = _board_transport(seen)
    code, buf = _live_run(monkeypatch, tmp_path, handler)

    assert code == 0
    assert buf.getvalue() == "", "the opt-out means silent, not tidier"
    assert seen, "and it opted out of the TRAIL, not out of doing the check"
    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(out) == 1
    assert json.loads(out[0]) == {"claimable": True, "kind": "queue", "task_id": task["id"]}


@pytest.mark.parametrize("value,traced", [
    ("1", False), ("true", False), ("YES", False), (" on ", False),
    ("", True), ("0", True), ("no", True), ("false", True),
])
def test_the_opt_out_reads_the_same_spellings_as_the_skill_sync_one(monkeypatch, value, traced):
    """Same truthiness set as setup_cmd's VIKUNJA_MCP_NO_SKILL_SYNC — the value of copying the
    convention is that an operator who learned one env var already knows this one."""
    monkeypatch.setenv(TRACE_OPT_OUT_ENV, value)
    assert claimable_cmd.trace_enabled() is traced


def test_the_trail_never_leaks_the_token(monkeypatch, capsys, tmp_path):
    """The trail prints method + path + query. The token rides in the Authorization HEADER and
    no call here builds a URL out of it — but "we don't print headers" is a property of today's
    code, so pin the outcome. Anti-vacuum: the run must actually have SENT the credential,
    otherwise this passes on a check that never authenticated."""
    seen, auth = [], []
    handler, _ = _board_transport(
        seen, on_request=lambda r: auth.append(r.headers.get("authorization")),
    )
    code, buf = _live_run(monkeypatch, tmp_path, handler, token="super-secret-token")

    assert code == 0
    assert auth and all(h == "Bearer super-secret-token" for h in auth), auth
    assert "super-secret-token" not in buf.getvalue()
    assert "super-secret-token" not in capsys.readouterr().out


def test_the_trail_stays_off_stdout_on_the_failure_lane_too(monkeypatch, capsys, tmp_path):
    """The failure lane is the one the hub reads verbatim (exit 1 + `error` from STDOUT), so the
    trail must not have crept into it — and it still signs off, saying how far it got."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", tmp_path / "nope")
    for var in ("VIKUNJA_URL", "VIKUNJA_TOKEN", "VIKUNJA_PROJECT_ID"):
        monkeypatch.delenv(var, raising=False)

    assert run_claimable() == 1

    captured = capsys.readouterr()
    out = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert len(out) == 1
    assert "[claimable" not in captured.out, "the trail is stderr-only; stdout is the contract"
    assert "ConfigError" in json.loads(out[0])["error"]
    # config never loaded -> no `cfg/` token at all, which is itself the diagnosis
    tokens = captured.err.split()
    assert tokens[0] == "[claimable]" and len(tokens) == 2, captured.err
    assert tokens[1].startswith("fail/0@")
    assert captured.err.endswith("\n"), "a check that RAN and failed still terminates its line"


@pytest.mark.parametrize("break_it", ["stderr", "stderr-newline", "_step"])
def test_a_broken_trail_cannot_break_the_check(monkeypatch, capsys, tmp_path, break_it):
    """THE PRIORITY, stated as a test: the verdict outranks its own breadcrumbs. The trail runs
    inside an httpx event hook, so anything it raises leaves through client.send -> _req ->
    next_task into run_claimable's catch-all and comes out as {"error"} + exit 1 — a hub loop
    failing CLOSED because a pipe was closed or a disk was full.

    Three halves, because the failure has three arrival points: a stderr that raises on EVERY
    write; a stderr that raises only on the TERMINATING NEWLINE (the case that found a real
    hole — close() used to print it unguarded and passed only because _write had already
    flipped `enabled` on an earlier failure, so a stream that failed on that write alone would
    have escaped, and it escapes AFTER the verdict is on stdout, where the hub reads exit != 0
    beside a perfectly good verdict line); and a `_step` that raises on every request.

    An unwritable stderr costs the trail entirely — and costs it ONCE, which is the second half
    of that guard and is asserted below on its own; an unnameable request costs only that token
    (`!`), because the failure is per-request and the count must still be right."""
    class Exploding(io.StringIO):
        def __init__(self, only_newline=False):
            super().__init__()
            self.only_newline = only_newline
            self.attempts = 0        # WRITES ATTEMPTED, not written — see the assertion below

        def write(self, s="", *a, **kw):
            self.attempts += 1
            if not self.only_newline or "\n" in s:
                raise OSError("no space left on device")
            return super().write(s, *a, **kw)

    seen = []
    handler, task = _board_transport(seen)
    if break_it == "_step":
        monkeypatch.setattr(claimable_cmd, "_step", lambda r: 1 / 0)
    buf = io.StringIO() if break_it == "_step" else Exploding(break_it == "stderr-newline")
    code, buf = _live_run(monkeypatch, tmp_path, handler, buf=buf)

    assert code == 0, "a broken trail must not change the exit code"
    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(out) == 1
    assert json.loads(out[0]) == {"claimable": True, "kind": "queue", "task_id": task["id"]}
    assert len(seen) >= 5, "and the check really did run, rather than dying early"
    if break_it == "stderr":
        # THE OTHER HALF OF THE GUARD, and until this line nothing held it down: `except:
        # self.enabled = False` both SWALLOWS the error and DISABLES the trail, and only the
        # swallowing was pinned — before this line existed, mutating that assignment to `pass`
        # left the ENTIRE unit suite green (measured). Nothing else CAN catch it: every other
        # assertion in this case is about stdout and the exit code, and a stderr whose every
        # write raises stays equally empty either way. Attempts are the observable that moves:
        # 1 with the flag flipped, 11 without it on this fixture (one per _emit — the marker,
        # cfg, 7 request tokens, the closing token, the newline). That count is also what makes
        # _emit's "retrying would pay the same cost N times" true.
        assert buf.attempts == 1, (
            "the first write failure must DISABLE the trail, not just be swallowed per token: "
            f"{buf.attempts} writes attempted over {len(seen)} requests"
        )
    if break_it == "_step":
        tokens = buf.getvalue().split()
        assert tokens.count("!") == len(seen), tokens        # every request still left a mark
        assert tokens[-1].startswith(f"end/{len(seen)}@")


def test_a_closed_stderr_never_pushes_the_trail_onto_stdout(tmp_path):
    """THE ONE WAY THE TRAIL COULD BREAK THE CONTRACT, and no guard catches it, because
    nothing raises: with fd 2 closed, `sys.stderr is None`, and `print(..., file=None)` writes
    to sys.STDOUT by documented default. On the single line that spliced the trail INTO the
    verdict — `…tasks/812{"claimable": true,…}` — and since the consumer reads the LAST stdout
    line, it got ` end/7@0.0s`: bad verdict json, fail-CLOSED, at exit 0.

    A subprocess with fd 2 actually closed, not a monkeypatched sentinel, because the thing
    under test is what the INTERPRETER does with a missing fd 2. It has to be closed BEFORE
    exec (preexec_fn), not by the script itself: `os.close(2)` after startup leaves sys.stderr
    a live object over a dead fd, so writes raise EBADF, the guard catches them, and the run
    ends 120 on the shutdown flush — a different bug entirely, and the one this fixture caught
    itself making. hgdev-acp always sets cmd.Stderr, so it never saw this; `2>&-` and a
    daemonized caller reach it in one line."""
    script = textwrap.dedent("""
        import json, sys
        from vikunja_mcp.claimable_cmd import _Trail
        assert sys.stderr is None, "fixture is vacuous: this process HAS a stderr"
        t = _Trail()
        t.note("cfg/3")
        print(json.dumps({"claimable": True, "kind": "queue", "task_id": 812}))
        t.close("end/1@0.0s")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, cwd=tmp_path,
        preexec_fn=lambda: os.close(2),  # noqa: PLW1509 — the point IS the missing fd
    )

    assert proc.returncode == 0, proc.stdout
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"the trail leaked onto stdout: {proc.stdout!r}"
    assert json.loads(lines[-1]) == {"claimable": True, "kind": "queue", "task_id": 812}
    assert "[claimable]" not in proc.stdout


def test_every_token_is_one_whitespace_free_word(monkeypatch, capsys, tmp_path):
    """"One token per request" has to hold by CONSTRUCTION, not by the shapes _step happens to
    emit today. It did not: `_step` deliberately keeps a non-GET's verb (`POST tasks` + `/628`)
    and nothing on this path sends one yet, so the prose and the BEFORE test's
    `len(tokens) == len(seen) + 3` invariant would both have been wrong on the same future day.
    A `page` value carrying a newline is the other half — unreachable through api.py, one line
    away by construction, and it would split the ONE line the whole byte budget rests on."""
    trail = claimable_cmd._Trail()
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)

    trail(httpx.Request("POST", "https://t/api/v1/tasks/628"))
    trail(httpx.Request("GET", "https://t/api/v1/labels?page=%0Aboom"))
    trail.close("end/2@0.0s")

    assert buf.getvalue().count("\n") == 1, "the terminating newline must be the ONLY one"
    tokens = buf.getvalue().split()
    assert tokens == ["[claimable]", "POST_tasks/628", "labels:_boom", "end/2@0.0s"]


def test_a_killed_check_still_says_where_it_was(tmp_path):
    """THE LANE THIS FEATURE EXISTS FOR, and the reason the trail is written straight through
    instead of collected and printed at the end (the card's "one line with the last completed
    operation" reading): the hub kills a wedged check with SIGKILL — no Python cleanup, no
    atexit, no `finally` — so anything still in a buffer dies with the process.

    TWO properties, and the single line makes both load-bearing: the tokens written before the
    kill must already be on the parent's side of the pipe (so `flush=True` matters here, since
    line buffering never triggers on a line that has no newline yet), and the survivor must
    have NO trailing newline — that absence is what distinguishes "killed on this token" from
    "finished". Both go RED if the trail is buffered to the end, and the first also goes RED if
    the flush is dropped."""
    script = textwrap.dedent("""
        import os, signal, httpx
        from vikunja_mcp.claimable_cmd import _Trail
        t = _Trail()
        t.note("cfg/3")
        t(httpx.Request("GET", "https://t.example.com/api/v1/projects/3/views?page=1"))
        os.kill(os.getpid(), signal.SIGKILL)
        t.close("end/1@0.0s")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )

    assert proc.returncode == -signal.SIGKILL, (proc.returncode, proc.stderr)
    assert proc.stderr == "[claimable] cfg/3 views:1"
    assert not proc.stderr.endswith("\n"), "an unterminated line IS the 'it never finished' signal"
    assert proc.stdout == "", "nothing but the verdict may ever reach stdout"


def test_wip_saturation_is_unreachable_for_the_standalone_check():
    """The hub's `kind` enum is CLOSED and it fail-closes on an unknown value, so
    wip_saturated must never reach classify_next. It cannot: the CLI passes no `exclude`,
    so a non-empty active set always returns via the resume branch BEFORE the slot guard.
    This pins that reasoning — if a future edit lets saturation through, the verdict would
    silently degrade to 'empty' and every hub loop would idle on a board that has work."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, wip_limit=1)
    task = api.add_task("held", "Queue")
    wf.claim(task["id"])
    api.add_task("free", "Queue")

    result = wf.next_task()
    assert "wip_saturated" not in result
    assert classify_next(result) == {"claimable": True, "kind": "resume", "task_id": task["id"]}


def test_the_all_excluded_signal_is_unreachable_for_the_standalone_check():
    """Same reasoning as wip_saturation above, and the same reason it is pinned rather than
    argued (#1202): the free-queue branch now honours `exclude`, and the CLI passes NONE, so a
    candidate can never be withheld here. If a future edit let the signal through, the verdict
    would still read 'empty' — correct, since nothing is claimable — but the two facts are
    separate and only this pin says the branch is unreachable at all.

    The CONTROL is the second half: the same board, the same call, with the id excluded by hand
    the way an agent would — that IS the signal, so the first assertion is not passing merely
    because the shape does not exist."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    free = api.add_task("free", "Queue")

    result = wf.next_task()
    assert "all_excluded" not in result
    assert classify_next(result) == {"claimable": True, "kind": "queue", "task_id": free["id"]}

    withheld = wf.next_task(exclude=[free["id"]])
    assert withheld["all_excluded"] is True
    assert classify_next(withheld) == {"claimable": False, "kind": "empty", "task_id": None}


# --- VMCP-295 (1169): require_review_independence reaches the exported verdict -------------------

def _card_in_review_written_by_the_caller() -> tuple[FakeAPI, dict]:
    """One card driven Queue -> Design -> Build -> Review by ONE identity, through the real gates.

    Hand-forging that state would defeat the point of the two tests below: what they separate
    is the board where the only card in Review is the CALLER'S OWN work still awaiting a
    review — precisely the shape next_task's offering loop asks the flag about — and a
    forged card could be the right shape by accident while the gates disagree."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = api.add_task("shipped", "Queue")
    wf.claim(task["id"])
    wf.advance(task["id"], to="build", spec="the plan")
    wf.advance(task["id"], to="review", worklog="the report", evidence="deadbeef")
    return api, task


def _hub_layers(monkeypatch, tmp_path, toml: str) -> None:
    """The hub's own layer-1 env plus a repo toml — the ONLY layer that carries the flag.

    A toml and not an env var, because config.py refuses to read this key from any env layer
    (pinned in test_config: it is committed team policy, like wip_limit). load_config walks UP
    from cwd, so the toml is written into tmp_path and cwd moved there."""
    (tmp_path / ".vikunja-mcp.toml").write_text(toml)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIKUNJA_URL", "https://tracker.example.com")
    monkeypatch.setenv("VIKUNJA_TOKEN", "tok-value")
    monkeypatch.setenv("VIKUNJA_PROJECT_ID", "3")


def test_the_toml_review_independence_flag_reaches_the_exported_verdict(
    monkeypatch, capsys, tmp_path,
):
    """THE PIN: run_claimable builds its OWN Workflow, so a repo policy key that is not passed
    at that construction site is simply not in force here — and since #991 this one is read
    INSIDE next_task's review-offering branch, where it decides whether an own-authored card is
    offered at all. Unwired, the exported verdict says claimable/review on a board the MCP
    server's own next_task calls empty: the hub then boots an agent for a review its tracker
    tools would refuse, which is the class of no-op boot this whole command exists to stop.

    END-TO-END through run_claimable, not classify_next, deliberately: the defect lives at the
    CONSTRUCTION SITE and a unit test of the classifier cannot see it.

    MUTATION-PROVED rather than asserted, selection `tests/unit/test_claimable_cmd.py` with
    `collected 52 items` in both rounds: control 0 failed; deleting
    `require_review_independence=cfg.require_review_independence` from run_claimable -> 1 failed,
    and the one FAILED line names this test. The sibling below stays GREEN under that same
    mutation, which is correct and is why it is a control and not a second pin: it fixes the
    DEFAULT, which an unwired build produces too."""
    api, task = _card_in_review_written_by_the_caller()
    _hub_layers(monkeypatch, tmp_path, (
        '[tracker]\nrequire_review_independence = true\n'
    ))
    monkeypatch.setattr(claimable_cmd, "VikunjaAPI", lambda url, token, **_: api)

    assert claimable_cmd.load_config().require_review_independence is True, \
        "the fixture is vacuous unless the toml this walk-up finds is the one just written"
    assert run_claimable() == 0

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1, "stdout IS the machine contract: exactly one JSON line"
    assert json.loads(lines[0]) == {"claimable": False, "kind": "empty", "task_id": None}
    assert task["id"]  # the card exists; it is the FLAG that keeps it out of the verdict


def test_without_the_flag_that_same_board_is_offered_as_a_review(monkeypatch, capsys, tmp_path):
    """THE CONTROL, and it is what makes the test above a measurement rather than a coincidence:
    same fixture, same identity, same card, one line of toml removed. The default is FALSE, so
    an own-authored card awaiting a review IS claimable here — the #991 behaviour, without which
    an external supervisor would never wake an agent for a pending review in a solo setup.

    Without this pair a broken board (no worklog, an epic label, a wrong project id) would make
    the first test pass for reasons that have nothing to do with the flag."""
    api, task = _card_in_review_written_by_the_caller()
    _hub_layers(monkeypatch, tmp_path, '[tracker]\n')
    monkeypatch.setattr(claimable_cmd, "VikunjaAPI", lambda url, token, **_: api)

    assert claimable_cmd.load_config().require_review_independence is False
    assert run_claimable() == 0

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"claimable": True, "kind": "review", "task_id": task["id"]}
