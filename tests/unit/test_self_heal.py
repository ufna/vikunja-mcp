"""On-server-start self-heal of installed agent artifacts (sync_installed_artifacts).

A moving-`stable` rollout re-resolves the MCP server every session, but the SKILL.md + hook
copies in ~/.claude / ~/.config/opencode used to freeze at the last manual `install-skill`,
so agents silently ran a stale rulebook. The server now refreshes already-installed copies
from the packaged source on start — refresh-only, best-effort, opt-out-able.
"""
import pathlib

import pytest

from vikunja_mcp import setup_cmd
from vikunja_mcp.setup_cmd import (
    HOOK_SCRIPT_NAME,
    install_skill,
    render_hook_script,
    sync_installed_artifacts,
)


def _install(tmp_path):
    """Do a real install into temp roots (never touch the live ~/.claude)."""
    claude_root = tmp_path / "claude"
    opencode_root = tmp_path / "opencode"
    install_skill(dest_root=claude_root, opencode_root=opencode_root)
    return claude_root, opencode_root


def _skill(root):
    return root / "skills" / "tracker" / "SKILL.md"


def _hook(claude_root):
    return claude_root / "hooks" / HOOK_SCRIPT_NAME


def _packaged_skill_text():
    from importlib.resources import files

    return files("vikunja_mcp").joinpath("skills/tracker/SKILL.md").read_text(encoding="utf-8")


def test_stale_skill_and_hook_are_rewritten(tmp_path):
    claude_root, opencode_root = _install(tmp_path)

    # drift all three installed artifacts away from the packaged/rendered source
    _skill(claude_root).write_text("STALE\n", encoding="utf-8")
    _skill(opencode_root).write_text("STALE\n", encoding="utf-8")
    hook = _hook(claude_root)
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# drifted\n", encoding="utf-8")

    healed = sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root)

    # every stale copy is refreshed back to source, and reported
    assert set(healed) == {_skill(claude_root), _skill(opencode_root), hook}
    assert _skill(claude_root).read_text(encoding="utf-8") == _packaged_skill_text()
    assert _skill(opencode_root).read_text(encoding="utf-8") == _packaged_skill_text()
    assert hook.read_text(encoding="utf-8") == render_hook_script()


def test_identical_copies_are_left_untouched(tmp_path):
    claude_root, opencode_root = _install(tmp_path)
    watched = (_skill(claude_root), _skill(opencode_root), _hook(claude_root))
    before = {p: p.stat().st_mtime_ns for p in watched}

    healed = sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root)

    assert healed == []                              # in sync -> nothing to do
    for p, mtime in before.items():
        assert p.stat().st_mtime_ns == mtime         # not even rewritten to identical bytes


def test_healed_hook_keeps_its_executable_bit(tmp_path):
    claude_root, _ = _install(tmp_path)
    hook = _hook(claude_root)
    hook.write_text("STALE\n", encoding="utf-8")
    hook.chmod(0o644)                                # drop the exec bit while stale

    sync_installed_artifacts(dest_root=claude_root, opencode_root=tmp_path / "absent")

    assert hook.stat().st_mode & 0o111               # exec bit restored on refresh


def test_opt_out_skips_all_healing(tmp_path, monkeypatch):
    claude_root, opencode_root = _install(tmp_path)
    _skill(claude_root).write_text("STALE\n", encoding="utf-8")
    monkeypatch.setenv(setup_cmd.SKILL_SYNC_OPT_OUT_ENV, "1")

    healed = sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root)

    assert healed == []
    assert _skill(claude_root).read_text(encoding="utf-8") == "STALE\n"   # left stale, untouched


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_opt_out_accepts_common_truthy_values(tmp_path, monkeypatch, val):
    claude_root, opencode_root = _install(tmp_path)
    _skill(claude_root).write_text("STALE\n", encoding="utf-8")
    monkeypatch.setenv(setup_cmd.SKILL_SYNC_OPT_OUT_ENV, val)
    assert sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root) == []


def test_falsey_opt_out_still_heals(tmp_path, monkeypatch):
    claude_root, opencode_root = _install(tmp_path)
    _skill(claude_root).write_text("STALE\n", encoding="utf-8")
    monkeypatch.setenv(setup_cmd.SKILL_SYNC_OPT_OUT_ENV, "0")          # not a truthy opt-out
    healed = sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root)
    assert _skill(claude_root) in healed


def test_missing_dest_is_not_provisioned(tmp_path):
    # never ran install-skill here: refresh-only must create NOTHING (heal != provision)
    claude_root = tmp_path / "claude"
    opencode_root = tmp_path / "opencode"

    healed = sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root)

    assert healed == []
    assert not claude_root.exists() and not opencode_root.exists()     # not even parent dirs


def test_unwritable_dest_never_raises(tmp_path, monkeypatch):
    claude_root, opencode_root = _install(tmp_path)
    _skill(claude_root).write_text("STALE\n", encoding="utf-8")        # stale -> a write is attempted

    def boom(self, *a, **k):
        raise PermissionError(f"read-only: {self}")

    monkeypatch.setattr(pathlib.Path, "write_text", boom)             # every write now fails

    # the failure is swallowed: no exception, nothing reported healed, file left as-is
    assert sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root) == []
    assert _skill(claude_root).read_text(encoding="utf-8") == "STALE\n"


def test_one_failing_source_does_not_sink_the_other(tmp_path, monkeypatch):
    claude_root, opencode_root = _install(tmp_path)
    _skill(claude_root).write_text("STALE\n", encoding="utf-8")
    hook = _hook(claude_root)
    hook.write_text("STALE\n", encoding="utf-8")

    def boom():
        raise RuntimeError("cannot render hook")

    monkeypatch.setattr(setup_cmd, "render_hook_script", boom)        # hook source explodes

    healed = sync_installed_artifacts(dest_root=claude_root, opencode_root=opencode_root)

    assert _skill(claude_root) in healed                             # skill still healed
    assert hook not in healed                                        # broken hook source skipped
    assert hook.read_text(encoding="utf-8") == "STALE\n"            # left as-is, no crash


def test_packaged_skill_declares_the_managed_contract():
    """SKILL.md must carry the same 'local edits are overwritten' contract the hook does,
    so the on-start refresh is never a surprise, and must name the opt-out env var."""
    text = _packaged_skill_text()
    assert "перезаписаны" in text                                    # local edits overwritten
    assert setup_cmd.SKILL_SYNC_OPT_OUT_ENV in text                 # names the opt-out
