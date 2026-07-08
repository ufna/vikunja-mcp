import asyncio

import httpx

from vikunja_mcp import server


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


def test_version_flag(capsys):
    from vikunja_mcp import __version__

    server.main(argv=["--version"])
    assert __version__ in capsys.readouterr().out
