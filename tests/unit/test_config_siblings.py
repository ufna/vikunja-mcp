"""`siblings` — the repo's registry of the OTHER tracker projects it may hand work to.

Committed TEAM POLICY, so the same one-source read as wip_limit/language: the repo toml
and nothing else. The dogiators shape that created it is two repos on one token —
`dogiators-front` (4) and `dogiators-backend` (17) — where the front agent had no way to
learn that "backend" is 17, because its own toml never named it.
"""
import pytest

from vikunja_mcp.config import ConfigError, load_config


def _write_toml(path, project_id=4, extra=""):
    path.joinpath(".vikunja-mcp.toml").write_text(
        f'[tracker]\nurl = "https://tracker.zz.hgdev.com"\nproject_id = {project_id}\n'
        f'project = "dogiators-front"\n{extra}'
    )


def test_absent_key_is_an_empty_registry(tmp_path):
    """Absent -> {} and nothing more. A repo with no siblings is the ordinary case (every
    project that exists today), so the key must ship inert."""
    _write_toml(tmp_path)
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.siblings == {}


def test_reads_the_registry_from_the_repo_toml(tmp_path):
    _write_toml(tmp_path, extra='siblings = { backend = 17, docs = 21 }\n')
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert cfg.siblings == {"backend": 17, "docs": 21}


def test_never_read_from_the_environment(tmp_path):
    """Team policy, like wip_limit: a machine's environment must not widen which projects
    this repo can push work into. The env spelling is simply not a key that exists."""
    _write_toml(tmp_path, extra='siblings = { backend = 17 }\n')
    cfg = load_config(cwd=tmp_path, environ={
        "VIKUNJA_TOKEN": "tk", "VIKUNJA_SIBLINGS": "backend=99,secret=100",
    })
    assert cfg.siblings == {"backend": 17}


def test_non_integer_id_is_refused_by_name(tmp_path):
    _write_toml(tmp_path, extra='siblings = { backend = "seventeen" }\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert "backend" in str(exc.value)


def test_non_positive_id_is_refused(tmp_path):
    """Negative ids are Vikunja PSEUDO-projects (favorites); 0 is not a project at all.
    file_task already refuses them one layer down — refusing here names the toml line."""
    _write_toml(tmp_path, extra='siblings = { backend = -1 }\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert "backend" in str(exc.value)


def test_listing_your_own_project_is_refused(tmp_path):
    """A sibling that IS you would make `handoff` file a card into its own project's
    Backlog and then block the current card on it — a self-deadlock the gate cannot break.
    Refuse the config instead of shipping the trap."""
    _write_toml(tmp_path, project_id=4, extra='siblings = { self = 4 }\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert "self" in str(exc.value)


def test_two_names_for_one_id_are_refused(tmp_path):
    """The registry is read in BOTH directions — name -> id when an agent names a target,
    id -> name when a tool writes provenance onto the card. Two names for one id make the
    second direction ambiguous, so the card would carry an arbitrary one of them."""
    _write_toml(tmp_path, extra='siblings = { backend = 17, back = 17 }\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert "17" in str(exc.value)


def test_blank_name_is_refused(tmp_path):
    """The name is what an agent types into handoff/transfer_task, so it must be nameable."""
    _write_toml(tmp_path, extra='siblings = { "" = 17 }\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert "name" in str(exc.value).lower()


def test_a_non_table_value_is_refused(tmp_path):
    """`siblings = 17` is the plausible typo for a single sibling. Refuse it by shape
    rather than letting int(...) succeed on something that is not a registry at all."""
    _write_toml(tmp_path, extra='siblings = 17\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert "siblings" in str(exc.value)


def test_a_boolean_id_is_refused(tmp_path):
    """TOML has real booleans and `int(True)` is 1 — a live project id. Left ungated,
    `siblings = { backend = true }` would silently address project 1."""
    _write_toml(tmp_path, extra='siblings = { backend = true }\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "tk"})
    assert "backend" in str(exc.value)
