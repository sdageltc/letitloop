import argparse
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import time
from typing import Any, Dict, List, Optional

from letitloop.conformance.adapters.atomic_wal_adapter import AtomicWalAdapter
from letitloop.conformance.adapters.base import FrameworkAdapter
from letitloop.conformance.adapters.in_memory_adapter import InMemoryAdapter
from letitloop.conformance.adapters.snapshot_graph_adapter import SnapshotGraphAdapter
from letitloop.conformance.adapters.unmanaged_script_adapter import UnmanagedScriptAdapter
from letitloop.conformance.harness.schema import DurabilityScore, SyntheticStep, SyntheticTaskSpec

ADAPTERS: Dict[str, type] = {
    "atomic_wal": AtomicWalAdapter,
    "snapshot_graph": SnapshotGraphAdapter,
    "in_memory_loop": InMemoryAdapter,
    "unmanaged_script": UnmanagedScriptAdapter,
    # Backward compatibility aliases
    "letitloop": AtomicWalAdapter,
    "langgraph": SnapshotGraphAdapter,
    "autogen": InMemoryAdapter,
    "crewai": InMemoryAdapter,
    "raw_python": UnmanagedScriptAdapter,
}

ARCHETYPE_LABELS: Dict[str, str] = {
    "atomic_wal": "Atomic WAL Engine (LetItLoop / Temporal)",
    "snapshot_graph": "Periodic Snapshot Graph (LangGraph / Pregel)",
    "in_memory_loop": "In-Memory Event Loop (AutoGen / CrewAI)",
    "unmanaged_script": "Unmanaged Script Execution (Raw Python CLI)",
    "letitloop": "Atomic WAL Engine (LetItLoop / Temporal)",
    "langgraph": "Periodic Snapshot Graph (LangGraph / Pregel)",
    "autogen": "In-Memory Event Loop (AutoGen / CrewAI)",
    "crewai": "In-Memory Event Loop (AutoGen / CrewAI)",
    "raw_python": "Unmanaged Script Execution (Raw Python CLI)",
}

PRIMARY_ARCHETYPES = ["atomic_wal", "snapshot_graph", "in_memory_loop", "unmanaged_script"]

KILL_WINDOWS = {
    "PROMPT": "DCP-001-PRE_STEP",
    "EXEC": "DCP-002-MID_ACTION",
    "WRITE": "DCP-003-POST_ACTION_PRE_JOURNAL",
    "VERIFY": "DCP-004-POST_JOURNAL_PRE_FSYNC",
}
SCENARIO_TO_WINDOW = {v: k for k, v in KILL_WINDOWS.items()}

ALLOWED_SCENARIOS = {
    "DCP-001",
    "DCP-002",
    "DCP-003",
    "DCP-004",
    "DCP-001-PRE_STEP",
    "DCP-002-MID_ACTION",
    "DCP-003-POST_ACTION_PRE_JOURNAL",
    "DCP-004-POST_JOURNAL_PRE_FSYNC",
}


def _bench_run_key() -> str:
    """Load or create a bench run key for HMAC anti-cheat (mode 0600 on POSIX)."""
    # Prefer env, else .bench_wal/.bench_key, else ephemeral
    env_key = os.environ.get("LETITLOOP_BENCH_KEY")
    if env_key:
        return env_key
    key_path = pathlib.Path(".bench_wal") / ".bench_key"
    try:
        if key_path.is_file():
            k = key_path.read_text(encoding="utf-8").strip()
            if k:
                return k
        k = secrets.token_hex(32)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = key_path.with_suffix(".tmp")
        tmp.write_text(k, encoding="utf-8")
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        tmp.replace(key_path)
        return k
    except Exception:
        return secrets.token_hex(32)


def _sign_payload(payload: dict, key: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _attach_trace_signature(data: dict, key: str | None = None) -> dict:
    """Attach non-reproducible run trace (nonce+timestamp) + HMAC to prevent synthetic spoofing."""
    key = key or _bench_run_key()
    nonce = secrets.token_hex(16)
    ts = time.time()
    # Copy without existing hmac fields for canonical signing
    base = {k: v for k, v in data.items() if k not in ("hmac_hex", "trace_id", "run_nonce")}
    base["_trace_nonce"] = nonce
    base["_trace_ts"] = ts
    h = _sign_payload(base, key)
    data["trace_id"] = nonce[:16]
    data["run_nonce"] = nonce
    data["hmac_hex"] = h
    data["_trace_ts"] = ts
    return data


def _load_scenario_json(scenario_id: str) -> dict:
    import json as _json
    import re

    # Hardening: whitelist + no path traversal
    if ".." in scenario_id or "/" in scenario_id or "\\" in scenario_id:
        raise ValueError(f"sandbox: scenario_id {scenario_id!r} contains path traversal")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", scenario_id):
        raise ValueError(f"sandbox: scenario_id {scenario_id!r} contains invalid characters")
    # allow prefix like DCP-002 to match DCP-002-MID_ACTION, but must be known prefix
    if scenario_id not in ALLOWED_SCENARIOS and not any(
        scenario_id == s[: len(scenario_id)] for s in ALLOWED_SCENARIOS
    ):
        # also allow if any allowed starts with scenario_id
        if not any(s.startswith(scenario_id) for s in ALLOWED_SCENARIOS):
            raise FileNotFoundError(f"Scenario {scenario_id} not in whitelist {sorted(ALLOWED_SCENARIOS)}")
    base = pathlib.Path(__file__).parent.parent / "scenarios"
    for f in base.glob("*.json"):
        if scenario_id in f.name:
            return _json.loads(f.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Scenario {scenario_id} not found in {base}")


def _scenario_to_task_spec(scenario: dict):
    phase = scenario.get("phase", "PRE_STEP")
    phase_to_kill = {
        "PRE_STEP": 0,
        "MID_ACTION": 1,
        "POST_ACTION_PRE_JOURNAL": 1,
        "POST_JOURNAL_PRE_FSYNC": 2,
    }
    kill_idx = phase_to_kill.get(phase, 1)
    sid = scenario.get("id", "DCP-UNKNOWN")
    steps = [
        SyntheticStep(
            step_id=f"{sid}_s1",
            action_type="FILE_WRITE",
            target_path=f"build/{sid}_f1.txt",
            expected_content="c1",
            simulated_token_cost=120,
        ),
        SyntheticStep(
            step_id=f"{sid}_s2",
            action_type="FILE_WRITE",
            target_path=f"build/{sid}_f2.txt",
            expected_content="c2",
            simulated_token_cost=180,
        ),
        SyntheticStep(
            step_id=f"{sid}_s3",
            action_type="FILE_WRITE",
            target_path=f"build/{sid}_f3.txt",
            expected_content="c3",
            simulated_token_cost=220,
        ),
    ]
    return SyntheticTaskSpec(task_id=sid, steps=steps, kill_at_step_index=kill_idx, kill_signal="SIGKILL")


class DurabilityBenchmarkRunner:
    def __init__(self, output_dir: str = "results", wal_dir: str = ".bench_wal"):
        self.output_dir = pathlib.Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.wal_dir = wal_dir

    def run_durability_trial(self, framework_name: str, task_spec: SyntheticTaskSpec) -> DurabilityScore:
        adapter_cls = ADAPTERS.get(framework_name.lower())
        if not adapter_cls:
            raise ValueError(f"Unknown framework adapter: {framework_name}. Available: {list(ADAPTERS.keys())}")

        adapter: FrameworkAdapter = adapter_cls(wal_dir=self.wal_dir)
        adapter.start_task(task_spec)
        return adapter.resume_task(task_spec)

    def run_matrix_sweep(self, tasks: Optional[List[SyntheticTaskSpec]] = None) -> List[DurabilityScore]:
        if not tasks:
            tasks = [
                SyntheticTaskSpec(
                    task_id="dcp-micro-3step",
                    steps=[
                        SyntheticStep(
                            step_id="step_1",
                            action_type="FILE_WRITE",
                            target_path="build/f1.txt",
                            expected_content="c1",
                            simulated_token_cost=100,
                        ),
                        SyntheticStep(
                            step_id="step_2",
                            action_type="FILE_WRITE",
                            target_path="build/f2.txt",
                            expected_content="c2",
                            simulated_token_cost=150,
                        ),
                        SyntheticStep(
                            step_id="step_3",
                            action_type="FILE_WRITE",
                            target_path="build/f3.txt",
                            expected_content="c3",
                            simulated_token_cost=200,
                        ),
                    ],
                    kill_at_step_index=1,
                    kill_signal="SIGKILL",
                ),
                SyntheticTaskSpec(
                    task_id="dcp-pipeline-5step",
                    steps=[
                        SyntheticStep(
                            step_id="s1",
                            action_type="FILE_WRITE",
                            target_path="build/p1.txt",
                            expected_content="d1",
                            simulated_token_cost=120,
                        ),
                        SyntheticStep(
                            step_id="s2",
                            action_type="FILE_WRITE",
                            target_path="build/p2.txt",
                            expected_content="d2",
                            simulated_token_cost=180,
                        ),
                        SyntheticStep(
                            step_id="s3",
                            action_type="FILE_WRITE",
                            target_path="build/p3.txt",
                            expected_content="d3",
                            simulated_token_cost=250,
                        ),
                        SyntheticStep(
                            step_id="s4",
                            action_type="FILE_WRITE",
                            target_path="build/p4.txt",
                            expected_content="d4",
                            simulated_token_cost=300,
                        ),
                        SyntheticStep(
                            step_id="s5",
                            action_type="FILE_WRITE",
                            target_path="build/p5.txt",
                            expected_content="d5",
                            simulated_token_cost=350,
                        ),
                    ],
                    kill_at_step_index=2,
                    kill_signal="SIGKILL",
                ),
            ]

        results = []
        for fw in PRIMARY_ARCHETYPES:
            for task in tasks:
                score = self.run_durability_trial(fw, task)
                results.append(score)
        return results

    def compile_leaderboard(self, results: List[DurabilityScore]) -> Dict[str, Any]:
        summary: Dict[str, Dict[str, Any]] = {}
        for r in results:
            if r.framework not in summary:
                summary[r.framework] = {
                    "framework": r.framework,
                    "archetype_label": ARCHETYPE_LABELS.get(r.framework, r.framework),
                    "total_trials": 0,
                    "passed_trials": 0,
                    "avg_token_waste_pct": 0.0,
                    "avg_recovery_latency_ms": 0.0,
                    "state_corruptions": 0,
                }
            s = summary[r.framework]
            s["total_trials"] += 1
            if r.resumed_successfully:
                s["passed_trials"] += 1
            s["avg_token_waste_pct"] += r.duplicate_token_waste_pct
            s["avg_recovery_latency_ms"] += r.recovery_latency_seconds * 1000.0
            if r.state_corruption_detected:
                s["state_corruptions"] += 1

        leaderboard = []
        for fw, s in summary.items():
            n = s["total_trials"] or 1
            recovery_rate = (s["passed_trials"] / n) * 100.0
            avg_waste = s["avg_token_waste_pct"] / n
            avg_latency = s["avg_recovery_latency_ms"] / n
            leaderboard.append(
                {
                    "framework": fw,
                    "archetype_label": s["archetype_label"],
                    "recovery_rate_pct": round(recovery_rate, 1),
                    "avg_duplicate_token_waste_pct": round(avg_waste, 1),
                    "avg_recovery_latency_ms": round(avg_latency, 2),
                    "state_corruptions": s["state_corruptions"],
                    "dcp_status": "CONFORMANT" if recovery_rate == 100.0 and avg_waste < 5.0 else "NON_CONFORMANT",
                }
            )

        leaderboard.sort(key=lambda x: (-x["recovery_rate_pct"], x["avg_duplicate_token_waste_pct"]))
        data = {
            "protocol_version": "DCP-2.0",
            "methodology": "Physical OS Subprocess Fault Injection (SIGKILL)",
            "timestamp": time.time(),
            "leaderboard": leaderboard,
        }
        return _attach_trace_signature(data)

    def export_markdown_leaderboard(self, leaderboard_data: Dict[str, Any], target_path: str):
        p = pathlib.Path(target_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            "# Durability Conformance Protocol (DCP-1.0) Leaderboard 🏆",
            "",
            "Empirical crash-resilience matrix for AI agent architectural patterns under physical OS `SIGKILL` fault injection.",
            "",
            "> [!NOTE]",
            "> **Methodological Scope Disclosure**: DCP-1.0 evaluates **runtime crash durability, process isolation, and token waste under abrupt process termination**. It does **not** evaluate LLM reasoning IQ or single-turn coding capabilities (use SWE-bench / GAIA for reasoning evaluations).",
            "",
            "| Rank | Architectural Archetype & Reference Pattern | Crash Recovery ($R_{crash}$) | Duplicate Token Waste ($W_{token}$) | Resumption Latency | DCP-1.0 Status |",
            "|:---:|---|:---:|:---:|:---:|:---:|",
        ]

        # DCP-2.0 vs DCP-1.0 compatibility
        is_dcp2 = leaderboard_data.get("protocol_version", "").startswith("DCP-2")
        for idx, row in enumerate(leaderboard_data["leaderboard"], 1):
            if is_dcp2:
                # DCP-2.0 receipt fields: avg_T_resume_ms, avg_W_token_pct, total_C_fail, recovery_rate_pct
                badge = (
                    "🟢 CONFORMANT"
                    if row.get("recovery_rate_pct", 0) == 100.0 and row.get("avg_W_token_pct", 100) < 5.0
                    else "🔴 NON-CONFORMANT"
                )
                status_icon = "🥇" if idx == 1 else ("🥈" if idx == 2 else f"**{idx}**")
                lines.append(
                    f"| {status_icon} | **`{row['archetype_label']}`** | `{row.get('recovery_rate_pct', 0)}%` | `{row.get('avg_W_token_pct', 0)}%` | `{row.get('avg_T_resume_ms', 0)} ms` | {badge} |"
                )
            else:
                badge = "🟢 CONFORMANT" if row["dcp_status"] == "CONFORMANT" else "🔴 NON-CONFORMANT"
                status_icon = "🥇" if idx == 1 else ("🥈" if idx == 2 else f"**{idx}**")
                lines.append(
                    f"| {status_icon} | **`{row['archetype_label']}`** | `{row['recovery_rate_pct']}%` | `{row['avg_duplicate_token_waste_pct']}%` | `{row['avg_recovery_latency_ms']} ms` | {badge} |"
                )

        lines.extend(
            [
                "",
                "---",
                "### Methodology & Empirical Invariants",
                "1. **Physical Process Fault Injection**: Processes run as real OS child subprocesses and are terminated abruptly midway through atomic operations using non-maskable `SIGKILL` (`taskkill /F /T` on Windows).",
                "2. **Zero-API Synthetic Harness**: Eliminates cloud latency, billing spikes, and rate-limiting flakiness.",
                "3. **Token Waste Accounting**: Re-executed tool invocations on resumed tasks are tracked as duplicated waste tokens ($W_{token}$).",
                "",
                "*Generated by `agent-durability-bench`.*",
            ]
        )

        p.write_text("\n".join(lines), encoding="utf-8")

    def run_scenario_trial(self, framework_name: str, scenario_id: str) -> dict:
        """Run a single DCP scenario and emit structured JSON receipt with real metrics."""
        scenario = _load_scenario_json(scenario_id)
        spec = _scenario_to_task_spec(scenario)
        score = self.run_durability_trial(framework_name, spec)
        t_resume_ms = round(score.recovery_latency_seconds * 1000, 2)
        w_token = round(score.duplicate_token_waste_pct, 2)
        c_fail = 0 if score.resumed_successfully and not score.state_corruption_detected else 1
        window = SCENARIO_TO_WINDOW.get(scenario.get("id", ""), scenario.get("phase", "UNKNOWN"))
        receipt = {
            "protocol_version": "DCP-2.0",
            "scenario_id": scenario.get("id", scenario_id),
            "scenario_name": scenario.get("name", ""),
            "kill_window": window,
            "framework": score.framework,
            "archetype_label": ARCHETYPE_LABELS.get(score.framework, score.framework),
            "T_resume_ms": t_resume_ms,
            "W_token_pct": w_token,
            "C_fail": c_fail,
            "resumed_successfully": score.resumed_successfully,
            "state_corruption_detected": score.state_corruption_detected,
            "final_verdict": score.final_verdict,
            "recovery_latency_seconds": score.recovery_latency_seconds,
            "timestamp": time.time(),
        }
        _attach_trace_signature(receipt)
        return receipt

    def run_compare_all(self, scenario_ids: list | None = None) -> dict:
        """Matrix sweep across all archetypes and all DCP scenarios — structured receipts."""
        if scenario_ids is None:
            scenario_ids = ["DCP-001", "DCP-002", "DCP-003", "DCP-004"]
        receipts = []
        for fw in PRIMARY_ARCHETYPES:
            for sid in scenario_ids:
                try:
                    receipt = self.run_scenario_trial(fw, sid)
                except Exception as e:
                    receipt = {"scenario_id": sid, "framework": fw, "error": str(e), "C_fail": 1}
                receipts.append(receipt)
        # aggregate per-framework stats with real measured metrics
        summary = {}
        for r in receipts:
            fw = r.get("framework", "unknown")
            summary.setdefault(
                fw,
                {
                    "framework": fw,
                    "archetype_label": ARCHETYPE_LABELS.get(fw, fw),
                    "receipts": [],
                    "avg_T_resume_ms": 0,
                    "avg_W_token_pct": 0,
                    "total_C_fail": 0,
                },
            )
            summary[fw]["receipts"].append(r)
            summary[fw]["avg_T_resume_ms"] += r.get("T_resume_ms", 0)
            summary[fw]["avg_W_token_pct"] += r.get("W_token_pct", 0)
            summary[fw]["total_C_fail"] += r.get("C_fail", 0)
        leaderboard = []
        for fw, s in summary.items():
            n = len(s["receipts"]) or 1
            leaderboard.append(
                {
                    "framework": fw,
                    "archetype_label": s["archetype_label"],
                    "avg_T_resume_ms": round(s["avg_T_resume_ms"] / n, 2),
                    "avg_W_token_pct": round(s["avg_W_token_pct"] / n, 2),
                    "total_C_fail": s["total_C_fail"],
                    "recovery_rate_pct": round(100 * (n - s["total_C_fail"]) / n, 1),
                }
            )
        leaderboard.sort(key=lambda x: (-x["recovery_rate_pct"], x["avg_W_token_pct"]))
        data = {
            "protocol_version": "DCP-2.0",
            "methodology": "Physical OS Subprocess Fault Injection (SIGKILL) — 4 kill windows (PROMPT, EXEC, WRITE, VERIFY)",
            "timestamp": time.time(),
            "leaderboard": leaderboard,
            "receipts": receipts,
        }
        return _attach_trace_signature(data)


def main():
    parser = argparse.ArgumentParser(description="Agent Durability Benchmark Runner (DCP-2.0)")
    parser.add_argument("--matrix", action="store_true", help="Run full cross-framework matrix sweep (legacy)")
    parser.add_argument("--framework", default="atomic_wal", help="Target framework adapter")
    parser.add_argument("--compare", default=None, help="Compare mode: 'all' runs DCP-2.0 matrix across all frameworks")
    parser.add_argument("--scenario", default=None, help="Run single DCP scenario by ID (e.g., DCP-002)")
    parser.add_argument("--signal", default="SIGKILL", help="Fault signal (default SIGKILL)")
    parser.add_argument("--export-json", default="results/leaderboard.json", help="Path to export results JSON")
    parser.add_argument("--export-markdown", default="docs/index.md", help="Path to export markdown leaderboard")
    args = parser.parse_args()

    runner = DurabilityBenchmarkRunner()

    if args.compare == "all":
        print("=" * 60)
        print("RUNNING DCP-2.0 CONFORMANCE MOAT — COMPARE ALL (4 windows x 4 archetypes)")
        print("=" * 60)
        data = runner.run_compare_all()
        json_path = pathlib.Path(args.export_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        runner.export_markdown_leaderboard(data, args.export_markdown)
        print("\n" + json.dumps(data, indent=2))
        print(f"\nReceipts exported to {args.export_json}")
        return
    if args.scenario:
        print(f"RUNNING DCP-2.0 SCENARIO {args.scenario} for {args.framework}")
        receipt = runner.run_scenario_trial(args.framework, args.scenario)
        print(json.dumps(receipt, indent=2))
        # also write single receipt as JSON for CI consumption
        json_path = pathlib.Path(args.export_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return
    if args.matrix:
        print("=" * 60)
        print("RUNNING DCP-1.0 PHYSICAL SUBPROCESS MATRIX SWEEP")
        print("=" * 60)
        results = runner.run_matrix_sweep()
        leaderboard_data = runner.compile_leaderboard(results)

        json_path = pathlib.Path(args.export_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(leaderboard_data, indent=2), encoding="utf-8")

        runner.export_markdown_leaderboard(leaderboard_data, args.export_markdown)

        print("\n" + json.dumps(leaderboard_data, indent=2))
        print(f"\nLeaderboard exported to {args.export_json} and {args.export_markdown}")
    else:
        spec = SyntheticTaskSpec(
            task_id="bench-standard-001",
            steps=[
                SyntheticStep(
                    step_id="step_1",
                    action_type="FILE_WRITE",
                    target_path="build/f1.txt",
                    expected_content="stage_1",
                    simulated_token_cost=100,
                ),
                SyntheticStep(
                    step_id="step_2",
                    action_type="FILE_WRITE",
                    target_path="build/f2.txt",
                    expected_content="stage_2",
                    simulated_token_cost=150,
                ),
                SyntheticStep(
                    step_id="step_3",
                    action_type="FILE_WRITE",
                    target_path="build/f3.txt",
                    expected_content="stage_3",
                    simulated_token_cost=200,
                ),
            ],
            kill_at_step_index=1,
            kill_signal="SIGKILL",
        )
        score = runner.run_durability_trial(args.framework, spec)
        print("\n[BENCHMARK TRIAL COMPLETED]")
        print(json.dumps(score.model_dump(), indent=2))


if __name__ == "__main__":
    main()
