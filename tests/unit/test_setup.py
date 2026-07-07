"""Tests for setup_cmd: project onboarding and reconciliation."""
from tests.unit.fakes import FakeAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import STAGES


def bucket_titles(api):
    return [b["title"] for b in api.buckets(0, 0)]


def test_fresh_project_gets_canonical_buckets_and_done_config():
    api = FakeAPI(buckets=[])
    api.project = {"id": -999, "title": "nothing"}   # проекта нет -> создание
    pid = reconcile(api, "voice", shares=[("agent-voice", 1)])
    assert pid == api.project["id"]
    assert bucket_titles(api) == STAGES              # авто To-Do/Doing удалены, Done переиспользован
    assert api.view_config["done_bucket_id"] == api.bucket_id("Done")
    assert api.view_config["default_bucket_id"] == api.bucket_id("Backlog")
    assert ("agent-voice") in [u for _, u, _ in api.shares]


def test_existing_project_migrates_old_buckets():
    api = FakeAPI(buckets=["Todo", "Doing", "Review", "Done"])
    t_todo = api.add_task("waiting", "Todo")
    t_doing = api.add_task("wip", "Doing")
    t_review = api.add_task("check me", "Review")
    reconcile(api, "hgdev-infra", shares=[])
    assert bucket_titles(api) == STAGES
    assert api.stage_of(t_todo["id"]) == "Queue"
    assert api.stage_of(t_doing["id"]) == "Build"
    assert api.stage_of(t_review["id"]) == "Review"


def test_reconcile_is_idempotent():
    api = FakeAPI(buckets=[])
    api.project = {"id": -999, "title": "nothing"}
    reconcile(api, "voice", shares=[("agent-voice", 1)])
    ids_before = {b["title"]: b["id"] for b in api.buckets(0, 0)}
    reconcile(api, "voice", shares=[("agent-voice", 1)])
    ids_after = {b["title"]: b["id"] for b in api.buckets(0, 0)}
    assert ids_before == ids_after                   # ничего не пересоздано
    assert len(api.shares) == 1                      # шара не задублирована


def test_unknown_nonempty_bucket_is_kept():
    api = FakeAPI(buckets=["Custom", *STAGES])
    kept = api.add_task("odd", "Custom")
    reconcile(api, "hgdev-infra", shares=[])
    assert "Custom" in bucket_titles(api)            # непустой посторонний бакет не трогаем
    assert api.stage_of(kept["id"]) == "Custom"
