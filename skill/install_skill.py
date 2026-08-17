#!/usr/bin/env python3
"""1-Click Skill Installer for letitloop.

Installs the `letitloop` agent skill into supported AI environments:
- Claude Code (~/.claude/skills/letitloop/)
- Google Antigravity (~/.gemini/antigravity/builtin/skills/letitloop/ or .agents/skills/letitloop/)
- Cursor (.cursor/skills/letitloop/)
- OpenCode (.opencode/skills/letitloop/)
"""

import argparse
import shutil
import sys
from pathlib import Path


def get_skill_src() -> Path:
    current_dir = Path(__file__).resolve().parent
    skill_file = current_dir / "SKILL.md"
    if not skill_file.is_file():
        raise FileNotFoundError(f"SKILL.md not found at {skill_file}")
    return current_dir


def install_for_claude_code(src_dir: Path) -> Path:
    home = Path.home()
    dest = home / ".claude" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_antigravity(src_dir: Path, target_workspace: Path | None = None) -> Path:
    if target_workspace and (target_workspace / ".agents").exists():
        dest = target_workspace / ".agents" / "skills" / "letitloop"
    else:
        home = Path.home()
        dest = home / ".gemini" / "antigravity" / "builtin" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_cursor(src_dir: Path, target_workspace: Path) -> Path:
    dest = target_workspace / ".cursor" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_opencode(src_dir: Path, target_workspace: Path) -> Path:
    dest = target_workspace / ".opencode" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def main():
    parser = argparse.ArgumentParser(description="Install letitloop skill for AI assistants.")
    parser.add_argument(
        "--target",
        choices=["all", "claude", "antigravity", "cursor", "opencode"],
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
    src = get_skill_src()
    ws = Path(args.workspace).resolve()

    installed = []

    if args.target in ("all", "claude"):
        try:
            dest = install_for_claude_code(src)
            installed.append(("Claude Code", dest))
        except Exception as e:
            print(f"[-] Claude Code install skipped: {e}", file=sys.stderr)

    if args.target in ("all", "antigravity"):
        try:
            dest = install_for_antigravity(src, ws)
            installed.append(("Google Antigravity", dest))
        except Exception as e:
            print(f"[-] Google Antigravity install skipped: {e}", file=sys.stderr)

    if args.target in ("all", "cursor"):
        try:
            dest = install_for_cursor(src, ws)
            installed.append(("Cursor IDE", dest))
        except Exception as e:
            print(f"[-] Cursor install skipped: {e}", file=sys.stderr)

    if args.target in ("all", "opencode"):
        try:
            dest = install_for_opencode(src, ws)
            installed.append(("OpenCode", dest))
        except Exception as e:
            print(f"[-] OpenCode install skipped: {e}", file=sys.stderr)

    print("\n========================================================")
    print("✨ letitloop SKILL INSTALLATION SUMMARY")
    print("========================================================")
    for name, path in installed:
        print(f"✅ {name:<20} -> {path}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
