"""stdio MCP-сервер. Гейты живут в Workflow; тут — тонкая обвязка и понятные ошибки."""
import sys
from functools import wraps

import httpx
from mcp.server.fastmcp import FastMCP

from vikunja_mcp import __version__
from vikunja_mcp.api import VikunjaAPI, VikunjaError
from vikunja_mcp.config import ConfigError, load_config
from vikunja_mcp.workflow import Workflow, WorkflowError

mcp = FastMCP("vikunja-tracker")

_workflow: Workflow | None = None


def _reset_workflow_cache() -> None:
    global _workflow
    _workflow = None


def _wf() -> Workflow:
    global _workflow
    if _workflow is None:
        cfg = load_config()
        _workflow = Workflow(VikunjaAPI(cfg.url, cfg.token), cfg.project_id)
    return _workflow


def _tool(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (WorkflowError, ConfigError) as e:
            return {"error": str(e)}
        except VikunjaError as e:
            return {"error": f"Vikunja API: {e.status} {e.message}"}
        except httpx.HTTPError as e:
            return {
                "error": f"трекер недоступен ({e.__class__.__name__}): "
                f"проверь url в .vikunja-mcp.toml и VPN"
            }

    return wrapper


@mcp.tool()
@_tool
def next_task() -> dict:
    """Что делать дальше, в порядке: (1) ТВОЯ активная задача (Design/Build, в т.ч.
    вернувшаяся из Your Call), (2) назначенная на тебя в Queue, (3) багфикс в
    Review, ждущий независимого ревью (label bug, вердикта ещё нет), (4) верхняя
    СВОБОДНАЯ из Queue. Задачи, назначенные на другого, не выдаёт никогда — они
    «для людей». Backlog и blocked не трогает. Одна задача за раз."""
    return _wf().next_task()


@mcp.tool()
@_tool
def claim(task_id: int) -> dict:
    """Взять задачу из Queue: назначает тебя и переносит в Design. Брать можно
    свободные или уже назначенные на тебя; назначенная на другого — «для людей»,
    её claim не отдаст. Отказ также вне Queue и при проигранной гонке (тогда next_task)."""
    return _wf().claim(task_id)


@mcp.tool()
@_tool
def get_task(task_id: int) -> dict:
    """Досье задачи: полное (не обрезанное) описание, стадия, assignees, лейблы,
    related (связанные задачи по видам родства) и все комментарии."""
    return _wf().get_task(task_id)


@mcp.tool()
@_tool
def comment(task_id: int, text: str) -> dict:
    """Заметка о ходе работы: находки, решения ('выбрал X вместо Y потому что Z')."""
    return _wf().comment(task_id, text)


@mcp.tool()
@_tool
def advance(
    task_id: int, to: str,
    spec: str | None = None, worklog: str | None = None, evidence: str | None = None,
    root_cause: str | None = None,
) -> dict:
    """Продвинуть СВОЮ задачу. to='build' требует spec (подход/дизайн).
    to='review' требует ОТЧЁТ о проделанной работе: worklog (что сделано и как
    проверено — запуском, не чтением кода) + evidence (коммит/PR/вывод проверки);
    для багфиксов ОБЯЗАТЕЛЬНО передай root_cause — причину бага (почему возник),
    а не симптом. Отчёт уходит комментом в задачу — его читает ревьюер.
    Перехода в Done нет — Done ставит человек после ревью."""
    return _wf().advance(
        task_id, to, spec=spec, worklog=worklog, evidence=evidence, root_cause=root_cause
    )


@mcp.tool()
@_tool
def review_task(task_id: int, verdict: str, report: str) -> dict:
    """Независимое ревью багфикса в Review (предлагается через next_task). Ты НЕ должен
    быть автором проверяемого кода — ревьюит отдельная сессия. Проверь по сути:
    воспроизведи баг, убедись что фикс закрывает root cause (не симптом), прогони
    ЗАПУСКОМ. verdict='approve' — вердикт-коммент, Done дальше ставит человек;
    verdict='needs_work' — вердикт-коммент и задача возвращается в Build имплементеру.
    report обязателен: что проверял, что наблюдал, почему такой вердикт."""
    return _wf().review_task(task_id, verdict, report)


@mcp.tool()
@_tool
def call_human(task_id: int, question: str) -> dict:
    """Застрял и нужен человек (решение/вводные): вопрос уйдёт комментом, задача — в
    колонку 'Your Call' (в сокращениях — YC), assignee сохранится. Это НЕ ревью и НЕ
    внешняя блокировка."""
    return _wf().call_human(task_id, question)


@mcp.tool()
@_tool
def return_task(task_id: int, reason: str) -> dict:
    """Вернуть задачу из-за ВНЕШНЕЙ блокировки (нет доступа/зависимость/чужой сервис):
    снимает тебя, ставит label 'blocked', уносит в Backlog на ре-триаж человеком."""
    return _wf().return_task(task_id, reason)


@mcp.tool()
@_tool
def decompose(task_id: int, subtasks: list[dict]) -> dict:
    """Разбить СВОЮ большую задачу (>~полдня работы) на >=2 подзадачи:
    [{'title': ..., 'description'?: ..., 'priority'?: 0-5}]. Подзадачи встают в Queue
    с relation на родителя; родитель уходит в Backlog с label 'epic'."""
    return _wf().decompose(task_id, subtasks)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--version":
        print(f"vikunja-mcp {__version__}")
        return
    if args and args[0] == "setup":
        from vikunja_mcp.setup_cmd import run_setup

        raise SystemExit(run_setup(args[1:]))
    if args and args[0] == "install-skill":
        from vikunja_mcp.setup_cmd import install_skill

        install_skill()
        return
    mcp.run()
