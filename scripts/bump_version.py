"""Бамп патч-версии проекта в обоих version-файлах.

Единый источник истины — ``__version__`` в ``src/vikunja_mcp/__init__.py``;
``pyproject.toml`` держится с ним синхронно. Релизный CI-job зовёт этот скрипт
на каждый зелёный push в main: читает текущую версию, бампает патч
(``X.Y.Z`` -> ``X.Y.(Z+1)``), синхронно переписывает обе строки и печатает новую
версию последней строкой stdout как ``vX.Y.Z`` — job забирает её как тег и
как имя коммита.

Чистые/тестируемые функции (``bump_patch`` / ``read_version`` / ``write_version``),
CLI-обёртка — только в ``main`` под ``if __name__ == "__main__"``.
"""

from __future__ import annotations

import re
from pathlib import Path

# Version-файлы относительно корня репо (скрипт лежит в scripts/).
REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = REPO_ROOT / "src" / "vikunja_mcp" / "__init__.py"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

# Строка вида: __version__ = "0.1.3"
_INIT_RE = re.compile(r'(?m)^(__version__\s*=\s*")(\d+\.\d+\.\d+)(")')
# Строка [project].version: version = "0.1.3". ^-якорь (MULTILINE) держит замену
# строго на строке, начинающейся с `version` — target-version = "py311" и
# requires-python = "..." не попадают под неё.
_PYPROJECT_RE = re.compile(r'(?m)^(version\s*=\s*")(\d+\.\d+\.\d+)(")')


def bump_patch(ver: str) -> str:
    """'X.Y.Z' -> 'X.Y.(Z+1)'. Перенос — обычная арифметика: '0.1.9' -> '0.1.10'."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ver.strip())
    if not m:
        raise ValueError(f"версия не в формате X.Y.Z: {ver!r}")
    major, minor, patch = (int(x) for x in m.groups())
    return f"{major}.{minor}.{patch + 1}"


def read_version(init_path: Path = INIT_PATH) -> str:
    """Текущая версия из источника истины — __version__ в __init__.py."""
    m = _INIT_RE.search(init_path.read_text())
    if not m:
        raise ValueError(f"не найден __version__ в {init_path}")
    return m.group(2)


def _replace_version(path: Path, pattern: re.Pattern[str], new_version: str) -> None:
    """Заменяет ровно одну version-строку в файле; ошибка, если совпадений не 1."""
    new_text, n = pattern.subn(rf"\g<1>{new_version}\g<3>", path.read_text())
    if n != 1:
        raise ValueError(f"ожидал ровно 1 version-строку в {path}, нашёл {n}")
    path.write_text(new_text)


def write_version(
    new_version: str,
    *,
    pyproject_path: Path = PYPROJECT_PATH,
    init_path: Path = INIT_PATH,
) -> None:
    """Синхронно проставляет new_version в оба version-файла."""
    _replace_version(init_path, _INIT_RE, new_version)
    _replace_version(pyproject_path, _PYPROJECT_RE, new_version)


def main() -> None:
    new_version = bump_patch(read_version())
    write_version(new_version)
    # Последняя строка stdout — vX.Y.Z: CI забирает её как output (тег + коммит).
    print(f"v{new_version}")


if __name__ == "__main__":
    main()
