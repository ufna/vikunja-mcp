"""Бамп патч-версии проекта в обоих version-файлах И в self-entry ``uv.lock``.

Единый источник истины — ``__version__`` в ``src/vikunja_mcp/__init__.py``;
``pyproject.toml`` держится с ним синхронно. Релизный CI-job зовёт этот скрипт
на каждый зелёный push в main: читает текущую версию, бампает патч
(``X.Y.Z`` -> ``X.Y.(Z+1)``), синхронно переписывает обе строки и печатает новую
версию последней строкой stdout как ``vX.Y.Z`` — job забирает её как тег и
как имя коммита.

Третий файл — ``uv.lock``: его собственная запись
``[[package]] name = "vikunja-mcp"`` тоже хранит версию. Без её синхронного
бампа лок разъезжается с ``pyproject`` (последний manual-бамп записал ``0.1.2``,
пакет ушёл вперёд), и ближайший ``uv sync`` грязнит дерево. Правим self-entry
IN-PLACE регексом, а НЕ шеллаутом в ``uv lock``: смена версии локального editable-
пакета меняет в локе ровно одну строку, поэтому таргетная замена байт-в-байт
равна выхлопу ``uv lock``, но без сети и без риска задеть чужие пины, и оставляет
скрипт чисто-тестируемым (без внешнего процесса).

Чистые/тестируемые функции (``bump_patch`` / ``read_version`` / ``write_version``
/ ``write_lock_version``), CLI-обёртка — только в ``main`` под
``if __name__ == "__main__"``.
"""

from __future__ import annotations

import re
from pathlib import Path

# Version-файлы относительно корня репо (скрипт лежит в scripts/).
REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = REPO_ROOT / "src" / "vikunja_mcp" / "__init__.py"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
LOCK_PATH = REPO_ROOT / "uv.lock"

# Строка вида: __version__ = "0.1.3"
_INIT_RE = re.compile(r'(?m)^(__version__\s*=\s*")(\d+\.\d+\.\d+)(")')
# Строка [project].version: version = "0.1.3". ^-якорь (MULTILINE) держит замену
# строго на строке, начинающейся с `version` — target-version = "py311" и
# requires-python = "..." не попадают под неё.
_PYPROJECT_RE = re.compile(r'(?m)^(version\s*=\s*")(\d+\.\d+\.\d+)(")')
# self-entry в uv.lock: пара строк `name = "vikunja-mcp"` + `version = "..."`.
# В локе десятки version-строк (по одной на пакет) — привязка к паре name+version
# бьёт ровно в запись нашего пакета и не задевает чужие пины. ^-якорь + точная
# сцепка через \n соответствуют стабильному формату вывода uv (ключи без отступа,
# version сразу под name). Если формат когда-то поедет — совпадений станет не 1 и
# _replace_version громко упадёт, а не тихо пропустит бамп.
_LOCK_SELF_RE = re.compile(r'(?m)^(name = "vikunja-mcp"\nversion = ")(\d+\.\d+\.\d+)(")')


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


def write_lock_version(new_version: str, *, lock_path: Path = LOCK_PATH) -> None:
    """Проставляет new_version в self-entry uv.lock (только запись нашего пакета).

    Правит ровно одну строку — `version` в блоке `[[package]] name = "vikunja-mcp"`.
    Этого достаточно: у локального editable-пакета версия — единственное поле,
    зависящее от `pyproject`, так что результат байт-в-байт равен `uv lock`.
    """
    _replace_version(lock_path, _LOCK_SELF_RE, new_version)


def main() -> None:
    new_version = bump_patch(read_version())
    write_version(new_version)
    write_lock_version(new_version)
    # Последняя строка stdout — vX.Y.Z: CI забирает её как output (тег + коммит).
    print(f"v{new_version}")


if __name__ == "__main__":
    main()
