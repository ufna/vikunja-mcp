from vikunja_mcp.setup_cmd import install_skill


def test_install_skill_copies_to_claude_skills(tmp_path):
    install_skill(dest_root=tmp_path)
    installed = tmp_path / "skills" / "tracker" / "SKILL.md"
    assert installed.is_file()
    text = installed.read_text()
    assert text.startswith("---") and "name: tracker" in text
