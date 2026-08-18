"""Multi-platform Agent Skill Installer for letitloop.

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

import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def get_skill_src() -> Path:
    """Resolve SKILL.md directory path."""
    current_dir = Path(__file__).resolve().parent.parent / "skill"
    skill_file = current_dir / "SKILL.md"
    if not skill_file.is_file():
        # Fallback: check alongside module if packaged
        pkg_skill = Path(__file__).resolve().parent / "SKILL.md"
        if pkg_skill.is_file():
            return Path(__file__).resolve().parent
        raise FileNotFoundError(f"SKILL.md not found at {skill_file}")
    return current_dir


def install_for_claude_code(src_dir: Path) -> Path:
    home = Path.home()
    dest = home / ".claude" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_antigravity(src_dir: Path, target_workspace: Optional[Path] = None) -> Path:
    if target_workspace and (target_workspace / ".agents").exists():
        dest = target_workspace / ".agents" / "skills" / "letitloop"
    else:
        home = Path.home()
        dest = home / ".gemini" / "antigravity" / "builtin" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_hermes(src_dir: Path, target_workspace: Optional[Path] = None) -> Path:
    if target_workspace and (target_workspace / ".hermes").exists():
        dest = target_workspace / ".hermes" / "skills" / "letitloop"
    else:
        home = Path.home()
        dest = home / ".hermes" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_opencode(src_dir: Path, target_workspace: Path) -> Path:
    dest = target_workspace / ".opencode" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_cursor(src_dir: Path, target_workspace: Path) -> Path:
    dest = target_workspace / ".cursor" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_cline(src_dir: Path, target_workspace: Path) -> Path:
    dest = target_workspace / ".cline" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_windsurf(src_dir: Path, target_workspace: Path) -> Path:
    dest = target_workspace / ".windsurf" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


def install_for_codex(src_dir: Path, target_workspace: Optional[Path] = None) -> Path:
    """Install letitloop skill for OpenAI Codex (global skills dir or project-local .codex/skills)."""
    if target_workspace and (target_workspace / ".codex").exists():
        dest = target_workspace / ".codex" / "skills" / "letitloop"
    elif target_workspace and (target_workspace / "AGENTS.md").exists():
        dest = target_workspace / ".codex" / "skills" / "letitloop"
    else:
        home = Path.home()
        dest = home / ".codex" / "skills" / "letitloop"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_dir / "SKILL.md", dest / "SKILL.md")
    return dest


TARGET_MAP = {
    "claude": ("Claude Code", install_for_claude_code),
    "antigravity": ("Google Antigravity", install_for_antigravity),
    "codex": ("OpenAI Codex", install_for_codex),
    "hermes": ("Hermes Agent", install_for_hermes),
    "opencode": ("OpenCode", install_for_opencode),
    "cursor": ("Cursor IDE", install_for_cursor),
    "cline": ("Cline", install_for_cline),
    "windsurf": ("Windsurf", install_for_windsurf),
}


def run_skill_install(target: str = "all", workspace: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """Execute skill installation for the given target and workspace."""
    src = get_skill_src()
    ws = (workspace or Path.cwd()).resolve()
    installed = []

    targets_to_run = list(TARGET_MAP.keys()) if target == "all" else [target]

    for key in targets_to_run:
        if key not in TARGET_MAP:
            continue
        name, fn = TARGET_MAP[key]
        try:
            if key in ("claude",):
                dest = fn(src)
            elif key in ("antigravity", "hermes", "codex"):
                dest = fn(src, ws)
            else:
                dest = fn(src, ws)
            installed.append((name, dest))
        except Exception as e:
            print(f"[-] {name} install skipped: {e}", file=sys.stderr)

    return installed
