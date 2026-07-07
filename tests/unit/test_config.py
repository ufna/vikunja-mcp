import pytest

from vikunja_mcp.config import Config, ConfigError, load_config


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
