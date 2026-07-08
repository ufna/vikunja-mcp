"""`vikunja-mcp setup` — создать/реконсилировать проект под канонический пайплайн."""
import argparse
import os
import sys

from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.workflow import STAGES

MIGRATION = {"Todo": "Queue", "To-Do": "Queue", "To-do": "Queue", "Doing": "Build"}
# переименование колонок in-place (в отличие от MIGRATION — та переносит задачи между
# РАЗНЫМИ бакетами): title бакета в Vikunja и есть идентификатор колонки, поэтому старую
# колонку ПЕРЕИМЕНОВЫВАЕМ на месте, а не создаём новую (иначе на старой доске колонка
# задвоится, а задачи в ней осиротеют).
RENAMES = {"Call to Human": "Your Call"}
PERMISSIONS = {"read": 0, "write": 1, "admin": 2}

# SessionStart hook that redirects a bare `/loop` in a tracker-configured project to the
# tracker orchestrator (instead of Claude Code's generic autonomous default). Named so it
# can be found idempotently in settings.json (match by this filename in a hook command).
HOOK_SCRIPT_NAME = "vikunja-tracker-orchestrator.sh"

# The standing context injected on every session start INSIDE a tracker project. Kept short
# (the full playbook is the `tracker` skill); it must NAME the override of the generic loop
# default, or a bare `/loop` never drains the queue. Mirrors CLAUDE.md's dogfood section.
ORCHESTRATOR_IGNITION = (
    "[vikunja-mcp] This project is tracker-configured (.vikunja-mcp.toml found). When you "
    "run under /loop, act as the TRACKER ORCHESTRATOR — a thin pump: next_task -> claim a "
    "fresh Queue task -> dispatch ONE per-task agent that owns the whole task lifecycle "
    "(Design -> Build -> commit+push -> advance to Review) -> drain the next. This "
    'DELIBERATELY OVERRIDES Claude Code\'s generic autonomous-/loop default ("steward, not '
    'initiator; don\'t start fresh work without a human go-ahead; stop when idle"): the '
    "Queue is human-triaged work, so claiming a fresh Queue task and dispatching IS the "
    "mandate, not unbidden initiation. An empty queue means ScheduleWakeup (~20 min), never "
    "a stop. Ask the human only via call_human (a card), never the console. Full playbook: "
    "use the `tracker` skill."
)


def reconcile(api, project_title: str, shares: list[tuple[str, int]]) -> int:
    project = next((p for p in api.projects() if p["title"] == project_title), None)
    if project is None:
        project = api.create_project(project_title)
        print(f"project '{project_title}': created (id={project['id']})")
    else:
        print(f"project '{project_title}': exists (id={project['id']})")
    pid = project["id"]

    view = api.kanban_view(pid)
    existing = {b["title"]: b for b in api.buckets(pid, view["id"])}

    # переименование старых колонок на месте (POST — full-replace title+position),
    # до сборки canonical, чтобы новый заголовок сразу переиспользовался как каноничный.
    # Если новая колонка уже есть (полу-мигрированная доска) — не трогаем: старую пустую
    # снесёт снос лишних бакетов ниже, непустую он оставит с предупреждением.
    for old_title, new_title in RENAMES.items():
        if old_title in existing and new_title not in existing:
            bucket = existing.pop(old_title)
            bucket["title"] = new_title
            api.update_bucket(pid, view["id"], bucket, position=bucket.get("position", 0))
            existing[new_title] = bucket
            print(f"  renamed bucket '{old_title}' -> '{new_title}'")

    canonical: dict[str, dict] = {}
    for title in STAGES:
        canonical[title] = existing.get(title) or api.create_bucket(pid, view["id"], title)

    # порядок колонок = порядок STAGES (переиспользованный авто-Done иначе вылезет первым)
    for idx, title in enumerate(STAGES):
        api.update_bucket(pid, view["id"], canonical[title], position=(idx + 1) * 100)

    # перенос задач из старых бакетов по маппингу
    board = {b["title"]: b for b in api.view_tasks(pid, view["id"])}
    for old_title, new_title in MIGRATION.items():
        bucket = board.get(old_title)
        for task in (bucket or {}).get("tasks") or []:
            api.move_task(pid, view["id"], canonical[new_title]["id"], task["id"])
            print(f"  moved #{task['id']} '{task['title']}': {old_title} -> {new_title}")

    # снос лишних ПУСТЫХ бакетов (включая авто To-Do/Doing и опустевшие старые)
    board = {b["title"]: b for b in api.view_tasks(pid, view["id"])}
    for title, bucket in board.items():
        if title in canonical and canonical[title]["id"] == bucket["id"]:
            continue
        if bucket.get("tasks"):
            print(f"  !! бакет '{title}' не пуст и не каноничен — оставлен, разбери руками")
            continue
        api.delete_bucket(pid, view["id"], bucket["id"])

    api.configure_kanban(
        pid, view,
        default_bucket_id=canonical["Backlog"]["id"],
        done_bucket_id=canonical["Done"]["id"],
    )
    for username, permission in shares:
        api.share_project(pid, username, permission)
        print(f"  share -> {username} (permission {permission})")
    return pid


def _print_snippets(pid: int, project_title: str, url: str) -> None:
    print("\n--- .vikunja-mcp.toml (закоммить в корень рабочего репо) ---")
    print(f'[tracker]\nurl = "{url}"\nproject_id = {pid}\nproject = "{project_title}"')
    print(
        "\nТокен туда не кладём: создай рядом .vikunja-mcp.env с VIKUNJA_TOKEN=...\n"
        "и добавь .vikunja-mcp.env в .gitignore рабочего репо — коммитить нельзя."
    )
    print("\n--- .mcp.json (Claude Code; закоммить рядом; канал stable = авто-раскатка релизов) ---")
    print(
        '{ "mcpServers": { "tracker": {\n'
        '    "command": "uvx",\n'
        '    "args": ["--refresh-package", "vikunja-mcp",\n'
        '             "--from", "git+https://github.com/ufna/vikunja-mcp@stable", "vikunja-mcp"]\n'
        "} } }"
    )
    print("\n--- opencode.json (opencode; закоммить рядом; та же stable-раскатка, токен так же внешний) ---")
    print(
        '{ "$schema": "https://opencode.ai/config.json", "mcp": { "tracker": {\n'
        '    "type": "local",\n'
        '    "command": ["uvx", "--refresh-package", "vikunja-mcp",\n'
        '      "--from", "git+https://github.com/ufna/vikunja-mcp@stable", "vikunja-mcp"],\n'
        '    "enabled": true\n'
        "} } }"
    )


def run_setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vikunja-mcp setup")
    parser.add_argument("--project", required=True)
    parser.add_argument("--share", action="append", default=[], metavar="USER:read|write|admin")
    parser.add_argument("--url", default=os.environ.get("VIKUNJA_URL"))
    args = parser.parse_args(argv)

    token = os.environ.get("VIKUNJA_TOKEN")
    if not args.url or not token:
        print("нужны --url (или VIKUNJA_URL) и VIKUNJA_TOKEN (админский) в env", file=sys.stderr)
        return 2

    shares = []
    for raw in args.share:
        user, _, perm = raw.partition(":")
        if perm not in PERMISSIONS:
            print(f"--share {raw}: permission должен быть read|write|admin", file=sys.stderr)
            return 2
        shares.append((user, PERMISSIONS[perm]))

    api = VikunjaAPI(args.url, token)
    pid = reconcile(api, args.project, shares)
    _print_snippets(pid, args.project, args.url)
    return 0


def render_hook_script() -> str:
    """POSIX-sh SessionStart hook: walk cwd upward for `.vikunja-mcp.toml` and, if found,
    print the orchestrator ignition as SessionStart additionalContext, else print nothing.
    Dependency-free at runtime (no jq/python): the JSON is pre-built here by json.dumps and
    emitted verbatim from a QUOTED heredoc, so there is nothing to escape when it runs and
    backticks/`$` in the ignition stay literal. Always exits 0 (no output outside a tracker
    project) so it never pollutes /loop elsewhere and never raises a hook-error notice."""
    import json

    payload = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ORCHESTRATOR_IGNITION,
        }
    })
    return (
        "#!/bin/sh\n"
        "# vikunja-mcp MANAGED SessionStart hook — tracker-orchestrator ignition.\n"
        "# Injects the orchestrator standing-context ONLY inside a tracker-configured\n"
        "# project (walk-up for .vikunja-mcp.toml), so it never hijacks /loop elsewhere.\n"
        "# Re-created idempotently by `vikunja-mcp install-skill`; local edits are overwritten.\n"
        'dir="${CLAUDE_PROJECT_DIR:-$PWD}"\n'
        "while :; do\n"
        '  if [ -f "$dir/.vikunja-mcp.toml" ]; then\n'
        "    cat <<'VIKUNJA_MCP_IGNITION_EOF'\n"
        f"{payload}\n"
        "VIKUNJA_MCP_IGNITION_EOF\n"
        "    exit 0\n"
        "  fi\n"
        '  case "$dir" in /|"") break ;; esac\n'
        '  dir=$(dirname "$dir")\n'
        "done\n"
        "exit 0\n"
    )


def install_orchestrator_hook(claude_root) -> "object":
    """Write the managed hook script under <claude_root>/hooks/ and register it in
    <claude_root>/settings.json under hooks.SessionStart (no matcher = fires on
    startup/resume/clear/compact, so the framing survives compaction in a long /loop).
    IDEMPOTENT and non-destructive: any prior entry referencing our script name is dropped
    and exactly one fresh entry re-added; unrelated hooks and every other settings key are
    preserved (this is the user-level ~/.claude/settings.json). Returns the script path."""
    import json
    import shlex
    import stat
    from pathlib import Path

    claude_root = Path(claude_root)
    hooks_dir = claude_root / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    script = hooks_dir / HOOK_SCRIPT_NAME
    script.write_text(render_hook_script())
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    settings_path = claude_root / "settings.json"
    settings: dict = {}
    if settings_path.is_file():
        try:
            loaded = json.loads(settings_path.read_text() or "{}")
            settings = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            settings = {}                    # corrupt/hand-broken file — start clean, don't crash

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = settings["hooks"] = {}
    session_start = hooks.get("SessionStart")
    if not isinstance(session_start, list):
        session_start = []

    def _is_managed(entry: object) -> bool:
        return isinstance(entry, dict) and any(
            isinstance(h, dict) and HOOK_SCRIPT_NAME in str(h.get("command", ""))
            for h in (entry.get("hooks") or [])
        )

    kept = [e for e in session_start if not _is_managed(e)]     # keep the user's other hooks
    kept.append({"hooks": [
        {"type": "command", "command": f"sh {shlex.quote(str(script))}", "timeout": 5},
    ]})
    hooks["SessionStart"] = kept
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return script


def install_skill(dest_root=None, opencode_root=None) -> None:
    """Разложить упакованный SKILL.md туда, где его подхватят агенты:
    Claude Code (~/.claude/skills/tracker) и opencode (~/.config/opencode/skills/tracker).
    У opencode нет авто-дискавери произвольного файла правил — он тянет его через
    config-ключ `instructions`, поэтому печатаем готовую строку для opencode.json.
    Для Claude Code ДОПОЛНИТЕЛЬНО ставим conditional SessionStart-хук: он редиректит голый
    /loop в tracker-проекте на оркестратора (иначе /loop уходит в generic-автономный дефолт
    и не дренирует очередь). Хук машинного уровня, но срабатывает только при наличии
    .vikunja-mcp.toml, поэтому не мешает другим проектам."""
    import shutil
    from importlib.resources import files
    from pathlib import Path

    src = files("vikunja_mcp").joinpath("skills/tracker/SKILL.md")

    def _copy_to(root: Path) -> Path:
        dest = root / "skills" / "tracker"
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / "SKILL.md"
        shutil.copyfile(str(src), out)
        return out

    claude_root = Path(dest_root) if dest_root else Path("~/.claude").expanduser()
    oc_root = Path(opencode_root) if opencode_root else Path("~/.config/opencode").expanduser()

    claude_skill = _copy_to(claude_root)
    print(f"skill installed (Claude Code): {claude_skill}")

    hook = install_orchestrator_hook(claude_root)
    print(f"orchestrator hook installed (Claude Code): {hook}")
    print(f"  registered in {claude_root / 'settings.json'} under hooks.SessionStart")
    print("  fires only inside a tracker project (.vikunja-mcp.toml); restart Claude Code to load")

    oc_skill = _copy_to(oc_root)
    print(f"skill installed (opencode): {oc_skill}")
    print(f'  добавь в opencode.json: "instructions": ["{oc_skill}"]')
