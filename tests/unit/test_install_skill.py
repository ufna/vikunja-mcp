import json
import os
import shutil
import subprocess

import pytest

from vikunja_mcp.setup_cmd import (
    HOOK_SCRIPT_NAME,
    install_orchestrator_hook,
    install_skill,
)


def test_install_skill_copies_to_claude_and_opencode(tmp_path, capsys):
    claude_root = tmp_path / "claude"
    opencode_root = tmp_path / "opencode"
    install_skill(dest_root=claude_root, opencode_root=opencode_root)

    claude_skill = claude_root / "skills" / "tracker" / "SKILL.md"
    opencode_skill = opencode_root / "skills" / "tracker" / "SKILL.md"
    assert claude_skill.is_file()
    assert opencode_skill.is_file()

    text = opencode_skill.read_text()
    assert text.startswith("---") and "name: tracker" in text
    assert claude_skill.read_text() == text          # один и тот же упакованный источник, без форка

    out = capsys.readouterr().out
    assert "instructions" in out                     # печатает строку для opencode.json
    assert str(opencode_skill) in out                # с реальным путём установленного файла


# --- SessionStart orchestrator hook -----------------------------------------------------

def _session_start(claude_root):
    settings = json.loads((claude_root / "settings.json").read_text())
    return settings["hooks"]["SessionStart"]


def _managed_entries(session_start):
    return [
        e for e in session_start
        if any(HOOK_SCRIPT_NAME in str(h.get("command", "")) for h in e.get("hooks", []))
    ]


def test_install_writes_conditional_sessionstart_hook(tmp_path, capsys):
    claude_root = tmp_path / "claude"
    script = install_orchestrator_hook(claude_root)

    # 1) the hook script lands, is executable, and is the file the settings entry points at
    assert script == claude_root / "hooks" / HOOK_SCRIPT_NAME
    assert script.is_file()
    assert script.stat().st_mode & 0o111                       # some execute bit set

    body = script.read_text()
    assert ".vikunja-mcp.toml" in body                         # conditional gate on the marker
    assert 'hookEventName": "SessionStart"' in body            # injects as SessionStart context
    assert "additionalContext" in body

    # 2) the injected text is real SessionStart additionalContext and NAMES the override
    payload_line = next(ln for ln in body.splitlines() if ln.startswith('{"hookSpecificOutput"'))
    payload = json.loads(payload_line)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "OVERRIDES" in ctx and "orchestrator" in ctx.lower()
    assert "tracker" in ctx                                    # points at the full playbook skill

    # 3) it is registered in settings.json under hooks.SessionStart, pointing at the script
    managed = _managed_entries(_session_start(claude_root))
    assert len(managed) == 1
    assert any(str(script) in h["command"] for h in managed[0]["hooks"])


def test_install_hook_is_idempotent(tmp_path):
    claude_root = tmp_path / "claude"
    install_orchestrator_hook(claude_root)
    install_orchestrator_hook(claude_root)
    install_orchestrator_hook(claude_root)

    # re-running never duplicates the managed entry (marker = the script name)
    assert len(_managed_entries(_session_start(claude_root))) == 1


def test_install_hook_preserves_unrelated_settings(tmp_path):
    claude_root = tmp_path / "claude"
    claude_root.mkdir()
    (claude_root / "settings.json").write_text(json.dumps({
        "model": "opus",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard"}]}],
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "echo mine"}]},   # user's own hook
            ],
        },
    }))

    install_orchestrator_hook(claude_root)

    settings = json.loads((claude_root / "settings.json").read_text())
    assert settings["model"] == "opus"                                    # unrelated key kept
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}           # unrelated key kept
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "guard"  # other event kept

    session_start = settings["hooks"]["SessionStart"]
    commands = [h["command"] for e in session_start for h in e["hooks"]]
    assert "echo mine" in commands                                        # user's SessionStart kept
    assert len(_managed_entries(session_start)) == 1                      # + exactly one of ours

    # a re-run STILL leaves the user's hook and just one of ours
    install_orchestrator_hook(claude_root)
    session_start = json.loads((claude_root / "settings.json").read_text())["hooks"]["SessionStart"]
    assert "echo mine" in [h["command"] for e in session_start for h in e["hooks"]]
    assert len(_managed_entries(session_start)) == 1


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")
def test_generated_hook_gates_on_toml_when_run(tmp_path):
    """Actually RUN the generated shell script: it must emit parseable additionalContext
    inside a tracker project (walk-up from a subdir) and NOTHING outside one."""
    claude_root = tmp_path / "claude"
    script = install_orchestrator_hook(claude_root)

    def run_hook(start_dir):
        # inherit the real env (PATH for dirname/cat/sh) but pin the start dir both ways
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(start_dir)}
        return subprocess.run(
            ["sh", str(script)], env=env, cwd=str(start_dir), capture_output=True, text=True,
        )

    # tracker project: .vikunja-mcp.toml at the root, session started in a nested subdir
    project = tmp_path / "proj"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / ".vikunja-mcp.toml").write_text("[tracker]\nproject_id = 1\n")

    inside = run_hook(nested)                                   # walk-up must find the toml
    assert inside.returncode == 0
    ctx = json.loads(inside.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "OVERRIDES" in ctx                                   # the ignition really fired

    # non-tracker project: no toml anywhere up to root -> silent, exit 0, no output
    outside_dir = tmp_path / "plain"
    outside_dir.mkdir()
    outside = run_hook(outside_dir)
    assert outside.returncode == 0
    assert outside.stdout.strip() == ""                        # no cross-project pollution
