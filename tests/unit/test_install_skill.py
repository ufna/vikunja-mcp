from vikunja_mcp.setup_cmd import install_skill


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
