#!/usr/bin/env python3
"""Landscape Seam Ledger (LSL) Automated Verification Runner.

Verifies all declared ecosystem seams across core engine, landing page,
and satellite repositories prior to releases or completion declarations.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / ".github" / "landscape_seams.json"
if not LEDGER_PATH.is_file():
    LEDGER_PATH = ROOT / ".letitloop" / "landscape_seams.json"


def run_command(cmd: str, cwd: Path) -> tuple[int, str]:
    res = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True)
    out = (res.stdout + "\n" + res.stderr).strip()
    return res.returncode, out


def verify_seams() -> int:
    if not LEDGER_PATH.is_file():
        print(f"[LSL:FAIL] Ledger missing at {LEDGER_PATH}")
        return 1

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    seams = data.get("seams", [])
    print(f"=== Running Landscape Seam Ledger (LSL) Gate: {len(seams)} seams ===")

    failures = 0
    for s in seams:
        s_id = s.get("id")
        s_type = s.get("type")
        print(f"\n[SEAM: {s_id}] ({s_type})")

        if s_type == "content_hygiene":
            target = ROOT / s["target_file"]
            if not target.is_file():
                print(f"  [FAIL] Target file missing: {target}")
                failures += 1
                continue
            content = target.read_text(encoding="utf-8")
            forbidden = s.get("forbidden_tokens", [])
            violated = [t for t in forbidden if t in content]
            if violated:
                print(f"  [FAIL] Forbidden tokens found: {violated}")
                failures += 1
            else:
                print("  [OK] Content hygiene verified: 0 forbidden tokens.")

        elif s_type in ("unit_tests", "integration_script"):
            cmd = s["command"]
            code, out = run_command(cmd, ROOT)
            expected = s.get("expected_exit_code", 0)
            if code == expected:
                print(f"  [OK] Command exited {code} as expected: `{cmd}`")
            else:
                print(f"  [FAIL] Command failed with code {code} (expected {expected}): `{cmd}`")
                print(f"     Output: {out[:200]}")
                failures += 1

        elif s_type == "satellite_repo":
            sub_path = (ROOT / s["path"]).resolve()
            if not sub_path.is_dir():
                print(f"  [WARN] Satellite dir missing: {sub_path} (skipped)")
                continue
            cmd = s["command"]
            code, out = run_command(cmd, sub_path)
            expected = s.get("expected_exit_code", 0)
            if code == expected:
                print(f"  [OK] Satellite `{s.get('scope')}` command exited {code}: `{cmd}`")
            else:
                print(f"  [FAIL] Satellite `{s.get('scope')}` command failed ({code}): `{cmd}`")
                print(f"     Output: {out[:200]}")
                failures += 1

    print("\n" + "=" * 60)
    if failures == 0:
        print("[SUCCESS] ALL LANDSCAPE SEAMS VERIFIED GREEN.")
        return 0
    else:
        print(f"[ERROR] {failures} LANDSCAPE SEAM(S) FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(verify_seams())
