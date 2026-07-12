"""Контракт мульти-проектного FakeAPI (кросс-проектный file_task). Фейк обязан РАЗЛИЧАТЬ
проекты: до этого каждый project-scoped метод игнорировал project_id, и workflow-баг,
двигающий задачу координатами ЧУЖОЙ доски, был невидим юнитам — ровно #125-режим
«фейк щедрее сервера». Эти тесты — растяжки, на которые опираются кросс-тесты workflow."""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import STAGES


def test_second_project_has_its_own_view_and_disjoint_buckets():
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    assert other["id"] != api.project["id"]
    other_view = api.kanban_view(other["id"])
    assert other_view["id"] != api.view["id"]
    own_ids = {b["id"] for b in api.buckets(api.project["id"], api.view["id"])}
    other_ids = {b["id"] for b in api.buckets(other["id"], other_view["id"])}
    assert own_ids.isdisjoint(other_ids)
    # primary state untouched — existing single-project tests see zero change
    assert api.kanban_view(api.project["id"])["id"] == api.view["id"]


def test_create_task_lands_in_the_target_projects_default_bucket():
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=["Inbox", *STAGES])
    t = api.create_task(other["id"], "filed elsewhere")
    other_view = api.kanban_view(other["id"])
    inbox = next(
        b for b in api.buckets(other["id"], other_view["id"]) if b["title"] == "Inbox"
    )
    assert api.task_bucket[t["id"]] == inbox["id"]   # ЦЕЛЕВОЙ дефолт-бакет, не свой
    assert api.stage_of(t["id"]) == "Inbox"          # stage_of видит чужие доски


def test_move_task_refuses_a_bucket_of_another_projects_view():
    # РАСТЯЖКА: workflow, передавший координаты СВОЕЙ доски для задачи в чужом
    # проекте, обязан здесь упасть — как реальный сервер (bucket не на том view -> 404).
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    t = api.create_task(other["id"], "x")
    own_backlog = api.bucket_id("Backlog")           # бакет ПЕРВИЧНОГО проекта
    with pytest.raises(VikunjaError) as err:
        api.move_task(other["id"], api.kanban_view(other["id"])["id"], own_backlog, t["id"])
    assert err.value.status == 404


def test_unknown_project_404s_and_forbidden_project_403s():
    api = FakeAPI(buckets=STAGES)
    secret = api.add_project("secret", buckets=STAGES, forbidden=True)
    with pytest.raises(VikunjaError) as e403:
        api.kanban_view(secret["id"])
    assert e403.value.status == 403                  # есть, но токену не расшарен
    with pytest.raises(VikunjaError) as e404:
        api.kanban_view(999999)
    assert e404.value.status == 404                  # не существует вовсе
    assert all(p["id"] != secret["id"] for p in api.projects())  # и в листинге его нет
