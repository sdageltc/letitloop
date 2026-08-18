#!/usr/bin/env python3
"""Universal 1-Click Skill Installer for letitloop.

Installs the `letitloop` agent skill into supported 2026 AI coding environments:
- Claude Code (~/.claude/skills/letitloop/)
- Google Antigravity (~/.gemini/antigravity/builtin/skills/letitloop/ or .agents/skills/letitloop/)
- OpenAI Codex (~/.codex/skills/letitloop/ or .codex/skills/letitloop/)
- Hermes Agent (~/.hermes/skills/letitloop/ or .hermes/skills/letitloop/)
- OpenCode (.opencode/skills/letitloop/)
- Cursor IDE (.cursor/skills/letitloop/)
- Cline (.cline/skills/letitloop/ or ~/.cline/skills/letitloop/)
- Windsurf (.windsurf/skills/letitloop/)
"""

import argparse
from pathlib import Path

from orchestrator.skill_installer import (
    get_skill_src,
    install_for_antigravity,
    install_for_claude_code,
    install_for_cline,
    install_for_codex,
    install_for_cursor,
    install_for_hermes,
    install_for_opencode,
    install_for_windsurf,
    run_skill_install,
)

__all__ = [
    "get_skill_src",
    "install_for_antigravity",
    "install_for_claude_code",
    "install_for_cline",
    "install_for_codex",
    "install_for_cursor",
    "install_for_hermes",
    "install_for_opencode",
    "install_for_windsurf",
    "run_skill_install",
    "main",
]


def main():
    parser = argparse.ArgumentParser(description="Install letitloop skill for AI assistants.")
    parser.add_argument(
        "--target",
        choices=["all", "claude", "antigravity", "hermes", "opencode", "cursor", "cline", "windsurf", "codex"],
        default="all",
        help="Target AI assistant environment (default: all)",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=".",
        help="Target workspace path for project-local skill installation (default: current directory)",
    )

    args = parser.parse_args()
    ws = Path(args.workspace).resolve()

    installed = run_skill_install(target=args.target, workspace=ws)

    print("\n========================================================")
    print("letitloop Multi-Platform Skill Installation Summary")
    print("========================================================")
    for name, path in installed:
        print(f"[OK] {name:<22} -> {path}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
