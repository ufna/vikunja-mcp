"""`vikunja-mcp setup` — создать/реконсилировать проект под канонический пайплайн."""
import argparse
import os
import sys

from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.workflow import STAGES

MIGRATION = {"Todo": "Queue", "To-Do": "Queue", "To-do": "Queue", "Doing": "Build"}
PERMISSIONS = {"read": 0, "write": 1, "admin": 2}


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
    print("\n--- .mcp.json (закоммить рядом; канал stable = автоматическая раскатка релизов) ---")
    print(
        '{ "mcpServers": { "tracker": {\n'
        '    "command": "uvx",\n'
        '    "args": ["--refresh-package", "vikunja-mcp",\n'
        '             "--from", "git+https://github.com/ufna/vikunja-mcp@stable", "vikunja-mcp"]\n'
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


def install_skill(dest_root=None) -> None:
    import shutil
    from importlib.resources import files
    from pathlib import Path

    src = files("vikunja_mcp").joinpath("skills/tracker/SKILL.md")
    root = Path(dest_root) if dest_root else Path("~/.claude").expanduser()
    dest = root / "skills" / "tracker"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(src), dest / "SKILL.md")
    print(f"skill installed: {dest / 'SKILL.md'}")
