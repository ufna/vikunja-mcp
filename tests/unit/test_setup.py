"""Tests for setup_cmd: project onboarding and reconciliation."""
from tests.unit.fakes import FakeAPI
from vikunja_mcp.setup_cmd import _print_snippets, reconcile
from vikunja_mcp.workflow import STAGES


def bucket_titles(api):
    # pass the primary project's real coordinates: the multi-project FakeAPI now dispatches on
    # project_id and 404s an unknown id (as the real server does), so the old bogus (0, 0) — a
    # relic of the fake ignoring project_id — no longer resolves. Mirrors a real caller.
    return [b["title"] for b in api.buckets(api.project["id"], api.view["id"])]


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


def test_call_to_human_bucket_renamed_in_place():
    """Старая колонка 'Call to Human' переименовывается НА МЕСТЕ (тот же bucket id),
    а не пересоздаётся: задачи в ней не осиротеют и колонка не задвоится."""
    old_stages = ["Backlog", "Queue", "Design", "Build", "Review", "Call to Human", "Done"]
    api = FakeAPI(buckets=old_stages)
    parked = api.add_task("ждёт ответа человека", "Call to Human")
    old_bucket_id = api.bucket_id("Call to Human")

    reconcile(api, "hgdev-infra", shares=[])

    titles = bucket_titles(api)
    assert titles == STAGES                              # порядок канонический, 'Your Call' на месте
    assert "Call to Human" not in titles                 # старого имени не осталось
    assert api.stage_of(parked["id"]) == "Your Call"     # задача не потерялась
    assert api.bucket_id("Your Call") == old_bucket_id   # тот же бакет — переименован in-place


def test_reconcile_is_idempotent():
    api = FakeAPI(buckets=[])
    api.project = {"id": -999, "title": "nothing"}
    reconcile(api, "voice", shares=[("agent-voice", 1)])
    ids_before = {b["title"]: b["id"] for b in api.buckets(api.project["id"], api.view["id"])}
    reconcile(api, "voice", shares=[("agent-voice", 1)])
    ids_after = {b["title"]: b["id"] for b in api.buckets(api.project["id"], api.view["id"])}
    assert ids_before == ids_after                   # ничего не пересоздано
    assert len(api.shares) == 1                      # шара не задублирована


def test_unknown_nonempty_bucket_is_kept():
    api = FakeAPI(buckets=["Custom", *STAGES])
    kept = api.add_task("odd", "Custom")
    reconcile(api, "hgdev-infra", shares=[])
    assert "Custom" in bucket_titles(api)            # непустой посторонний бакет не трогаем
    assert api.stage_of(kept["id"]) == "Custom"


def test_print_snippets_includes_opencode_block_without_token(capsys):
    _print_snippets(pid=42, project_title="voice", url="https://vikunja.example.com")
    out = capsys.readouterr().out
    assert ".mcp.json" in out                                    # блок Claude Code на месте
    assert "opencode.json" in out                                # + блок opencode рядом
    assert '"type": "local"' in out                              # opencode local-сервер
    assert '"$schema": "https://opencode.ai/config.json"' in out
    assert "vikunja-mcp@stable" in out                           # stable-канал в обоих блоках
    assert "tk_" not in out                                      # никакого токена в сниппетах
