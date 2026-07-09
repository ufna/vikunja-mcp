import asyncio

import httpx

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import server
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.config import Config, ConfigError
from vikunja_mcp.workflow import STAGES, Workflow


def test_exposes_exactly_the_workflow_tools():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "next_task", "claim", "get_task", "comment",
        "advance", "call_human", "return_task", "decompose", "review_task",
        "file_task", "download_attachment", "attach_file",
    }


def test_tool_errors_are_returned_not_raised(monkeypatch, tmp_path):
    """Без конфига тулза должна вернуть {'error': ...}, а не уронить сервер."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VIKUNJA_TOKEN", raising=False)
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    monkeypatch.delenv("VIKUNJA_PROJECT_ID", raising=False)
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", tmp_path / "nope")
    server._reset_workflow_cache()
    result = server.next_task()
    assert "error" in result


def test_tool_catches_transport_errors_with_hint(monkeypatch):
    """httpx-исключения (сеть/VPN недоступны) не должны ронять сервер сырым traceback'ом."""
    class BoomWorkflow:
        def next_task(self):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(server, "_wf", lambda: BoomWorkflow())
    result = server.next_task()
    assert "error" in result
    assert "tracker unreachable" in result["error"]
    assert "ConnectError" in result["error"]


def test_401_message_owns_both_expired_and_scope_without_the_restart_myth(monkeypatch):
    """tracker #140: verified on real 2.3.0 that Vikunja returns the SAME code-11 401 for an
    invalid/expired token AND for a scope gap (byte-for-byte identical body + headers), so the
    message must OWN BOTH — and must NOT repeat the old, confidently-wrong claim that a restart
    can't help 'because scopes are fixed at mint' (dead wrong for a rotated token). It names both
    required groups, the file to fix, the expired possibility, and preserves the raw server text."""
    class Boom:
        def next_task(self):
            raise VikunjaError(401, '{"code":11,"message":"invalid token"}')

    monkeypatch.setattr(server, "_wf", lambda: Boom())
    monkeypatch.setattr(server, "_reload_workflow_from_disk", lambda: False)  # nothing rotated
    msg = server.next_task()["error"]
    assert "projects:views_buckets" in msg           # owns the scope-gap remedy
    assert "other:user" in msg
    assert "expired" in msg.lower()                  # owns the invalid/expired case too
    assert ".vikunja-mcp.env" in msg                 # points at the file to fix
    assert "restart" in msg.lower()                  # still speaks to the restart instinct
    assert "scopes are fixed" not in msg.lower()     # ...but the confidently-wrong claim is GONE
    assert '{"code":11' in msg                       # raw server body preserved


def test_401_reloads_config_and_retries_once_then_succeeds(monkeypatch):
    """tracker #140 option (б): on a 401 the server reloads .vikunja-mcp.env and retries the SAME
    call once; if the freshly read token works, the rotation is survived with no restart."""
    reloads = {"n": 0}
    state = {"token_ok": False}

    class WF:
        def next_task(self):
            if not state["token_ok"]:
                raise VikunjaError(401, '{"code":11}')
            return {"ok": True}

    def fake_reload():
        reloads["n"] += 1
        state["token_ok"] = True          # the on-disk token is now the fresh, valid one
        return True

    monkeypatch.setattr(server, "_wf", lambda: WF())
    monkeypatch.setattr(server, "_reload_workflow_from_disk", fake_reload)
    assert server.next_task() == {"ok": True}
    assert reloads["n"] == 1               # reloaded exactly once


def test_second_401_after_reload_is_not_retried_again(monkeypatch):
    """The retry is EXACTLY one: a token still rejected after the reload surfaces the guidance,
    it does not reload/retry in a loop."""
    reloads = {"n": 0}
    calls = {"n": 0}

    class WF:
        def next_task(self):
            calls["n"] += 1
            raise VikunjaError(401, '{"code":11,"message":"still bad"}')

    def fake_reload():
        reloads["n"] += 1
        return True

    monkeypatch.setattr(server, "_wf", lambda: WF())
    monkeypatch.setattr(server, "_reload_workflow_from_disk", fake_reload)
    msg = server.next_task()["error"]
    assert reloads["n"] == 1               # reloaded once, never again
    assert calls["n"] == 2                 # original attempt + exactly one retry, no loop
    assert "projects:views_buckets" in msg
    assert "still bad" in msg              # raw text from the SECOND 401 is what surfaced


def test_non_401_errors_never_reload_or_retry(monkeypatch):
    """Only a 401 arms the reload+retry. A 403/404/5xx must not touch config or re-run the call
    (re-running a mutating tool blindly is exactly what we must not do off an ambiguous error)."""
    reloads = {"n": 0}

    def fake_reload():
        reloads["n"] += 1
        return True

    monkeypatch.setattr(server, "_reload_workflow_from_disk", fake_reload)
    for status in (403, 404, 500):
        calls = {"n": 0}

        class WF:
            def next_task(self):
                calls["n"] += 1
                raise VikunjaError(status, "boom")

        monkeypatch.setattr(server, "_wf", lambda: WF())
        server.next_task()
        assert calls["n"] == 1, f"status {status} must not be retried"
    assert reloads["n"] == 0                # reload never even considered for a non-401


@pytest.mark.parametrize(
    "config_error",
    [ConfigError("no token: .vikunja-mcp.env vanished"), OSError("Permission denied")],
    ids=["config-gone", "unreadable-file"],
)
def test_reload_failure_degrades_gracefully_without_crashing(monkeypatch, config_error):
    """tracker #140: if .vikunja-mcp.env is missing / unreadable at reload time, the reload must
    fail SOFT via the REAL _reload_workflow_from_disk — no crash, no retry — and the original 401
    guidance is surfaced. Exercised for both a ConfigError (token gone) and an OSError (file
    unreadable) at load_config time."""
    calls = {"n": 0}

    class WF:
        def next_task(self):
            calls["n"] += 1
            raise VikunjaError(401, '{"code":11}')

    def boom():
        raise config_error

    monkeypatch.setattr(server, "_wf", lambda: WF())
    monkeypatch.setattr(server, "load_config", boom)    # real _reload_workflow_from_disk runs
    result = server.next_task()                          # must not raise
    assert "projects:views_buckets" in result["error"]   # original 401 guidance surfaced
    assert calls["n"] == 1                               # reload failed -> no retry


def test_reload_rebuilds_workflow_with_the_fresh_on_disk_token(monkeypatch):
    """_reload_workflow_from_disk rebuilds the cached Workflow from a fresh config read, so the
    NEW token in .vikunja-mcp.env is the credential used from the retry onward."""
    built = {}
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url="https://t", token="FRESH", project_id=10),
    )
    monkeypatch.setattr(
        server, "VikunjaAPI",
        lambda url, token: built.update(url=url, token=token) or ("api", token),
    )
    monkeypatch.setattr(server, "Workflow", lambda api, pid, enforce_single_wip=False: ("wf", api, pid))
    server._reset_workflow_cache()
    try:
        assert server._reload_workflow_from_disk() is True
        assert built == {"url": "https://t", "token": "FRESH"}   # rebuilt with the fresh token
        assert server._workflow == ("wf", ("api", "FRESH"), 10)  # and cached
        assert server._workflow_token == "FRESH"                 # ...and the token is tracked
    finally:
        server._reset_workflow_cache()      # don't leak the fake Workflow into other tests


# --- tracker #140 rework: the whole-tool retry must NOT duplicate writes on a scope gap ---------
# A tool is several HTTP requests. On a scope-gap 401 (token lacks views_buckets_tasks) the 401
# lands on the kanban MOVE, AFTER an earlier write already succeeded — advance posts [worklog]
# then moves (workflow.py); file_task creates the card then moves. Retrying the WHOLE tool re-runs
# that earlier write, which the reviewer proved on a real container (comment 0->2, card 0->2). The
# guard: retry ONLY when the token freshly read from .vikunja-mcp.env DIFFERS from the one that
# just 401'd — a rotation changes it (recovery lives), a scope gap does not (no retry, no dup).


class _ScopeGapAPI(FakeAPI):
    """A token WITH tasks/comments scope but WITHOUT views_buckets_tasks: every write lands EXCEPT
    the kanban bucket MOVE, which 401s — exactly the scope gap the reviewer used. The move is where
    the 401 surfaces, AFTER advance's [worklog] / file_task's create_task has already written."""

    def move_task(self, *args, **kwargs):
        raise VikunjaError(
            401, '{"code":11,"message":"missing, malformed, expired or otherwise invalid token"}'
        )


def _wire_scope_gap(monkeypatch, workflow):
    """Wire `server` so a 401 reload reads an UNCHANGED token (a scope gap, not a rotation): the
    REAL _reload_workflow_from_disk then returns False and the retry never fires. The SAME setup
    makes the current (pre-guard) code retry — which is what turns these tests RED before the fix."""
    token = "scoped-token-that-never-changes"
    monkeypatch.setattr(server, "_wf", lambda: workflow)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url="https://t", token=token, project_id=workflow.project_id),
    )
    monkeypatch.setattr(server, "_workflow_token", token, raising=False)


def test_scope_gap_401_does_not_duplicate_the_worklog_comment(monkeypatch):
    """advance(to='review') posts [worklog] then moves the bucket; under a scope gap the move 401s.
    The whole-tool retry must NOT re-post the comment. RED before the changed-token guard (it posts
    twice); GREEN after (the unchanged token means no retry)."""
    api = _ScopeGapAPI(buckets=STAGES)
    task = api.add_task("t", "Build", assignee=api.me_user)
    _wire_scope_gap(monkeypatch, Workflow(api, api.project["id"]))

    result = server.advance(task["id"], "review", worklog="did it", evidence="abc123")

    worklogs = [c for c in api.comments_text(task["id"]) if c.startswith("[worklog]")]
    assert len(worklogs) == 1, "scope-gap 401 re-ran advance and DUPLICATED the [worklog] comment"
    assert "projects:views_buckets" in result["error"]   # honest guidance still surfaced


def test_scope_gap_401_does_not_duplicate_the_filed_card(monkeypatch):
    """file_task creates the card then moves it to Backlog; under a scope gap the move 401s. The
    whole-tool retry must NOT create a second card. RED before the guard (two cards); GREEN after."""
    api = _ScopeGapAPI(buckets=STAGES)
    _wire_scope_gap(monkeypatch, Workflow(api, api.project["id"]))

    before = len(api.tasks)
    result = server.file_task("found a leak")

    assert len(api.tasks) - before == 1, "scope-gap 401 re-ran file_task and DUPLICATED the card"
    assert "projects:views_buckets" in result["error"]


def test_reload_returns_false_when_the_on_disk_token_is_unchanged(monkeypatch):
    """The guard proper: an UNCHANGED token (a scope gap — the file was not touched) must NOT
    rebuild or signal a retry. This is what distinguishes the two byte-identical 401s by looking
    at the credential rather than the (indistinguishable) response."""
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url="https://t", token="SAME", project_id=10),
    )
    monkeypatch.setattr(server, "_workflow_token", "SAME", raising=False)
    sentinel = object()
    monkeypatch.setattr(server, "_workflow", sentinel, raising=False)
    assert server._reload_workflow_from_disk() is False   # no rotation -> no retry
    assert server._workflow is sentinel                   # cached Workflow left untouched


def test_403_is_surfaced_as_project_permission_error(monkeypatch):
    """403 is a different remedy than 401: the token is fine but its user lacks
    permission on the project/resource — grant write access, don't touch scopes."""
    class Boom:
        def next_task(self):
            raise VikunjaError(403, "forbidden")

    monkeypatch.setattr(server, "_wf", lambda: Boom())
    msg = server.next_task()["error"]
    assert "403" in msg
    assert "permission" in msg.lower()
    assert "forbidden" in msg                # raw server text preserved


def test_non_auth_vikunja_errors_are_left_untouched(monkeypatch):
    """Only 401/403 get the credential guidance; other statuses keep the terse form."""
    class Boom:
        def next_task(self):
            raise VikunjaError(404, "not found")

    monkeypatch.setattr(server, "_wf", lambda: Boom())
    assert server.next_task()["error"] == "Vikunja API: 404 not found"


def test_version_flag(capsys):
    from vikunja_mcp import __version__

    server.main(argv=["--version"])
    assert __version__ in capsys.readouterr().out


def test_server_self_heals_on_start_before_the_run_loop(monkeypatch):
    """The server refreshes installed agent artifacts on start, and BEFORE the blocking
    stdio run loop — so a `stable` rollout reaches SKILL.md + hook as automatically as code."""
    calls = []
    monkeypatch.setattr(server, "_self_heal_installed_artifacts", lambda: calls.append("heal"))
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append("run"))

    server.main(argv=[])                              # the plain server path (no subcommand)

    assert calls == ["heal", "run"]


def test_self_heal_swallows_errors(monkeypatch):
    """A heal failure must never crash the stdio server — it is wholly best-effort."""
    def boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("vikunja_mcp.setup_cmd.sync_installed_artifacts", boom)
    server._self_heal_installed_artifacts()          # must not raise


def test_self_heal_logs_to_stderr_never_stdout(monkeypatch, capsys):
    """stdout is the MCP protocol channel; a healed-something note must go to stderr only."""
    from pathlib import Path

    monkeypatch.setattr(
        "vikunja_mcp.setup_cmd.sync_installed_artifacts", lambda: [Path("/x/SKILL.md")]
    )
    server._self_heal_installed_artifacts()

    captured = capsys.readouterr()
    assert captured.out == ""                        # never pollute the stdio channel
    assert "refreshed 1" in captured.err             # but do leave a trace on stderr
