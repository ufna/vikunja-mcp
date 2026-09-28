"""`file_task` requires `breaks` — what goes wrong if the finding is never fixed (tracker #1987).

An agent's own finding is filed only when something BREAKS. A finding where nothing does (a
wording, a stale figure, a claim wider than its measurement) is dropped, not filed anywhere —
Icebox included. `queue=True` is exempt: a human asked for that card, which is justification
enough. The sentence is rendered at the top of the description, where the human triaging it
reads first.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import server
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    return api, wf


@pytest.mark.parametrize("dest", [{}, {"icebox": True}, {"cross": True}])
@pytest.mark.parametrize("breaks", ["", "   "])
def test_an_own_finding_without_breaks_is_refused_creating_nothing(env, dest, breaks):
    api, wf = env
    kwargs = dict(dest)
    if kwargs.pop("cross", False):
        kwargs["project_id"] = api.add_project("neighbour", buckets=STAGES)["id"]
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="breaks") as refusal:
        wf.file_task(title="a finding", breaks=breaks, **kwargs)
    assert len(api.tasks) == before
    assert "drop" in str(refusal.value), "the refusal must say what to do instead: drop it"


def test_a_human_asked_card_needs_no_breaks(env):
    api, wf = env
    res = wf.file_task(title="work a human asked for", queue=True)
    assert api.stage_of(res["filed"]["id"]) == "Queue"


def test_breaks_opens_the_description(env):
    api, wf = env
    res = wf.file_task(
        title="a finding", description="details", breaks="  claim 403s for every agent  "
    )
    assert api.tasks[res["filed"]["id"]]["description"] == (
        "What breaks: claim 403s for every agent\n\ndetails"
    )


def test_breaks_alone_is_the_whole_description(env):
    api, wf = env
    res = wf.file_task(title="a finding", breaks="the gc reaps a live tree")
    assert api.tasks[res["filed"]["id"]]["description"] == "What breaks: the gc reaps a live tree"


def test_breaks_follows_the_project_language():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, language="ru")
    res = wf.file_task(title="находка", breaks="claim отдаёт 403")
    assert api.tasks[res["filed"]["id"]]["description"] == "Что сломается: claim отдаёт 403"


def test_a_queued_card_given_breaks_still_renders_it(env):
    api, wf = env
    res = wf.file_task(title="asked for", queue=True, breaks="deploys fail")
    assert api.tasks[res["filed"]["id"]]["description"] == "What breaks: deploys fail"


def test_the_tool_passes_breaks_through(monkeypatch):
    api = FakeAPI(buckets=STAGES)
    monkeypatch.setattr(server, "_wf", lambda: Workflow(api, api.project["id"]))
    refused = server.file_task("no impact named")
    assert "breaks" in refused["error"]
    filed = server.file_task("a finding", breaks="the hub boots a no-op agent")
    assert api.tasks[filed["filed"]["id"]]["description"].startswith("What breaks: the hub")
