import asyncio

import httpx

from vikunja_mcp import server
from vikunja_mcp.api import VikunjaError


def test_exposes_exactly_the_workflow_tools():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "next_task", "claim", "get_task", "comment",
        "advance", "call_human", "return_task", "decompose", "review_task",
        "file_task",
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


def test_401_is_surfaced_as_actionable_scope_error(monkeypatch):
    """A bare 'Vikunja API 401' reads like a session hiccup and invites a pointless
    /mcp reconnect or server restart. Real incident: a token missing the
    `projects:views_buckets` group let reads + comment through but 401'd every stage
    transition (they move kanban buckets), and the agent misdiagnosed it as a 'stuck
    credential → restart the server'. The tool must name the scope and kill that
    instinct — a token's permissions are fixed at mint time, so only a re-mint helps."""
    class Boom:
        def next_task(self):
            raise VikunjaError(401, "missing permission")

    monkeypatch.setattr(server, "_wf", lambda: Boom())
    msg = server.next_task()["error"]
    assert "projects:views_buckets" in msg   # names the group that gates transitions
    assert "restart" in msg.lower()          # ... and explicitly kills the restart instinct
    assert "mint" in msg.lower()             # ... points at the real remedy (re-mint the token)
    assert "missing permission" in msg       # ... while preserving the raw server text


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
