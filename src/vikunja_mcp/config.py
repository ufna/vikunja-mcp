"""Config resolution: env > repo .vikunja-mcp.env (repo-local, beside toml) >
repo .vikunja-mcp.toml (walk-up) > ~/.config/vikunja-mcp/env."""
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ENV_URL = "VIKUNJA_URL"
ENV_TOKEN = "VIKUNJA_TOKEN"
ENV_PROJECT_ID = "VIKUNJA_PROJECT_ID"
REPO_FILE = ".vikunja-mcp.toml"
REPO_ENV_FILE = ".vikunja-mcp.env"
USER_ENV_FILE = Path("~/.config/vikunja-mcp/env").expanduser()


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    project_id: int
    project_name: str | None = None
    # committed team policy (read ONLY from the repo toml, not env/secret): when true,
    # claim() refuses a new task while you already have an active Design/Build one.
    # Default off -> ships inert and reversible; opt in per team.
    enforce_single_wip: bool = False


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]              # кавычки защищают значение — # внутри не комментарий
        else:
            value = value.split(" #", 1)[0].rstrip()   # только у НЕзакавыченных значений
        out[key.strip()] = value
    return out


def _find_repo_toml(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        p = candidate / REPO_FILE
        if p.is_file():
            return p
    return None


def load_config(cwd: Path | None = None, environ: Mapping[str, str] | None = None) -> Config:
    import os

    env = dict(environ) if environ is not None else dict(os.environ)
    user = _parse_env_file(USER_ENV_FILE)

    repo: dict = {}
    repo_env: dict[str, str] = {}
    toml_path = _find_repo_toml(cwd or Path.cwd())
    if toml_path is not None:
        repo = tomllib.loads(toml_path.read_text()).get("tracker", {})
        # repo-local .env лежит СТРОГО рядом с найденным toml — отдельного walk-up
        # для него нет, это одна и та же директория (предсказуемо, без сюрпризов)
        repo_env = _parse_env_file(toml_path.parent / REPO_ENV_FILE)

    url = env.get(ENV_URL) or repo_env.get(ENV_URL) or repo.get("url") or user.get(ENV_URL)
    token = env.get(ENV_TOKEN) or repo_env.get(ENV_TOKEN) or user.get(ENV_TOKEN)
    raw_pid = (
        env.get(ENV_PROJECT_ID)
        or repo_env.get(ENV_PROJECT_ID)
        or repo.get("project_id")
        or user.get(ENV_PROJECT_ID)
    )

    if not url or raw_pid is None:
        raise ConfigError(
            f"{REPO_FILE} with [tracker] url/project_id not found (searched from "
            f"{cwd or Path.cwd()} upward) and no {ENV_URL}/{ENV_PROJECT_ID} in env"
        )
    if not token:
        raise ConfigError(
            f"no token: put VIKUNJA_TOKEN=... in {REPO_ENV_FILE} next to {REPO_FILE}, "
            f"in {USER_ENV_FILE} (chmod 600), or pass it via env {ENV_TOKEN}"
        )
    try:
        project_id = int(raw_pid)
    except (TypeError, ValueError):
        raise ConfigError(
            f"VIKUNJA_PROJECT_ID/project_id must be a number, got {raw_pid!r}"
        )
    return Config(
        url=str(url), token=str(token),
        project_id=project_id, project_name=repo.get("project"),
        enforce_single_wip=bool(repo.get("enforce_single_wip", False)),
    )
