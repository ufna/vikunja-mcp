"""Тесты релизного bump-хелпера.

``scripts/bump_version.py`` лежит вне пакета (scripts/ — не модуль), поэтому
грузим его по абсолютному пути через importlib. Реальные pyproject/__init__
НЕ трогаем — запись проверяем только на файлах в tmp_path.
"""

import importlib.util
from pathlib import Path

import pytest

_BUMP_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
_spec = importlib.util.spec_from_file_location("bump_version", _BUMP_PATH)
bump_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_version)


# --- bump_patch: чистая арифметика по третьей цифре ---


def test_bump_patch_increments_third_component():
    assert bump_version.bump_patch("0.1.3") == "0.1.4"


def test_bump_patch_carries_into_two_digits():
    """Перенос — это не смена minor, а двузначный патч: 0.1.9 -> 0.1.10."""
    assert bump_version.bump_patch("0.1.9") == "0.1.10"


def test_bump_patch_leaves_major_minor_untouched():
    assert bump_version.bump_patch("2.5.0") == "2.5.1"


def test_bump_patch_rejects_non_semver():
    with pytest.raises(ValueError):
        bump_version.bump_patch("0.1")


# --- read_version: источник истины — __init__.py ---


def test_read_version_reads_from_init(tmp_path):
    init = tmp_path / "__init__.py"
    init.write_text('__version__ = "9.9.9"\n')
    assert bump_version.read_version(init) == "9.9.9"


# --- write_version: синхронно меняет обе строки, не задевая соседей ---


def _seed(tmp_path, version):
    init = tmp_path / "__init__.py"
    pyproject = tmp_path / "pyproject.toml"
    init.write_text(f'__version__ = "{version}"\n')
    pyproject.write_text(
        "[project]\n"
        'name = "vikunja-mcp"\n'
        f'version = "{version}"\n'
        'requires-python = ">=3.11"\n\n'
        "[tool.ruff]\n"
        'target-version = "py311"\n'
    )
    return init, pyproject


def test_write_version_updates_both_files(tmp_path):
    init, pyproject = _seed(tmp_path, "0.1.3")
    bump_version.write_version("0.1.4", pyproject_path=pyproject, init_path=init)
    assert '__version__ = "0.1.4"' in init.read_text()
    assert 'version = "0.1.4"' in pyproject.read_text()


def test_write_version_does_not_touch_lookalike_keys(tmp_path):
    """^-якорь: target-version / requires-python не должны сбиться."""
    init, pyproject = _seed(tmp_path, "0.1.3")
    bump_version.write_version("0.1.4", pyproject_path=pyproject, init_path=init)
    text = pyproject.read_text()
    assert 'target-version = "py311"' in text
    assert 'requires-python = ">=3.11"' in text


def test_write_version_raises_when_no_version_line(tmp_path):
    init = tmp_path / "__init__.py"
    pyproject = tmp_path / "pyproject.toml"
    init.write_text("# нет версии тут\n")
    pyproject.write_text('[project]\nversion = "0.1.3"\n')
    with pytest.raises(ValueError):
        bump_version.write_version("0.1.4", pyproject_path=pyproject, init_path=init)


def test_end_to_end_read_bump_write(tmp_path):
    """read -> bump -> write целиком, через temp-файлы (реальные не трогаем)."""
    init, pyproject = _seed(tmp_path, "0.1.9")
    new = bump_version.bump_patch(bump_version.read_version(init))
    bump_version.write_version(new, pyproject_path=pyproject, init_path=init)
    assert new == "0.1.10"
    assert '__version__ = "0.1.10"' in init.read_text()
    assert 'version = "0.1.10"' in pyproject.read_text()
    assert 'target-version = "py311"' in pyproject.read_text()


# --- write_lock_version: правит ТОЛЬКО self-entry uv.lock, не задевая чужие пакеты ---


def _seed_lock(tmp_path, version):
    """Мини-uv.lock: чужой пакет со своей version-строкой + self-entry проекта."""
    lock = tmp_path / "uv.lock"
    lock.write_text(
        "[[package]]\n"
        'name = "httpx"\n'
        'version = "0.28.1"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        "[[package]]\n"
        'name = "vikunja-mcp"\n'
        f'version = "{version}"\n'
        'source = { editable = "." }\n'
    )
    return lock


def test_write_lock_version_bumps_self_entry(tmp_path):
    lock = _seed_lock(tmp_path, "0.1.3")
    bump_version.write_lock_version("0.1.4", lock_path=lock)
    assert 'name = "vikunja-mcp"\nversion = "0.1.4"' in lock.read_text()


def test_write_lock_version_leaves_other_packages_untouched(tmp_path):
    """В реальном локе десятки version-строк — правим ровно self-entry, httpx не трогаем."""
    lock = _seed_lock(tmp_path, "0.1.3")
    bump_version.write_lock_version("0.1.4", lock_path=lock)
    assert 'name = "httpx"\nversion = "0.28.1"' in lock.read_text()


def test_write_lock_version_raises_when_no_self_entry(tmp_path):
    """Нет self-entry -> hard-fail (как и остальные version-строки в _replace_version)."""
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "httpx"\nversion = "0.28.1"\n')
    with pytest.raises(ValueError):
        bump_version.write_lock_version("0.1.4", lock_path=lock)
