import pytest


@pytest.fixture(autouse=True)
def isolated_user_env_file(tmp_path, monkeypatch):
    """Юнит-тесты не должны видеть настоящий ~/.config/vikunja-mcp/env."""
    monkeypatch.setattr(
        "vikunja_mcp.config.USER_ENV_FILE", tmp_path / "user-env-absent"
    )
