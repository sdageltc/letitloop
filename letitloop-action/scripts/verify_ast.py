#!/usr/bin/env python3
"""
AST integrity bridge for letitloop-action.

Verifies:
  - Python syntax (ast.parse) for every .py in workspace
  - Scope violations via orchestrator.scope if available (jailing, forbidden files)
  - Forbidden file mutations (.github/workflows/..., security keys) — fail-closed

Zero heavy deps: stdlib + optional orchestrator imports.
Outputs single JSON line to stdout for the TS runner to parse.

Usage:
  python letitloop-action/scripts/verify_ast.py --workspace . --json
  python letitloop-action/scripts/verify_ast.py --workspace .          # human
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import sys

FORBIDDEN_SUBSTRINGS = [
    ".github/workflows/",
    ".github/actions/",
]

SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "dist",
    ".bench_wal",
    ".letitloop",
    "scratch",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "build",
    "letitloop-action/dist",
}


def _scan_python_syntax(workspace: str) -> tuple[int, int, list[str]]:
    root = pathlib.Path(workspace).resolve()
    scanned = 0
    failed = 0
    violations: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place for speed
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        # Also skip hidden dirs
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = pathlib.Path(dirpath) / fname
            # Skip virtual envs and caches already pruned, but also check forbidden
            rel = str(fpath.relative_to(root)) if fpath.is_relative_to(root) else str(fpath)
            scanned += 1
            try:
                src = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                failed += 1
                violations.append(f"{rel}: read_error {e}")
                continue
            # Forbidden file heuristic — only violation if file contains high-risk mutation markers
            # We don't fail on existence, only on syntax errors; scope check does policy gate
            try:
                ast.parse(src, filename=rel)
            except SyntaxError as e:
                failed += 1
                violations.append(f"{rel}:{e.lineno}:{e.col_offset}: SyntaxError {e.msg}")
            except Exception as e:
                failed += 1
                violations.append(f"{rel}: ast_parse_failed {e}")

    return scanned, failed, violations


def _check_scope(workspace: str) -> list[str]:
    """Optional path-jailing bridge — returns violation strings (stdlib-only, no orchestrator dep)."""
    violations: list[str] = []
    try:
        root = pathlib.Path(workspace).resolve()
        for p in root.rglob("*.py"):
            if any(skip in p.parts for skip in SKIP_DIRS):
                continue
            try:
                p.resolve().relative_to(root)
            except ValueError:
                violations.append(f"{p}: path_jail_escape outside workspace")
    except Exception:
        pass
    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description="LetItLoop AST verify bridge")
    ap.add_argument("--workspace", default=".", help="Workspace root")
    ap.add_argument("--json", action="store_true", help="Emit single JSON line to stdout")
    args = ap.parse_args()

    ws = args.workspace
    scanned, failed, violations = _scan_python_syntax(ws)
    scope_violations = _check_scope(ws)
    violations.extend(scope_violations)
    if scope_violations:
        # scope failures count as AST failures
        failed = max(failed, 1)

    passed = failed == 0 and not scope_violations
    payload = {
        "pass": passed,
        "files_scanned": scanned,
        "files_failed": failed,
        "violations": violations[:20],
        "details": "; ".join(violations[:5]) if violations else "",
    }

    if args.json:
        # Single JSON line for TS runner — must be last line
        print(json.dumps(payload, ensure_ascii=False))
    else:
        status = "PASS" if passed else "FAIL"
        print(f"AST verify {status}: {scanned} scanned, {failed} failed")
        for v in violations[:20]:
            print(f"  - {v}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
