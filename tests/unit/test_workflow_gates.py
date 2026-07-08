import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = api.add_task("job", "Design", assignee=api.me_user)
    return api, wf, task


def test_advance_to_done_is_forbidden(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="human"):
        wf.advance(t["id"], to="done")


def test_advance_unknown_stage(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="invalid"):
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
    with pytest.raises(WorkflowError, match="question"):
        wf.call_human(t["id"], question="")
    wf.call_human(t["id"], question="какой из двух вариантов деплоя выбрать?")
    assert api.stage_of(t["id"]) == "Your Call"
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert any(c.startswith("[нужен человек]") for c in api.comments_text(t["id"]))


def test_return_task_unassigns_labels_and_moves_to_backlog(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="reason"):
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


def test_decompose_partial_failure_reports_created_children(env):
    # A failure on the 2nd create_task (network/429) must not drop a bare VikunjaError:
    # the child created by the 1st call is already on the board, and a blind retry would
    # duplicate it. decompose must raise a WorkflowError that surfaces that partial result.
    api, wf, t = env
    real_create = api.create_task
    calls = {"n": 0}
    created_ids = []

    def flaky_create(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise VikunjaError(429, "rate limited")
        child = real_create(*args, **kwargs)
        created_ids.append(child["id"])
        return child

    api.create_task = flaky_create

    with pytest.raises(WorkflowError) as ei:
        wf.decompose(t["id"], subtasks=[
            {"title": "first child"},
            {"title": "second child"},
        ])
    msg = str(ei.value)
    first_id = created_ids[0]
    # the already-created first child is named by id AND title -> the human/agent can see
    # exactly what leaked instead of blindly retrying
    assert f"#{first_id}" in msg
    assert "first child" in msg
    assert "second child" not in msg  # the 2nd child was never created
    # the partial result really is on the board (in Queue) — not imaginary
    assert first_id in api.tasks
    assert api.stage_of(first_id) == "Queue"
    # the parent is left un-finalized: no epic label, still assigned, not moved to Backlog
    assert not any(lb["title"] == "epic" for lb in api.tasks[t["id"]]["labels"])
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert api.stage_of(t["id"]) == "Design"


def test_decompose_first_child_failure_reraises_bare_error(env):
    # nothing was created yet -> no partial result to report; the bare error is safe to
    # retry, so decompose must NOT wrap it into a misleading "already created" message.
    api, wf, t = env

    def failing_create(*args, **kwargs):
        raise VikunjaError(429, "rate limited")

    api.create_task = failing_create
    with pytest.raises(VikunjaError):
        wf.decompose(t["id"], subtasks=[{"title": "a"}, {"title": "b"}])


def test_file_task_files_finding_into_backlog_with_marker_and_relation(env):
    api, wf, t = env
    # пустой title — отказ
    with pytest.raises(WorkflowError, match="title"):
        wf.file_task(title="   ")
    # находка по ходу работы над t: паркуем в Backlog и связываем с t
    res = wf.file_task(
        title="race in claim self-heal window",
        description="заметил по ходу работы",
        priority=2,
        related_task_id=t["id"],
    )
    new_id = res["filed"]["id"]
    assert new_id != t["id"]
    assert api.stage_of(new_id) == "Backlog"          # Backlog, НЕ Queue — приоритизирует человек
    assert res["filed"]["stage"] == "Backlog"
    assert api.tasks[new_id]["priority"] == 2
    assert any(c.startswith("[filed-by-agent]") for c in api.comments_text(new_id))
    assert (new_id, t["id"], "related") in api.relations
    assert res["related_to"] == t["id"]


def test_file_task_without_relation_has_no_link(env):
    api, wf, t = env
    res = wf.file_task(title="techdebt: refactor config walk-up")
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Backlog"
    assert not any(subj == new_id for subj, _other, _kind in api.relations)
    assert "related_to" not in res
    assert any(c.startswith("[filed-by-agent]") for c in api.comments_text(new_id))


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


def test_ref_composes_human_searchable_identifier():
    """#82: agents must echo the human-searchable ref "<identifier> (<id>)" — exactly the
    "VMCP-27 (82)" shape the human asked for — not the bare, unsearchable global id."""
    assert Workflow._ref({"id": 82, "identifier": "VMCP-27"}) == "VMCP-27 (82)"
    # project with no identifier prefix -> Vikunja returns "#<index>", which we keep
    assert Workflow._ref({"id": 82, "identifier": "#27"}) == "#27 (82)"
    # defensive fallback when identifier is empty/absent -> bare "#<id>"
    assert Workflow._ref({"id": 82, "identifier": ""}) == "#82"
    assert Workflow._ref({"id": 82}) == "#82"


def test_get_task_surfaces_searchable_ref(env):
    """get_task dossier carries the human-searchable ref alongside the raw id."""
    api, wf, t = env
    dossier = wf.get_task(t["id"])
    assert dossier["ref"] == f"{api.tasks[t['id']]['identifier']} ({t['id']})"
    assert dossier["ref"].startswith("HGI-") and dossier["ref"].endswith(f"({t['id']})")


def test_review_flow_for_bug_labels(env):
    api, wf, t = env
    # довели багфикс до Review
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e")

    # имплементеру (assignee) ревью НЕ предлагается
    assert "review" not in wf.next_task()

    # свободному агенту — предлагается
    api2 = api  # тот же борд, другой "я"
    reviewer = type(wf)(api2, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]

    # пустой report / кривой verdict / не-Review задача — отказ
    import pytest as _pytest
    with _pytest.raises(WorkflowError):
        reviewer.review_task(t["id"], verdict="approve", report="  ")
    with _pytest.raises(WorkflowError):
        reviewer.review_task(t["id"], verdict="lgtm", report="r")

    # needs_work: вердикт-коммент + возврат в Build, assignee сохранён
    reviewer.review_task(t["id"], verdict="needs_work", report="фикс лечит симптом")
    assert api.stage_of(t["id"]) == "Build"
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert any(c.startswith("[review] NEEDS WORK") for c in api.comments_text(t["id"]))

    # после вердикта задача больше не предлагается на ревью (вернулась в Build);
    # доводим снова и апрувим
    wf.advance(t["id"], to="review", worklog="w2", evidence="e2")
    reviewer.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    assert api.stage_of(t["id"]) == "Review"
    assert any(c.startswith("[review] APPROVE") for c in api.comments_text(t["id"]))
    # свежий APPROVE (новее последнего worklog) закрывает ревью — задача не предлагается
    res = reviewer.next_task()
    assert not res.get("review"), res


def test_review_not_offered_without_bug_label(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e")
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    res = reviewer.next_task()
    assert "review" not in res


def test_review_reoffered_after_needs_work_rework(env):
    """Цикл: needs_work -> доработка -> Review снова -> задача ОПЯТЬ предлагается на ревью."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1")

    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    assert reviewer.next_task().get("review") is True

    reviewer.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert not reviewer.next_task().get("review")          # в Build — ревьюить нечего

    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2")
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]  # re-offer!

    reviewer.review_task(t["id"], verdict="approve", report="теперь по причине")
    assert not reviewer.next_task().get("review")          # свежий вердикт закрыл цикл


def _label_titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]


def _to_review(wf, task_id):
    wf.advance(task_id, to="build", spec="s")
    return wf.advance(task_id, to="review", worklog="w", evidence="e")


def test_review_approve_adds_reviewed_strips_review_failed(env):
    """approve вешает reviewed и снимает review-failed (взаимоисключающие вердикт-метки)."""
    api, wf, t = env
    _to_review(wf, t["id"])
    # на момент апрува на задаче ещё висит review-failed (belt-and-suspenders на всякий)
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "review-failed"})
    wf.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    titles = _label_titles(api, t["id"])
    assert "reviewed" in titles
    assert "review-failed" not in titles
    assert api.stage_of(t["id"]) == "Review"  # апрув оставляет задачу в Review для человека


def test_review_needs_work_adds_review_failed_strips_reviewed(env):
    """needs_work вешает review-failed и снимает reviewed."""
    api, wf, t = env
    _to_review(wf, t["id"])
    # на момент needs_work на задаче висит reviewed (например, была одобрена и переоткрыта)
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "reviewed"})
    wf.review_task(t["id"], verdict="needs_work", report="фикс лечит симптом")
    titles = _label_titles(api, t["id"])
    assert "review-failed" in titles
    assert "reviewed" not in titles
    assert api.stage_of(t["id"]) == "Build"  # needs_work возвращает задачу в Build


def test_advance_review_resubmit_strips_review_failed(env):
    """Ресабмит в Review (после needs_work) снимает review-failed — reset вердикта."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1")
    wf.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert "review-failed" in _label_titles(api, t["id"])  # needs_work повесил
    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2")
    assert "review-failed" not in _label_titles(api, t["id"])  # ресабмит снял


def test_advance_review_first_submit_no_review_failed_label(env):
    """Первый сабмит в Review: review-failed нет — снятие это no-op, метка НЕ добавляется."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e")  # не падает
    assert "review-failed" not in _label_titles(api, t["id"])
    assert api.stage_of(t["id"]) == "Review"


def test_advance_review_bug_returns_review_needed_note(env):
    """advance(to='review') на баге отдаёт review_needed=True + подсказку про push-ревью."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    res = _to_review(wf, t["id"])
    assert res["review_needed"] is True
    assert res.get("note")


def test_advance_review_non_bug_no_review_needed(env):
    """На не-баге ничего лишнего в payload нет — только moved_to/task_id."""
    api, wf, t = env
    res = _to_review(wf, t["id"])
    assert "review_needed" not in res
    assert res == {"moved_to": "Review", "task_id": t["id"]}


def test_fake_remove_label_idempotent_and_mirrors_client(env):
    """FakeAPI.remove_label зеркалит клиент и идемпотентен (отсутствующий id — no-op)."""
    api, wf, t = env
    lb = api.get_or_create_label("reviewed")
    api.add_label(t["id"], lb["id"])
    assert "reviewed" in _label_titles(api, t["id"])
    api.remove_label(t["id"], lb["id"])
    assert "reviewed" not in _label_titles(api, t["id"])
    # повторное снятие того же id и снятие никогда не висевшего id — без ошибки
    api.remove_label(t["id"], lb["id"])
    api.remove_label(t["id"], 123456)
    assert "reviewed" not in _label_titles(api, t["id"])
