"""Unit tests for letitloop Agent Skill specification and installer."""

import tempfile
from pathlib import Path

import yaml

from skill.install_skill import (
    get_skill_src,
    install_for_antigravity,
    install_for_cline,
    install_for_cursor,
    install_for_hermes,
    install_for_opencode,
    install_for_windsurf,
)


def test_skill_md_structure():
    src = get_skill_src()
    skill_file = src / "SKILL.md"
    assert skill_file.exists()

    content = skill_file.read_text(encoding="utf-8")
    assert content.startswith("---")

    parts = content.split("---", 2)
    assert len(parts) >= 3
    fm_raw = parts[1]
    metadata = yaml.safe_load(fm_raw)

    assert metadata["name"] == "letitloop"
    assert "description" in metadata
    assert "tags" in metadata
    assert "orchestration" in metadata["tags"]


def test_skill_installers():
    src = get_skill_src()
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)

        # Cursor installer
        c_dest = install_for_cursor(src, ws)
        assert (c_dest / "SKILL.md").exists()

        # OpenCode installer
        o_dest = install_for_opencode(src, ws)
        assert (o_dest / "SKILL.md").exists()

        # Hermes installer
        (ws / ".hermes").mkdir()
        h_dest = install_for_hermes(src, ws)
        assert (h_dest / "SKILL.md").exists()

        # Cline installer
        cl_dest = install_for_cline(src, ws)
        assert (cl_dest / "SKILL.md").exists()

        # Windsurf installer
        w_dest = install_for_windsurf(src, ws)
        assert (w_dest / "SKILL.md").exists()

        # Antigravity project-local installer
        (ws / ".agents").mkdir()
        a_dest = install_for_antigravity(src, ws)
        assert (a_dest / "SKILL.md").exists()
