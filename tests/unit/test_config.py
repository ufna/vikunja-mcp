import pytest

from vikunja_mcp.config import Config, ConfigError, _parse_env_file, load_config


def _write_toml(path, project_id=3, url="https://tracker.zz.hgdev.com"):
    path.joinpath(".vikunja-mcp.toml").write_text(
        f'[tracker]\nurl = "{url}"\nproject_id = {project_id}\nproject = "hgdev-infra"\n'
    )


def test_reads_repo_toml_and_env_token(tmp_path):
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk_secret"})
    assert cfg == Config(
        url="https://tracker.zz.hgdev.com", token="tk_secret",
        project_id=3, project_name="hgdev-infra",
    )


def test_walks_up_to_find_toml(tmp_path):
    _write_toml(tmp_path)
    deep = tmp_path / "roles" / "vikunja"
    deep.mkdir(parents=True)
    cfg = load_config(cwd=deep, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.project_id == 3


def test_env_overrides_toml(tmp_path):
    _write_toml(tmp_path, project_id=3)
    cfg = load_config(cwd=tmp_path, environ={
        "VIKUNJA_TOKEN": "tk",
        "VIKUNJA_URL": "https://tracker.vpn.hgdev.com",
        "VIKUNJA_PROJECT_ID": "7",
    })
    assert cfg.url == "https://tracker.vpn.hgdev.com"
    assert cfg.project_id == 7


def test_user_env_file_supplies_token(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text("# comment\nVIKUNJA_TOKEN=tk_from_file\n\nOTHER=x\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_from_file"


def test_env_token_beats_user_file(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text("VIKUNJA_TOKEN=file_tk\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "env_tk"})
    assert cfg.token == "env_tk"


def test_missing_token_raises_with_hint(tmp_path):
    _write_toml(tmp_path)
    with pytest.raises(ConfigError, match="VIKUNJA_TOKEN"):
        load_config(cwd=tmp_path, environ={})


def test_missing_toml_and_env_raises(tmp_path):
    with pytest.raises(ConfigError, match="vikunja-mcp.toml"):
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})


def test_env_only_no_toml_works(tmp_path):
    cfg = load_config(cwd=tmp_path, environ={
        "VIKUNJA_TOKEN": "tk", "VIKUNJA_URL": "http://x", "VIKUNJA_PROJECT_ID": "5",
    })
    assert cfg.project_id == 5 and cfg.project_name is None


# --- F4: quotes / inline comments in the user env file ---

def test_env_file_strips_surrounding_double_quotes(tmp_path):
    path = tmp_path / "userenv"
    path.write_text('VIKUNJA_TOKEN="abc"\n')
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc"


def test_env_file_strips_surrounding_single_quotes(tmp_path):
    path = tmp_path / "userenv"
    path.write_text("VIKUNJA_TOKEN='abc'\n")
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc"


def test_env_file_strips_trailing_comment_on_unquoted_value(tmp_path):
    path = tmp_path / "userenv"
    path.write_text("VIKUNJA_TOKEN=abc # note\n")
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc"


def test_env_file_keeps_hash_inside_quotes(tmp_path):
    """Кавычки защищают значение — # внутри них не комментарий."""
    path = tmp_path / "userenv"
    path.write_text('VIKUNJA_TOKEN="abc # not a comment"\n')
    assert _parse_env_file(path)["VIKUNJA_TOKEN"] == "abc # not a comment"


# --- F5: bad VIKUNJA_PROJECT_ID ---

def test_bad_project_id_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="VIKUNJA_PROJECT_ID/project_id must be a number"):
        load_config(cwd=tmp_path, environ={
            "VIKUNJA_TOKEN": "tk", "VIKUNJA_URL": "http://x", "VIKUNJA_PROJECT_ID": "abc",
        })


# --- #39: repo-local .vikunja-mcp.env layer (env > repo-env > repo toml > user file) ---

def _write_repo_env(path, **kv):
    lines = "\n".join(f"{k}={v}" for k, v in kv.items())
    path.joinpath(".vikunja-mcp.env").write_text(lines + "\n")


def test_repo_env_supplies_token_when_user_file_empty(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_repo_env"


def test_env_beats_repo_env(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk_env"})
    assert cfg.token == "tk_env"


def test_repo_env_beats_user_file(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    user_file = tmp_path / "userenv"
    user_file.write_text("VIKUNJA_TOKEN=tk_user\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_repo_env"


def test_repo_env_found_via_walkup_from_subdirectory(tmp_path):
    """Один walk-up (тот же, что ищет toml) — repo-env лежит рядом с найденным toml."""
    _write_toml(tmp_path)
    _write_repo_env(tmp_path, VIKUNJA_TOKEN="tk_repo_env")
    deep = tmp_path / "roles" / "vikunja"
    deep.mkdir(parents=True)
    cfg = load_config(cwd=deep, environ={})
    assert cfg.token == "tk_repo_env"
    assert cfg.project_id == 3


def test_repo_env_quotes_and_trailing_comment(tmp_path):
    """Переиспользует _parse_env_file — те же правила кавычек/# что и у user env file."""
    _write_toml(tmp_path)
    tmp_path.joinpath(".vikunja-mcp.env").write_text(
        'VIKUNJA_TOKEN="tk quoted # not a comment"\n'
    )
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk quoted # not a comment"


def test_repo_env_url_and_project_id_override_toml(tmp_path):
    _write_toml(tmp_path, project_id=3, url="https://tracker.zz.hgdev.com")
    _write_repo_env(
        tmp_path,
        VIKUNJA_URL="https://tracker.override.example",
        VIKUNJA_PROJECT_ID="99",
        VIKUNJA_TOKEN="tk",
    )
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.url == "https://tracker.override.example"
    assert cfg.project_id == 99


def test_repo_env_must_be_beside_toml_not_elsewhere(tmp_path):
    """Не отдельный walk-up: .vikunja-mcp.env в cwd, но не рядом с найденным toml, — игнорируется."""
    _write_toml(tmp_path)
    deep = tmp_path / "roles" / "vikunja"
    deep.mkdir(parents=True)
    _write_repo_env(deep, VIKUNJA_TOKEN="tk_wrong_place")
    with pytest.raises(ConfigError, match="VIKUNJA_TOKEN"):
        load_config(cwd=deep, environ={})


def test_no_repo_env_file_behavior_unchanged(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text("VIKUNJA_TOKEN=tk_from_file\n")
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_from_file"


# --- #252: notify_webhook — Slack-compatible YC ping URL, a secret of the token's class ---

def test_notify_webhook_defaults_none(tmp_path):
    """Absent everywhere -> the feature ships off, no error."""
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.notify_webhook is None


def test_notify_webhook_from_env(tmp_path):
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={
        "VIKUNJA_TOKEN": "tk", "VIKUNJA_NOTIFY_WEBHOOK": "https://hooks.example/env",
    })
    assert cfg.notify_webhook == "https://hooks.example/env"


def test_notify_webhook_from_repo_env(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(
        tmp_path, VIKUNJA_TOKEN="tk", VIKUNJA_NOTIFY_WEBHOOK="https://hooks.example/repo",
    )
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.notify_webhook == "https://hooks.example/repo"


def test_notify_webhook_from_user_env_file(tmp_path, monkeypatch):
    _write_toml(tmp_path)
    user_file = tmp_path / "userenv"
    user_file.write_text(
        "VIKUNJA_TOKEN=tk\nVIKUNJA_NOTIFY_WEBHOOK=https://hooks.example/user\n"
    )
    monkeypatch.setattr("vikunja_mcp.config.USER_ENV_FILE", user_file)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.notify_webhook == "https://hooks.example/user"


def test_notify_webhook_env_beats_repo_env(tmp_path):
    _write_toml(tmp_path)
    _write_repo_env(
        tmp_path, VIKUNJA_TOKEN="tk", VIKUNJA_NOTIFY_WEBHOOK="https://hooks.example/repo",
    )
    cfg = load_config(
        cwd=tmp_path, environ={"VIKUNJA_NOTIFY_WEBHOOK": "https://hooks.example/env"},
    )
    assert cfg.notify_webhook == "https://hooks.example/env"


def test_notify_webhook_never_read_from_toml(tmp_path):
    """Вебхук-URL — секрет того же класса, что и токен (кто держит URL, тот постит в канал
    людей): из КОММИТИМОГО toml он не читается никогда, только из env-слоёв — иначе публичный
    репозиторий с toml утёк бы URL так же, как утёк бы токен."""
    tmp_path.joinpath(".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\n'
        'notify_webhook = "https://hooks.example/leaked"\n'
    )
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.notify_webhook is None


# --- #38: enforce_single_wip policy flag (committed in the toml, default off) ---

def test_enforce_single_wip_defaults_false(tmp_path):
    """Absent from the toml -> the WIP gate ships inert."""
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.enforce_single_wip is False


def test_enforce_single_wip_reads_true_from_toml(tmp_path):
    tmp_path.joinpath(".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nenforce_single_wip = true\n'
    )
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.enforce_single_wip is True
