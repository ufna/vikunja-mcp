"""Tests for setup_cmd: project onboarding and reconciliation."""
from tests.unit.fakes import FakeAPI
from vikunja_mcp.config import DEFAULT_LANGUAGE, LANGUAGES, load_config
from vikunja_mcp.setup_cmd import _print_snippets, reconcile, run_setup
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


def test_snippet_carries_the_language_key_and_load_config_reads_it_back(capsys, tmp_path):
    """`setup --language` puts the choice where the board is made (#1165), and the snippet it
    prints is a toml `load_config` actually accepts.

    Round-tripped rather than string-matched: an assert on the literal line would pass on a
    snippet that is valid-looking and unparseable, and the snippet's whole job is to be pasted
    into a file this package then reads. Every accepted value is exercised, so the set here and
    the set in `config.LANGUAGES` cannot drift apart silently.
    """
    for language in LANGUAGES:
        _print_snippets(pid=42, project_title="voice", url="https://vikunja.example.com",
                        language=language)
        out = capsys.readouterr().out
        # the toml is the block between the header line and the blank line that ends it
        after_header = out.split("--- .vikunja-mcp.toml", 1)[1].split("\n", 1)[1]
        body = after_header.split("\n\n", 1)[0]
        (tmp_path / ".vikunja-mcp.toml").write_text(body + "\n", encoding="utf-8")
        cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "t"})
        assert cfg.language == language
        assert cfg.project_id == 42


def test_the_language_key_is_printed_even_at_its_default(capsys):
    """The snippet is the one place a team SEES the option exists, so it is printed always.

    Omitting it at the default would be tidier output and a worse feature: nobody edits a key
    they have never seen, and the alternative discovery path is reading a rulebook.
    """
    _print_snippets(pid=42, project_title="voice", url="https://vikunja.example.com")
    assert f'language = "{DEFAULT_LANGUAGE}"' in capsys.readouterr().out


def test_setup_refuses_an_unknown_language_before_touching_the_board(capsys):
    """argparse's own refusal (exit 2, the accepted set named), mirroring the ConfigError
    `load_config` raises on the same bad value read out of the file. It fires during parsing, so
    no API client is built and nothing on the board is reconciled — asserted by passing a url
    that would fail loudly if a request were ever attempted."""
    try:
        run_setup(["--project", "x", "--url", "http://127.0.0.1:1", "--language", "de"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("setup accepted --language de")
    assert "invalid choice: 'de'" in capsys.readouterr().err


def test_icebox_is_created_rightmost_and_an_existing_one_is_reused():
    """#1640. `setup` needed no code change — the column rides in on STAGES — so what is pinned
    here is that riding in, in both directions: a board that never had the freezer GAINS it at
    the far right (positions come from `enumerate(STAGES)`, and Done sorting before it is the
    human's choice), and a board that already has one keeps the SAME bucket, tasks and all.

    The reuse half is the one that matters in the field: the dogiators boards this card came
    from already carry a hand-made `Icebox`, so a reconcile that created a second one beside it
    would strand every card a human had already frozen there."""
    api = FakeAPI(buckets=[])
    api.project = {"id": -999, "title": "nothing"}
    reconcile(api, "voice", shares=[])
    titles = bucket_titles(api)
    assert titles[-1] == "Icebox"                    # far right, after Done
    assert titles == STAGES

    frozen = api.add_task("legacy nit a human froze by hand", "Icebox")
    icebox_id = api.bucket_id("Icebox")
    reconcile(api, "voice", shares=[])
    assert api.bucket_id("Icebox") == icebox_id      # reused, not re-created beside the old one
    assert api.stage_of(frozen["id"]) == "Icebox"    # and the frozen card did not move
