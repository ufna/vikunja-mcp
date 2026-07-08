import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = api.add_task("job", "Design", assignee=api.me_user)
    return api, wf, task


def test_advance_to_done_is_forbidden(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="человек"):
        wf.advance(t["id"], to="done")


def test_advance_unknown_stage(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="недопустимый"):
        wf.advance(t["id"], to="review2")


def test_advance_build_requires_spec(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="spec"):
        wf.advance(t["id"], to="build", spec="   ")
    wf.advance(t["id"], to="build", spec="сделаю X через Y")
    assert api.stage_of(t["id"]) == "Build"
    assert any(c.startswith("[spec]") for c in api.comments_text(t["id"]))


def test_advance_review_requires_worklog_and_evidence(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError, match="worklog"):
        wf.advance(t["id"], to="review", worklog="сделано")
    wf.advance(t["id"], to="review", worklog="сделано", evidence="commit abc123")
    assert api.stage_of(t["id"]) == "Review"
    joined = "\n".join(api.comments_text(t["id"]))
    assert "[worklog]" in joined and "commit abc123" in joined


def test_advance_review_report_includes_root_cause(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(
        t["id"], to="review",
        worklog="починил рендер титула", evidence="commit deadbeef",
        root_cause="стейт лобби не подписан на смену экипировки",
    )
    report = next(c for c in api.comments_text(t["id"]) if c.startswith("[worklog]"))
    assert "Причина: стейт лобби не подписан" in report
    assert "Сделано: починил рендер титула" in report
    assert "Evidence: commit deadbeef" in report


def test_advance_wrong_source_stage(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="Build"):
        wf.advance(t["id"], to="review", worklog="w", evidence="e")  # задача ещё в Design


def test_advance_requires_ownership(env):
    api, wf, t = env
    api.tasks[t["id"]]["assignees"] = [{"id": 9, "username": "other"}]
    with pytest.raises(WorkflowError, match="claim"):
        wf.advance(t["id"], to="build", spec="s")


def test_call_human_keeps_assignee(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="вопрос"):
        wf.call_human(t["id"], question="")
    wf.call_human(t["id"], question="какой из двух вариантов деплоя выбрать?")
    assert api.stage_of(t["id"]) == "Call to Human"
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert any(c.startswith("[нужен человек]") for c in api.comments_text(t["id"]))


def test_return_task_unassigns_labels_and_moves_to_backlog(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="причин"):
        wf.return_task(t["id"], reason="")
    wf.return_task(t["id"], reason="нужен доступ к prod-базе")
    assert api.stage_of(t["id"]) == "Backlog"
    assert api.tasks[t["id"]]["assignees"] == []
    assert any(lb["title"] == "blocked" for lb in api.tasks[t["id"]]["labels"])
    assert any(c.startswith("[blocked]") for c in api.comments_text(t["id"]))


def test_decompose_creates_children_in_queue_parent_epic(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="2"):
        wf.decompose(t["id"], subtasks=[{"title": "one"}])
    res = wf.decompose(t["id"], subtasks=[
        {"title": "step 1", "description": "d1", "priority": 3},
        {"title": "step 2"},
    ])
    assert len(res["created"]) == 2
    for child in res["created"]:
        assert api.stage_of(child["id"]) == "Queue"
        assert (child["id"], t["id"], "parenttask") in api.relations
    assert api.stage_of(t["id"]) == "Backlog"
    assert api.tasks[t["id"]]["assignees"] == []
    assert any(lb["title"] == "epic" for lb in api.tasks[t["id"]]["labels"])
    assert any(c.startswith("[decompose]") for c in api.comments_text(t["id"]))


def test_comment_and_get_task(env):
    api, wf, t = env
    with pytest.raises(WorkflowError):
        wf.comment(t["id"], text=" ")
    wf.comment(t["id"], text="нашёл гочу в API")
    dossier = wf.get_task(t["id"])
    assert dossier["stage"] == "Design"
    assert dossier["comments"][-1]["text"] == "нашёл гочу в API"
    assert dossier["assignees"] == ["agent-infra"]


def test_get_task_returns_untruncated_description_and_related(env):
    """F3: get_task — полное досье, а не урезанная _summary (500 символов, без related)."""
    api, wf, t = env
    long_description = "х" * 600
    api.tasks[t["id"]]["description"] = long_description
    parent = api.add_task("epic", "Backlog")
    api.add_relation(t["id"], parent["id"], "parenttask")

    dossier = wf.get_task(t["id"])
    assert dossier["description"] == long_description
    assert len(dossier["description"]) > 500
    assert dossier["related"] == {
        "parenttask": [{"id": parent["id"], "title": "epic"}],
    }


def test_get_task_related_defaults_to_empty_dict_without_relations(env):
    api, wf, t = env
    dossier = wf.get_task(t["id"])
    assert dossier["related"] == {}
