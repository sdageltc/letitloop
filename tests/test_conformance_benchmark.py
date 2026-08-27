"""Tests for DCP-2.0 Conformance Moat — demo + bench matrix + anti-cheat HMAC (fast)."""

import json
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.fast


def test_lil_demo_runs_in_under_10s():
    """lil demo must exit 0 in <10s on Windows/macOS/Linux."""
    import time

    t0 = time.time()
    res = subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "demo"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    elapsed = time.time() - t0
    assert res.returncode == 0, f"demo failed: {res.stdout}\n{res.stderr}"
    assert elapsed < 10.0, f"demo took {elapsed:.1f}s >10s"
    assert "LetItLoop Demo" in res.stdout
    assert "PASS" in res.stdout or "resume" in res.stdout.lower()


def test_lil_bench_compare_all_outputs_verified_json_receipts():
    """lil bench --compare all must produce verified JSON receipts (fast minimal matrix)."""
    from letitloop.conformance.harness.runner import DurabilityBenchmarkRunner

    runner = DurabilityBenchmarkRunner(
        output_dir=str(pathlib.Path("results")), wal_dir=str(pathlib.Path(".bench_wal_test_fast"))
    )
    data = runner.run_compare_all(scenario_ids=["DCP-001"])
    assert data["protocol_version"] == "DCP-2.0"
    assert "leaderboard" in data
    assert "receipts" in data
    assert len(data["receipts"]) == 4, f"expected 4 receipts for single scenario, got {len(data['receipts'])}"
    assert "hmac_hex" in data
    assert len(data["hmac_hex"]) == 64
    for r in data["receipts"]:
        assert "scenario_id" in r
        assert "T_resume_ms" in r
        assert "W_token_pct" in r
        assert "C_fail" in r
        assert "hmac_hex" in r
    assert len(data["leaderboard"]) == 4


def test_bench_cli_compare_all_via_subprocess():
    """End-to-end CLI: lil bench --scenario DCP-001 exits 0 and writes JSON (fast single receipt)."""
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.cli",
            "bench",
            "--scenario",
            "DCP-001",
            "--json",
            "results/test_bench_fast.json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, f"bench failed: {res.stdout}\n{res.stderr}"
    p = pathlib.Path("results/test_bench_fast.json")
    assert p.is_file(), "bench did not write JSON"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["protocol_version"] == "DCP-2.0"
    # Single scenario via --scenario produces single receipt (or 1 receipt with HMAC)
    assert "scenario_id" in data or "receipts" in data
    assert "hmac_hex" in data or "T_resume_ms" in data


def test_reporter_svg_and_html_generation(tmp_path):
    """Reporter must generate deterministic SVG badges and HTML (no bench run, dummy data)."""
    from letitloop.conformance.reporter import generate_svg_badge, write_badges, write_html_report

    # Dummy leaderboard (deterministic, no harness run)
    data = {
        "protocol_version": "DCP-2.0",
        "timestamp": 1234567890,
        "leaderboard": [
            {
                "framework": "atomic_wal",
                "archetype_label": "Atomic WAL Engine (LetItLoop / Temporal)",
                "recovery_rate_pct": 100.0,
                "avg_W_token_pct": 0.0,
                "avg_T_resume_ms": 12.3,
                "total_C_fail": 0,
            },
            {
                "framework": "snapshot_graph",
                "archetype_label": "Periodic Snapshot Graph (LangGraph / Pregel)",
                "recovery_rate_pct": 0.0,
                "avg_W_token_pct": 75.0,
                "avg_T_resume_ms": 150.0,
                "total_C_fail": 4,
            },
        ],
        "receipts": [],
    }

    # SVG deterministic
    svg1 = generate_svg_badge("durability", "100%", "#4c1")
    svg2 = generate_svg_badge("durability", "100%", "#4c1")
    assert svg1 == svg2
    assert "<svg" in svg1

    # Write badges
    out_dir = tmp_path / "badges"
    write_badges(data, out_dir=str(out_dir))
    assert (out_dir / "durability_score.svg").is_file()
    assert (out_dir / "token_waste.svg").is_file()

    # HTML deterministic
    html_path = tmp_path / "index.html"
    write_html_report(html_path, data)
    assert html_path.is_file()
    html_content = html_path.read_text(encoding="utf-8")
    assert "DCP-2.0" in html_content
    assert "Leaderboard" in html_content
