"""The `search` tool: a read-only keyword lookup over GET /tasks?s=, added because the
package told agents to CHECK for duplicates before file_task while giving them no way to
check (#926 on the desktop tracker). Three layers are pinned separately, because each can
break alone: the API call (wire shape + exhaustive paging), the workflow annotation
(stage/scope/ref composition over the raw hits), and the blank-query refusal (the one
input that would otherwise return EVERY readable task).

The measured server facts these tests encode (live 2.6.0, 2026-09-13): `s=` matches the
WHOLE query as ONE substring over title AND description; the include_done param is
IGNORED on this endpoint, so done-state travels on each hit's `done` field instead; pages
fill until the last because s= filters in SQL. The 2.3.0 container facts are pinned in
tests/integration/test_search_tool.py, where a real server can contradict them.
"""
import inspect
import json

import httpx
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


# --- api.search_tasks: the wire shape -------------------------------------------------


def make_api(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        base_url="https://t.example/api/v1",
        headers={"Authorization": "Bearer tk"},
        transport=transport,
    )
    return VikunjaAPI("https://t.example", "tk", client=client)


def _task(tid, title, description=""):
    return {"id": tid, "title": title, "description": description, "done": False}


def test_search_hits_the_collection_route_with_s_and_pages_exhaustively():
    """/tasks, NOT /tasks/all — the retired path is the whole reason this tool exists (it
    lands on a catch-all that answers 2004 on 2.6.0). Paging must be EXHAUSTIVE at the
    stated page size: a truncated 'no hits' is precisely what gets a duplicate filed, so
    the read only ends when a page comes back short."""
    seen_paths, seen_params = [], []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 2})
        seen_paths.append(request.url.path)
        assert request.url.path.endswith("/tasks"), seen_paths
        seen_params.append(dict(request.url.params))
        page = int(request.url.params["page"])
        pages = {
            1: [_task(1, "alpha needle"), _task(2, "needle two")],
            2: [_task(3, "three", "body mentions the needle")],
            3: [],
        }
        return httpx.Response(200, json=pages[page])

    api = make_api(handler)
    hits = api.search_tasks("needle")
    assert [t["id"] for t in hits] == [1, 2, 3]
    assert all(p.endswith("/tasks") and not p.endswith("/tasks/all") for p in seen_paths)
    assert all(params.get("s") == "needle" for params in seen_params)
    assert [int(p["page"]) for p in seen_params] == [1, 2, 3]


def test_search_tasks_empty_result_is_an_empty_list_not_an_error():
    """The real server answers [] (or a null body) past the end — both must read as 'no
    hits', the answer a duplicate check acts on."""

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        return httpx.Response(200, json=[])

    assert make_api(handler).search_tasks("zzz-none") == []


# --- workflow.search: annotation over the raw hits ------------------------------------


def make_wf():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, language="en")


def test_in_project_hit_carries_ref_stage_and_done():
    api, wf = make_wf()
    card = api.add_task("Fix the widget frobnicator", "Queue")
    out = wf.search("frobnicator")
    assert [hit["id"] for hit in out["results"]] == [card["id"]]
    hit = out["results"][0]
    assert hit["ref"] == f"{card['identifier']} ({card['id']})"
    assert hit["stage"] == "Queue"
    assert hit["done"] is False
    assert hit["project_id"] == 3
    # the query is ALWAYS global — scope states that fact, never where the hits landed
    assert out["scope"] == "all projects readable by this token"


def test_hit_outside_the_tracker_board_has_project_id_but_no_stage():
    """A stage comes from OUR kanban; another project has no board of ours to read. One
    board read annotates every in-project hit — assert the annotation exists at all, since
    a per-hit board read would still produce correct output, just one read per hit."""
    api, wf = make_wf()
    neighbour = api.add_project("neighbour", buckets=STAGES, identifier="NB")
    api.add_task("ours: frobnicator repair", "Build")
    other = api.create_task(neighbour["id"], "theirs: frobnicator cleanup")
    out = wf.search("frobnicator")
    by_id = {hit["id"]: hit for hit in out["results"]}
    assert by_id[other["id"]]["project_id"] == neighbour["id"]
    assert "stage" not in by_id[other["id"]]
    ours = next(hit for hit in out["results"] if hit["project_id"] == 3)
    assert ours["stage"] == "Build"
    assert out["scope"] == "all projects readable by this token"


def test_done_travels_on_the_hit_because_include_done_is_not_a_parameter():
    """Measured on live 2.6.0: /tasks IGNORES the include_done param (a done card returns
    with it sent true OR false), so the tool exposes no such flag — done-state arrives on
    every hit instead, which is what a duplicate check actually needs."""
    api, wf = make_wf()
    card = api.add_task("retire the frobnicator", "Done")
    api.tasks[card["id"]]["done"] = True
    (hit,) = wf.search("frobnicator")["results"]
    assert hit["done"] is True


def test_blank_query_is_refused_before_any_http():
    """The server matches s= as one substring, and an EMPTY s= matches everything — a
    blank query must die locally with the remedy in the message, not return the whole
    readable board."""
    api, wf = make_wf()
    with pytest.raises(WorkflowError) as exc:
        wf.search("   ")
    assert "non-blank" in str(exc.value)


def test_no_hits_say_so_and_name_the_single_substring_semantics():
    """'no hits' is the answer a duplicate check ACTS on, so the empty payload must still
    teach the semantics that would have found the card — a phrase-search miss reads as 'no
    duplicate' when the truth is 'wrong query shape'."""
    api, wf = make_wf()
    api.add_task("split-tunnel dns carve-out", "Backlog")
    out = wf.search("split dns")            # both words present, verbatim substring absent
    assert out["results"] == []
    assert "substring" in out["hint"]


# --- the tool surface -----------------------------------------------------------------


def test_search_joins_the_surface_read_only():
    """A tool that takes no task_id is unaimable, and a read-only lookup must never move a
    card — the Review sweep in test_skill_contract.py pins the movement half on a real
    board; this pins the signature half that keeps `search` out of the pointable sets."""
    import vikunja_mcp.server as server

    fn = next(f for f in server._DEFERRED_TOOLS if f.__name__ == "search")
    params = inspect.signature(fn).parameters
    assert list(params) == ["query"]
    assert "task_id" not in params


def test_search_payload_survives_the_tool_boundary(monkeypatch):
    """server.search returns the workflow's dict verbatim on success — the agent reads
    results/hint straight out of the MCP response, not a re-wrapped shape."""

    class StubWF:
        def search(self, query):
            assert query == "needle"
            return {"query": query, "scope": "project 3", "results": [{"id": 1}], "hint": "h"}

    import vikunja_mcp.server as server

    monkeypatch.setattr(server, "_wf", lambda: StubWF())
    out = server.search("needle")
    assert out["results"] == [{"id": 1}]
    assert json.dumps(out)                  # the payload must be JSON-serialisable as-is
