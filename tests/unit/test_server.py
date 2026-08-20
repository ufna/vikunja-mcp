import asyncio
import json
import subprocess
import sys

import httpx

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import server
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.config import DEFAULT_LANGUAGE, Config, ConfigError
from vikunja_mcp.workflow import STAGES, Workflow


def test_exposes_exactly_the_workflow_tools():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "next_task", "claim", "get_task", "comment",
        "advance", "call_human", "return_task", "decompose", "review_task",
        "file_task", "download_attachment", "attach_file",
        "handoff", "transfer_task",
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
        def next_task(self, exclude=None):
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
        def next_task(self, exclude=None):
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
        def next_task(self, exclude=None):
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
        def next_task(self, exclude=None):
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
            def next_task(self, exclude=None):
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
        def next_task(self, exclude=None):
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
    monkeypatch.setattr(
        server, "Workflow",
        # mirrors the real Workflow signature — a kwarg missing here makes _build_workflow
        # raise TypeError, which _reload_workflow_from_disk swallows into a silent False
        lambda api, pid, enforce_single_wip=False, notifier=None, wip_limit=None,
        require_review_independence=False, language=DEFAULT_LANGUAGE,
        siblings=None: ("wf", api, pid),
    )
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


def test_file_task_tool_passes_project_id_through(monkeypatch):
    """The MCP tool must thread project_id into the workflow — a param added in workflow.py
    but forgotten in server.py would silently never be exposed to agents."""
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    monkeypatch.setattr(server, "_wf", lambda: Workflow(api, api.project["id"]))
    result = server.file_task("cross-filed", project_id=other["id"])
    assert result["filed"]["project_id"] == other["id"]


def test_file_task_tool_passes_queue_through(monkeypatch):
    """#249: queue=True добавлен в workflow.py — тул обязан прокинуть его насквозь, иначе
    параметр молча не существует для агентов (schema тула генерится из сигнатуры)."""
    api = FakeAPI(buckets=STAGES)
    monkeypatch.setattr(server, "_wf", lambda: Workflow(api, api.project["id"]))
    result = server.file_task("queued by explicit human ask", queue=True)
    assert result["filed"]["stage"] == "Queue"


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


# --- tracker #148: a token rotation must NOT silently REPOINT the session -----------------------
# _reload_workflow_from_disk re-reads the WHOLE config, so before #148 a rotation that ALSO changed
# url/project_id rebuilt onto the OTHER project/host with no error — the next next_task would hand
# back a DIFFERENT project's queue (four agent identities share this config shape on one tracker, so
# a mass re-mint mixing up project_id is a realistic slip). The guard: on a rotation (token changed)
# the reload REFUSES to adopt a changed url or project_id, surfacing an actionable restart error
# instead of silently repointing. An unchanged token (scope gap) and a pure rotation are unaffected.


def _set_session_baseline(monkeypatch, *, token, url, project_id):
    """Pin the in-memory session baseline the repoint guard compares the fresh config against."""
    monkeypatch.setattr(server, "_workflow_token", token, raising=False)
    monkeypatch.setattr(server, "_workflow_url", url, raising=False)
    monkeypatch.setattr(server, "_workflow_project_id", project_id, raising=False)


def test_reload_refuses_a_rotation_that_also_repoints_project_or_host(monkeypatch):
    """Function-level guard: a rotation (token changed) that ALSO moves project_id OR url raises
    ConfigError with an actionable restart message INSTEAD of rebuilding onto the other
    project/host. The cached Workflow and the baseline are left untouched (no silent repoint)."""
    sentinel = object()
    monkeypatch.setattr(server, "_workflow", sentinel, raising=False)
    _set_session_baseline(monkeypatch, token="OLD", url="https://t", project_id=10)

    monkeypatch.setattr(                                   # project_id moved 10 -> 999
        server, "load_config",
        lambda: Config(url="https://t", token="ROTATED", project_id=999),
    )
    with pytest.raises(ConfigError, match="MID-SESSION"):
        server._reload_workflow_from_disk()
    assert server._workflow is sentinel                   # did NOT rebuild
    assert server._workflow_project_id == 10              # baseline intact

    monkeypatch.setattr(                                   # host moved instead
        server, "load_config",
        lambda: Config(url="https://ELSEWHERE", token="ROTATED", project_id=10),
    )
    with pytest.raises(ConfigError, match="MID-SESSION"):
        server._reload_workflow_from_disk()
    assert server._workflow is sentinel
    assert server._workflow_url == "https://t"


def test_401_rotation_that_changes_project_id_refuses_to_repoint(monkeypatch, capsys):
    """Through the real _tool + _reload: a 401 whose reload finds a ROTATED token but a CHANGED
    project_id must surface the restart error and must NOT retry the tool onto the other project's
    queue. RED before #148 (the reload rebuilds and the tool retries -> calls==2, wrong project)."""
    calls = {"n": 0}

    class WF:
        def next_task(self, exclude=None):
            calls["n"] += 1
            raise VikunjaError(401, '{"code":11}')

    monkeypatch.setattr(server, "_wf", lambda: WF())
    _set_session_baseline(monkeypatch, token="OLD", url="https://t", project_id=10)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url="https://t", token="ROTATED", project_id=999),
    )
    msg = server.next_task()["error"]
    assert "mid-session" in msg.lower()          # the repoint refusal, not the generic 401 text
    assert "10" in msg and "999" in msg          # names old -> new project
    assert "restart" in msg.lower()
    assert calls["n"] == 1                        # refused: NOT retried onto project 999
    assert capsys.readouterr().out == ""         # MCP stdio channel stays byte-clean


def test_401_rotation_that_changes_url_refuses_to_repoint(monkeypatch, capsys):
    """Same guard for a changed HOST: a rotation that also moves url must refuse, not repoint to
    another tracker. RED before #148 (rebuild + retry, calls==2)."""
    calls = {"n": 0}

    class WF:
        def next_task(self, exclude=None):
            calls["n"] += 1
            raise VikunjaError(401, '{"code":11}')

    monkeypatch.setattr(server, "_wf", lambda: WF())
    _set_session_baseline(monkeypatch, token="OLD", url="https://t", project_id=10)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url="https://elsewhere.example", token="ROTATED", project_id=10),
    )
    msg = server.next_task()["error"]
    assert "mid-session" in msg.lower()
    assert "elsewhere.example" in msg            # names the new host
    assert "restart" in msg.lower()
    assert calls["n"] == 1
    assert capsys.readouterr().out == ""


def test_401_pure_rotation_same_url_and_project_still_self_heals(monkeypatch, capsys):
    """The rotation path must SURVIVE #148: a 401 whose reload finds a new token but the SAME url +
    project_id still rebuilds and retries once (recovery lives). Driven through the REAL _reload so
    the guard is exercised end-to-end, not stubbed away."""
    monkeypatch.setattr(server, "_workflow", None, raising=False)   # let the real reload write it
    calls = {"n": 0}
    state = {"ok": False}

    class WF:
        def next_task(self, exclude=None):
            calls["n"] += 1
            if not state["ok"]:
                raise VikunjaError(401, '{"code":11}')
            return {"ok": True}

    monkeypatch.setattr(server, "_wf", lambda: WF())
    _set_session_baseline(monkeypatch, token="OLD", url="https://t", project_id=10)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url="https://t", token="ROTATED", project_id=10),   # SAME url + project
    )

    def fake_build(cfg):
        state["ok"] = True                        # once rebuilt on the fresh token, the call works
        return WF()

    monkeypatch.setattr(server, "_build_workflow", fake_build)
    assert server.next_task() == {"ok": True}
    assert calls["n"] == 2                         # original 401 + exactly one retry
    assert server._workflow_token == "ROTATED"     # baseline advanced to the rotated token
    assert capsys.readouterr().out == ""


# --- tracker #154: the repoint guard must compare NORMALIZED urls, not raw strings --------------
# #148 stored and compared the RAW cfg.url, but VikunjaAPI normalizes it (canonical_base_url: strip
# the trailing slash, fold scheme + host CASE). So a rotation whose url differed only COSMETICALLY
# read as a mid-session HOST change and was REFUSED — inverting #148, which exists to stop a silent
# repoint, not to break a healthy token rotation over punctuation. The guard now canonicalizes BOTH
# sides with the SAME helper the client builds requests from, so the two can't drift apart. A
# genuinely different endpoint (http vs https, other host / port / path) must STILL refuse.


@pytest.mark.parametrize(
    "rotated_url",
    [
        "https://tracker.zz.hgdev.com/",        # trailing slash — cosmetic
        "HTTPS://tracker.zz.hgdev.com",         # scheme case — cosmetic (RFC 3986)
        "https://TRACKER.zz.hgdev.com",         # host case — cosmetic (DNS case-insensitive)
    ],
    ids=["trailing-slash", "scheme-case", "host-case"],
)
def test_reload_self_heals_a_rotation_whose_url_differs_only_cosmetically(
    monkeypatch, capsys, rotated_url
):
    """RED before #154: the raw-string compare treats a cosmetic url difference (trailing slash,
    HTTPS-vs-https, host case) as a changed host and REFUSES the rotation (raises the repoint
    ConfigError). After: both sides are canonicalized with the client's own helper, so the rotation
    rebuilds and self-heals like a same-url one — the fresh credential is adopted, not rejected over
    punctuation. stdout stays byte-clean (MCP stdio channel)."""
    rebuilt = object()
    monkeypatch.setattr(server, "_workflow", None, raising=False)
    monkeypatch.setattr(server, "_build_workflow", lambda cfg: rebuilt)
    _set_session_baseline(
        monkeypatch, token="OLD", url="https://tracker.zz.hgdev.com", project_id=10
    )
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url=rotated_url, token="ROTATED", project_id=10),
    )
    try:
        assert server._reload_workflow_from_disk() is True   # rebuilt, NOT refused as a repoint
        assert server._workflow is rebuilt
        assert server._workflow_token == "ROTATED"           # baseline advanced (clean rotation)
        assert capsys.readouterr().out == ""
    finally:
        server._reset_workflow_cache()                        # don't leak the sentinel/baseline


@pytest.mark.parametrize(
    ("baseline_url", "rotated_url"),
    [
        ("https://tracker.zz.hgdev.com?Token=A", "https://TRACKER.zz.hgdev.com?Token=A"),
        ("https://tracker.zz.hgdev.com?Token=A", "HTTPS://tracker.zz.hgdev.com?Token=A"),
        ("https://tracker.zz.hgdev.com#Frag", "https://TRACKER.zz.hgdev.com#Frag"),
        ("https://tracker.zz.hgdev.com:3456?Token=A", "https://TRACKER.zz.hgdev.com:3456?Token=A"),
    ],
    ids=[
        "host-case-folds-with-a-query-present", "scheme-case-folds-with-a-query-present",
        "host-case-folds-with-a-fragment-present", "host-case-folds-behind-a-port-and-a-query",
    ],
)
def test_reload_still_self_heals_a_cosmetic_rotation_when_the_url_carries_a_query(
    monkeypatch, capsys, baseline_url, rotated_url
):
    """The PAIRED half of tracker #706, and the reason the refusal rows above are not enough on
    their own. #706 narrowed what counts as authority; the failure mode of narrowing it is
    narrowing it too far, and a body that folded NOTHING would satisfy every refusal row in this
    file while re-inverting #148 exactly as #154 had to fix once already.

    So these rows go the other way: the query or fragment is IDENTICAL on both sides and only a
    genuinely case-INSENSITIVE part moves, which must still self-heal.

    What that buys, measured rather than predicted. The mutation `cut = 0` (authority always empty,
    so nothing folds at all) leaves EVERY refusal row in this file green — a guard that refuses
    everything refuses those correctly too — while three of the four rows here go red (the
    `scheme-case` row correctly stays green: the scheme is folded outside the authority slice). So
    the refusal rows alone cannot see an over-narrowed authority, which is the direction this test
    exists for. The honest limit on the other side: this test is not the ONLY thing that sees it —
    the pre-existing `test_reload_self_heals_a_rotation_whose_url_differs_only_cosmetically`
    `host-case` row reddens under the same mutation, as do fifteen rows of the api table and the
    httpx test beside it (20 failed in all, against a control of 0 failed). These
    rows are the ones that see it WITH a query or fragment present, which is the shape #706
    touched, and no pre-existing row carries one."""
    rebuilt = object()
    monkeypatch.setattr(server, "_workflow", None, raising=False)
    monkeypatch.setattr(server, "_build_workflow", lambda cfg: rebuilt)
    _set_session_baseline(monkeypatch, token="OLD", url=baseline_url, project_id=10)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url=rotated_url, token="ROTATED", project_id=10),
    )
    try:
        assert server._reload_workflow_from_disk() is True   # rebuilt, NOT refused as a repoint
        assert server._workflow is rebuilt
        assert server._workflow_token == "ROTATED"
        assert capsys.readouterr().out == ""
    finally:
        server._reset_workflow_cache()


@pytest.mark.parametrize(
    "rotated_url",
    [
        "http://tracker.zz.hgdev.com",           # scheme VALUE downgrade to plaintext — REAL
        "https://other.zz.hgdev.com",            # different host — REAL
        "https://tracker.zz.hgdev.com:8443",     # different port — REAL
        "https://tracker.zz.hgdev.com/vikunja",  # different path prefix — REAL
    ],
    ids=["scheme-value-downgrade", "different-host", "different-port", "different-path"],
)
def test_reload_still_refuses_a_rotation_to_a_genuinely_different_endpoint(
    monkeypatch, capsys, rotated_url
):
    """The normalization must not be too PERMISSIVE: folding the trailing slash + scheme/host case
    must still leave a real endpoint change refused, or #148's hole re-opens. An http-vs-https
    plaintext downgrade, a different host, a different port and a different path prefix are all
    genuine repoints — each must raise the mid-session refusal and NOT rebuild onto the new host."""
    sentinel = object()
    monkeypatch.setattr(server, "_workflow", sentinel, raising=False)
    _set_session_baseline(
        monkeypatch, token="OLD", url="https://tracker.zz.hgdev.com", project_id=10
    )
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url=rotated_url, token="ROTATED", project_id=10),
    )
    with pytest.raises(ConfigError, match="MID-SESSION"):
        server._reload_workflow_from_disk()
    assert server._workflow is sentinel                       # did NOT rebuild onto the new host
    assert capsys.readouterr().out == ""


# --- tracker #164: the CASE the url is sensitive to is a repoint too ----------------------------
# The test above varies the url's SUBSTANCE (other scheme value / host / port / path) and leaves
# its CASE alone, so it cannot see a canonicalizer that folds case where case is meaningful — and
# that is the permissive direction, the one #148 exists to close. Measured on 2026-08-02,
# `__pycache__` cleared: control 0 failed; mutating canonical_base_url to lowercase the path
# (`{path}` -> `{path.lower()}`) -> 0 failed over the whole unit suite. These rows are that
# mutation's kill at the level that matters — the guard, not the helper.


@pytest.mark.parametrize(
    ("baseline_url", "rotated_url"),
    [
        ("https://tracker.zz.hgdev.com/Vikunja", "https://tracker.zz.hgdev.com/vikunja"),
        ("https://User@tracker.zz.hgdev.com", "https://user@tracker.zz.hgdev.com"),
        ("https://u:PassWord@tracker.zz.hgdev.com", "https://u:password@tracker.zz.hgdev.com"),
        # tracker #707 — an IPv6 zone id is an OS INTERFACE NAME, not part of the address.
        ("https://[fe80::1%25ETH0]", "https://[fe80::1%25eth0]"),
        ("https://[fe80::1%25ETH0]:3456", "https://[fe80::1%25eth0]:3456"),
        # tracker #706 — a query or fragment written BEFORE any `/` is not authority either.
        ("https://tracker.zz.hgdev.com?Token=A", "https://tracker.zz.hgdev.com?token=a"),
        ("https://tracker.zz.hgdev.com#Frag", "https://tracker.zz.hgdev.com#frag"),
        ("https://tracker.zz.hgdev.com:3456?Token=A", "https://tracker.zz.hgdev.com:3456?token=a"),
        ("https://tracker.zz.hgdev.com?Foo=BAR#Frag", "https://tracker.zz.hgdev.com?foo=bar#frag"),
    ],
    ids=[
        "path-case", "userinfo-user-case", "userinfo-password-case",
        "ipv6-zone-id-case", "ipv6-zone-id-case-with-port",
        "query-case", "fragment-case", "query-case-behind-a-port", "query-and-fragment-case",
    ],
)
def test_reload_still_refuses_a_rotation_that_changes_only_a_CASE_SENSITIVE_part(
    monkeypatch, capsys, baseline_url, rotated_url
):
    """A rotation whose url differs from the running session ONLY in the case of a case-SENSITIVE
    component is a genuine repoint and must be refused, exactly like a different host.

    The path rows are the ones #154's reviewer found unprotected: `/Vikunja` and `/vikunja` are
    different endpoints on a case-sensitive server, so folding them would let a rotation walk the
    session onto another deployment behind the same host. The userinfo rows are the same argument
    about a CREDENTIAL — RFC 3986 6.2.2.1 folds the case of the scheme and a US-ASCII host and of
    nothing else, so userinfo is case-sensitive and two credentials are not one endpoint. Neither
    may rebuild, and stdout stays byte-clean (MCP stdio channel).

    The row that carries the card is `path-case`, and it is not covered by the negative test above
    even though that one has a `different-path` row: there the baseline has NO path at all, so
    lowercasing the path leaves the two strings different anyway and the refusal still fires.
    Measured — under the `path.lower()` mutation the `different-path` row stays GREEN and only this
    row goes red.

    HOW TO READ THE TWO ROUND TABLES BELOW, because they disagree and the disagreement is not a
    contradiction. #707's rounds were run on a tree that did NOT yet carry #706's four rows, and
    #706's were run on a branch that did not yet carry #707's two — this commit is the rebase that
    first put both in one tree, and neither table has been re-measured on it. So every ABSOLUTE
    count below is true of the tree its own table names and of neither the other's nor this one's;
    where the two name the same #164 round they name it at different trees, which is why the
    numbers differ. What DOES survive the merge is each table's qualitative half — which rows are
    red-first, and which mutations kill disjoint sets — because each of those was measured directly
    on the rows it names rather than derived from a total. The one round that WAS re-measured on
    the merged tree is written out in tests/unit/test_api.py's matching paragraph and covers the
    rows here too: same two-file selection, control round 0 failed, the pre-#706 body 15 failed,
    restored 0 failed, with all four `query-*`/`fragment-*` rows of this table among the dead —
    fourteen of the fifteen are named rows across the two files, and the fifteenth is the
    unparametrized httpx-divergence test in test_api.py.

    The `ipv6-zone-id-case` rows are #707's, and they are the same argument a third time. An IPv6
    zone id (`%25` + the id, RFC 6874) is an OS INTERFACE NAME grafted into the syntactic host, and
    interface names are case-sensitive — measured on 2026-08-03 rather than read off the RFC, which
    is silent on the question: `socket.if_nametoindex` raised OSError("no interface with this name")
    for the upper-cased spelling of every interface carrying a lower-case letter, 11 of 11 on Linux
    (python:3.12-alpine) and 26 of 26 on darwin, and `socket.getaddrinfo('::1%lo0')` vs `('::1%LO0')`
    returns scope 1 vs scope 0 — the resolver DROPS an unmatched zone rather than folding its case.
    (No interface index is quoted: a container's veth index moves between runs, so the reproducible
    signal is the OSError and the scope difference, not a number.)
    So `[fe80::1%25ETH0]` and `[fe80::1%25eth0]` are two
    different interfaces, and before #707 this guard ACCEPTED a rotation between them. The ADDRESS's
    hex is the opposite case and has its own test right below — the two must not be pinned together.

    MUTATION-CHECKED for #707 over the whole `tests/unit` selection, `__pycache__` DELETED and
    `PYTHONDONTWRITEBYTECODE=1`, restores verified by sha256 against the pristine file. Two sweeps
    on 2026-08-03, each opening with an unmutated control on the same selection; 921 collected every
    round. Control round: 0 failed.
      * `{path}` -> `{path.lower()}` in canonical_base_url -> 4 failed, the `path-case` row here
        among them. On the PRE-#164 tree, control 0 failed, that mutation was 0 failed
      * fold the authority WHOLE again (`authority.lower()`, the pre-#164 body) -> 13 failed, BOTH
        `userinfo-*` rows here among them, each as `DID NOT RAISE ConfigError` — i.e. that body
        had the guard accepting a CHANGED CREDENTIAL as the same endpoint, which is why those two
        rows were red-first rather than pins of behaviour that was already correct. Both
        `ipv6-zone-id-case*` rows are in that 13 too, for the same reason
      * (#707) `_fold_host` -> `host.lower()`, the pre-#707 body -> 8 failed, BOTH
        `ipv6-zone-id-case*` rows here among them, each as `DID NOT RAISE ConfigError`. Red-first:
        this is the guard accepting a repoint between two different interfaces
      * (#707) the same effect written as a PAIRED edit (helper kept, `zone.lower()` added back) ->
        8 failed, identical set — these rows pin behaviour, not one spelling
    The 6 above was re-measured for #707 and is now 13; it moved because #707 added rows to
    tests/unit/test_api.py, not because anything regressed.

    The `query-*` / `fragment-*` rows are tracker #706 at the level that matters. They are
    RED-FIRST: measured on the pre-#706 tree through this very function, the guard ACCEPTED the
    rotation `https://tracker.zz.hgdev.com?Token=A` -> `?token=a` and rebuilt the workflow onto it,
    because the authority slice ended at the first `/` alone and a query written before any `/`
    was therefore folded with the host. RFC 3986 3.2 ends the authority at the first `/`, `?` or
    `#`. A query string is where a Vikunja url would carry a token if it carried one at all, so
    reading two of them as one endpoint is the same permissive fold as the userinfo rows, not a
    typographic one.

    What the two extra rows do is narrower than it looks, and both were overstated in this
    docstring's first draft before the sweep contradicted it. `query-case-behind-a-port` covers the
    port sitting between the host and the terminator — but measured, it lives and dies with
    `query-case` in every round run for #706, so it is coverage, not an independent pin.
    `query-and-fragment-case` does NOT pin "both terminators are live at once": measured, it stays
    GREEN under both single-terminator mutations, because in the REFUSAL direction any surviving
    case difference keeps the guard refusing, and a url carrying both always keeps one. What pins
    the two halves is the PAIR `query-case` (dies when `?` leaves the terminator set) and
    `fragment-case` (dies when `#` does) — neither alone, and neither is the row that looks like it
    covers both.

    MUTATION-CHECKED for #706 over the whole `tests/unit` selection, `__pycache__` cleared and
    `PYTHONDONTWRITEBYTECODE=1`, restores confirmed by re-running to the control. #164's rounds,
    re-measured for #706 (which renamed the mutated variable and added four rows here, moving both
    counts). Control round: 0 failed.
      * `{tail}` -> `{tail.lower()}` in canonical_base_url — #164's `{path}` round on the renamed
        slice -> 18 failed, FIVE of them rows of this test, `path-case` among them. On the PRE-#164
        tree, control 0 failed, that mutation was 0 failed
      * fold the authority WHOLE again (`authority.lower()`, the pre-#164 body) -> 7 failed, BOTH
        `userinfo-*` rows here among them, each as `DID NOT RAISE ConfigError` — i.e. that body
        had the guard accepting a CHANGED CREDENTIAL as the same endpoint, which is why those two
        rows were red-first rather than pins of behaviour that was already correct

    #706's rounds, same selection and hygiene. Control round: 0 failed.
      * the pre-#706 body (authority ends at `/` alone) -> 14 failed, FOUR of them rows of this
        test and no other row in this file: `query-case`, `fragment-case`,
        `query-case-behind-a-port` and `query-and-fragment-case`, each as `DID NOT RAISE
        ConfigError`. That is the red-first evidence for all four
      * drop `?` from the terminator set -> 9 failed, `query-case` and `query-case-behind-a-port`
        here; drop `#` -> 5 failed, `fragment-case` here. `query-and-fragment-case` survives BOTH,
        which is the measurement behind the paragraph above
      * `cut = len(rest)` (everything is authority, so the path folds too) -> 18 failed, all FIVE
        `#164`+`#706` case rows here at once
    """
    sentinel = object()
    monkeypatch.setattr(server, "_workflow", sentinel, raising=False)
    _set_session_baseline(monkeypatch, token="OLD", url=baseline_url, project_id=10)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url=rotated_url, token="ROTATED", project_id=10),
    )
    with pytest.raises(ConfigError, match="MID-SESSION"):
        server._reload_workflow_from_disk()
    assert server._workflow is sentinel                # did NOT rebuild onto the other endpoint
    assert server._workflow_token == "OLD"             # baseline not advanced by a refused reload
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("baseline_url", "rotated_url"),
    [
        ("https://[FE80::1]", "https://[fe80::1]"),
        ("https://[::FFFF:1]:3456", "https://[::ffff:1]:3456"),
        ("https://[FE80::1%25eth0]", "https://[fe80::1%25eth0]"),
    ],
    ids=["ipv6-hex-case", "ipv6-hex-case-with-port", "ipv6-hex-case-BESIDE-a-zone-id"],
)
def test_reload_self_heals_a_rotation_that_changes_only_the_IPv6_HEX_case(
    monkeypatch, capsys, baseline_url, rotated_url
):
    """#707's PAIRED direction, and the reason it is a separate test rather than another row above.

    #707 stopped the zone id folding. The mistake that fix could have made is to stop folding the
    whole bracketed literal, which would be the STRICT failure: a rotation rewriting only the
    address's hex digits is cosmetic — `FE80::1` and `fe80::1` are the same address (measured:
    `IPv6Address('FE80::1') == IPv6Address('fe80::1')`, and str()s to lowercase; RFC 5952 prefers
    lowercase too) — and refusing it would break a healthy rotation, which is #154's inversion of
    #148 all over again. So the two halves of one literal must go SEPARATE ways, and both ways need
    a pin at the level that matters: the guard. The rows above pin the zone half refusing; these
    pin the hex half still folding. The third row is the one neither test would have caught alone —
    hex and zone in the SAME url, hex changing, zone held constant.

    MUTATION-CHECKED over the whole `tests/unit` selection, `__pycache__` DELETED and then
    `PYTHONDONTWRITEBYTECODE=1`, restores verified by sha256 against the pristine file; 921
    collected every round. Control round: 0 failed.
      * `_fold_host` -> `return host` for a bracketed literal (fold NOTHING inside brackets, the
        over-correction this test exists for) -> 7 failed, all three rows here among them
      * `address.lower()` -> `address` (a narrower way to write the same over-correction) -> 7
        failed, the SAME set. So these tests catch the class but do NOT distinguish the two edits
      * `_fold_host` -> `host.lower()` (the pre-#707 body) -> 8 failed, and these three rows stay
        GREEN — which is what makes them the paired half rather than a duplicate of the rows above
      * `rpartition("@")` -> `partition("@")` -> 10 failed, these rows among them: with no `@` the
        whole authority lands in the userinfo half and nothing folds, hex included
      * identity canonicalizer -> 14 failed, these rows among them
    The first version of this record said 4 for the first round. That number was written from
    expectation BEFORE the sweep ran and was wrong; the measured value is 7.
    """
    rebuilt = object()
    monkeypatch.setattr(server, "_workflow", None, raising=False)
    monkeypatch.setattr(server, "_build_workflow", lambda cfg: rebuilt)
    _set_session_baseline(monkeypatch, token="OLD", url=baseline_url, project_id=10)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url=rotated_url, token="ROTATED", project_id=10),
    )
    try:
        assert server._reload_workflow_from_disk() is True   # rebuilt, NOT refused as a repoint
        assert server._workflow is rebuilt
        assert server._workflow_token == "ROTATED"
        assert capsys.readouterr().out == ""
    finally:
        server._reset_workflow_cache()


def test_401_rotation_with_a_cosmetic_url_change_still_self_heals(monkeypatch, capsys):
    """End-to-end through the REAL _tool + _reload: a 401 whose rotated config differs only by a
    trailing slash on the url must self-heal (rebuild + retry once), NOT surface the repoint refusal.
    RED before #154 (the raw compare raises the refusal -> next_task returns the error, calls==1,
    no recovery). stdout stays byte-clean."""
    monkeypatch.setattr(server, "_workflow", None, raising=False)
    calls = {"n": 0}
    state = {"ok": False}

    class WF:
        def next_task(self, exclude=None):
            calls["n"] += 1
            if not state["ok"]:
                raise VikunjaError(401, '{"code":11}')
            return {"ok": True}

    monkeypatch.setattr(server, "_wf", lambda: WF())
    _set_session_baseline(monkeypatch, token="OLD", url="https://t", project_id=10)
    monkeypatch.setattr(
        server, "load_config",
        lambda: Config(url="https://t/", token="ROTATED", project_id=10),   # trailing slash ONLY
    )

    def fake_build(cfg):
        state["ok"] = True
        return WF()

    monkeypatch.setattr(server, "_build_workflow", fake_build)
    try:
        assert server.next_task() == {"ok": True}      # recovered, not the repoint error
        assert calls["n"] == 2                         # original 401 + exactly one retry
        assert server._workflow_token == "ROTATED"
        assert capsys.readouterr().out == ""
    finally:
        server._reset_workflow_cache()


def test_403_is_surfaced_as_project_permission_error(monkeypatch):
    """403 is a different remedy than 401: the token is fine but its user lacks
    permission on the project/resource — grant write access, don't touch scopes."""
    class Boom:
        def next_task(self, exclude=None):
            raise VikunjaError(403, "forbidden")

    monkeypatch.setattr(server, "_wf", lambda: Boom())
    msg = server.next_task()["error"]
    assert "403" in msg
    assert "permission" in msg.lower()
    assert "forbidden" in msg                # raw server text preserved


def test_non_auth_vikunja_errors_are_left_untouched(monkeypatch):
    """Only 401/403 get the credential guidance; other statuses keep the terse form."""
    class Boom:
        def next_task(self, exclude=None):
            raise VikunjaError(404, "not found")

    monkeypatch.setattr(server, "_wf", lambda: Boom())
    assert server.next_task()["error"] == "Vikunja API: 404 not found"


def test_version_flag(capsys):
    from vikunja_mcp import __version__

    server.main(argv=["--version"])
    assert __version__ in capsys.readouterr().out


def test_main_dispatches_claimable_subcommand(monkeypatch):
    """`vikunja-mcp claimable` exits with run_claimable's code — and does so WITHOUT the
    artifact self-heal or the stdio run loop: the hgdev-acp hub spawns this per poll tick,
    so it must not touch ~/.claude, must not risk heal noise on stderr, and must start fast."""
    calls = []
    monkeypatch.setattr("vikunja_mcp.claimable_cmd.run_claimable", lambda: 3)
    monkeypatch.setattr(server, "_self_heal_installed_artifacts", lambda: calls.append("heal"))
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append("run"))

    with pytest.raises(SystemExit) as exc:
        server.main(["claimable"])

    assert exc.value.code == 3
    assert calls == []


def test_main_dispatches_workspace_subcommand(monkeypatch):
    """`vikunja-mcp workspace` exits with run_workspace's code — and does so WITHOUT the
    artifact self-heal or the stdio run loop, for the same reasons as claimable: the pump
    calls this per task, so it must not touch ~/.claude and must start fast."""
    calls = []
    monkeypatch.setattr("vikunja_mcp.workspace_cmd.run_workspace", lambda argv: 7)
    monkeypatch.setattr(server, "_self_heal_installed_artifacts", lambda: calls.append("heal"))
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append("run"))

    with pytest.raises(SystemExit) as exc:
        server.main(["workspace", "42"])

    assert exc.value.code == 7
    assert calls == []


# --- tracker #521: the MCP SDK must stay OFF every non-MCP CLI path -----------------------------
# server.py used to build MCPServer at module scope, so every subcommand routed through main()
# paid the full SDK import (107 mcp-rooted modules; MCPServer.__init__ also calls
# logging.basicConfig). `claimable` pays it worst — hgdev-acp's loop spawns it per poll tick.
# This has to be probed in a SUBPROCESS: pytest itself imports the SDK long before this module
# runs (the tools/list test above builds the real server), so an in-process `"mcp" in sys.modules`
# assertion could never fail. The probe reports which mcp-rooted modules are loaded after each
# non-MCP path, and after touching server.mcp — the last one must be NON-empty, or the test would
# be passing simply because the SDK is unimportable.
_LAZY_IMPORT_PROBE = """
import asyncio, contextlib, io, json, sys


def mcp_modules():
    return sorted(m for m in sys.modules if m == "mcp" or m.startswith("mcp."))


import vikunja_mcp.server as server
seen = {"import vikunja_mcp.server": mcp_modules()}

# Stub the subcommand bodies (not the dispatch): the paths must be walked for real, but without
# a tracker, a config or a write into ~/.claude. main() re-imports each on entry, so these land.
import vikunja_mcp.claimable_cmd, vikunja_mcp.setup_cmd, vikunja_mcp.workspace_cmd
vikunja_mcp.claimable_cmd.run_claimable = lambda: 0
vikunja_mcp.workspace_cmd.run_workspace = lambda argv: 0
vikunja_mcp.setup_cmd.run_setup = lambda argv: 0
vikunja_mcp.setup_cmd.install_skill = lambda: None

for argv in (["--version"], ["setup"], ["install-skill"], ["claimable"], ["workspace", "1"]):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.suppress(SystemExit):
            server.main(argv=argv)
    seen["main(%r)" % argv] = mcp_modules()

tools = sorted(t.name for t in asyncio.run(server.mcp.list_tools()))
seen["touched server.mcp"] = mcp_modules()
print(json.dumps({"seen": seen, "tools": tools}))
"""


def test_no_non_mcp_cli_path_imports_the_mcp_sdk():
    """#521: --version / setup / install-skill / claimable / workspace must all run with ZERO
    mcp-rooted modules loaded; only touching the server (server.mcp / _server()) may import the
    SDK — and when it does, all 12 tools are still registered by the deferred registry.
    RED if the module-level `from mcp.server import MCPServer` / `mcp = MCPServer(...)` comes back
    (verified by reintroducing it: every path then reports ~107 mcp modules)."""
    probe = subprocess.run(
        [sys.executable, "-c", _LAZY_IMPORT_PROBE],
        capture_output=True, text=True, timeout=180,
    )
    assert probe.returncode == 0, probe.stderr
    data = json.loads(probe.stdout.strip().splitlines()[-1])

    for path, modules in data["seen"].items():
        if path == "touched server.mcp":
            continue
        assert modules == [], f"{path} imported the MCP SDK: {modules[:5]}... ({len(modules)})"
    # The negative assertions above are only meaningful if the SDK CAN be imported at all:
    assert data["seen"]["touched server.mcp"], "the lazy build imported no SDK — probe is vacuous"
    assert len(data["tools"]) == 14, data["tools"]


def test_the_lazy_server_is_a_single_cached_instance():
    """`server.mcp` (module __getattr__) and `_server()` (what main() calls) MUST be the same
    object, built once. If they diverged, main() would run a DIFFERENT server than the one a
    caller configured or a test patched — e.g. the claimable/workspace dispatch tests below patch
    server.mcp.run to prove it is never called."""
    assert server._server() is server.mcp
    assert server.mcp is server.mcp


def test_module_getattr_still_refuses_unknown_attributes():
    """The PEP 562 hook exists only to keep `mcp` lazy — everything else must still AttributeError
    (a hook that quietly returned something for any name would hide typos in tests and callers)."""
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        server.nope


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


def test_build_workflow_wires_notifier_only_when_configured():
    """#252: cfg.notify_webhook set -> the Workflow gets a WebhookNotifier carrying that URL
    and the tracker url (for the frontend deep-link); unset -> notifier is None, so call_human
    behaves bit-for-bit as before the feature existed."""
    from vikunja_mcp.notify import WebhookNotifier

    wired = server._build_workflow(Config(
        url="https://t", token="tk", project_id=10,
        notify_webhook="https://hooks.example/ping",
    ))
    assert isinstance(wired.notifier, WebhookNotifier)
    assert wired.notifier.webhook_url == "https://hooks.example/ping"
    assert wired.notifier.tracker_url == "https://t"

    bare = server._build_workflow(Config(url="https://t", token="tk", project_id=10))
    assert bare.notifier is None
