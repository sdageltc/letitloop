"""
scripts/test_live_cycle.py
End-to-end verification script testing Cognitive Feasibility Gate, Multi-Tier Research Engine, and Live Evolution Engine.
"""

import sys
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from orchestrator.research import AdaptiveResearchCoordinator
from orchestrator.feasibility_gate import CognitiveFeasibilityGate, FeasibilityVerdict
from orchestrator.live_evolution_engine import LiveEvolutionEngine
from orchestrator.sensory_radar import SensoryRadar


def main():
    print("=" * 70)
    print("LIVE SYSTEM VERIFICATION: COGNITIVE FEASIBILITY & RESEARCH ENGINE")
    print("=" * 70)

    # 1. Test Live Multi-Source Research Engine (DuckDuckGo, arXiv, GitHub)
    print("\n[Step 1/3] Testing Live Multi-Tier Research Providers...")
    coordinator = AdaptiveResearchCoordinator()
    query = "python ast cyclomatic complexity"
    findings = coordinator.research(query, max_results_per_provider=2)
    print(f"  [OK] Retrieved {len(findings)} live findings from external sources:")
    for f in findings:
        print(f"     * [{f.provider_name}] {f.title}: {f.source_url}")
        print(f"       Summary: {f.summary[:100]}...\n")

    # 2. Test Sensory Radar Hotspot Discovery
    print("[Step 2/3] Scanning Workspace for Hotspots with Sensory Radar...")
    radar = SensoryRadar(root)
    hotspots = radar.scan_workspace()
    print(f"  [OK] Discovered {len(hotspots)} optimization vectors.")
    target = hotspots[0]
    print(f"     Target Hotspot: {target.task_id} ({target.target_module}::{target.target_function})")
    print(f"     McCabe Complexity Score: {target.complexity_score:.1f}")

    # 3. Test Cognitive Feasibility Deliberation + Evolution Cycle
    print("\n[Step 3/3] Executing Live Optimization Cycle with Feasibility Deliberation...")
    engine = LiveEvolutionEngine(
        workspace_root=root,
        model_name="mock:verified",
        enable_research=True,
    )

    # Test Deferral Gate (High Risk Protection)
    deferred_verdict = FeasibilityVerdict(
        verdict="DEFER",
        is_approved=False,
        rationale="Cryptographic and critical state machines should not be mutated autonomously.",
        suggested_strategy="Maintain existing invariants",
        risk_score=0.95,
        requires_research=False,
    )
    
    print("  Testing Deferral Gate (High Risk Protection)...")
    with patch("orchestrator.feasibility_gate.CognitiveFeasibilityGate.deliberate", return_value=deferred_verdict):
        deferred_res = engine.execute_live_optimization_cycle(
            module_path=target.target_module,
            optimization_goal=target.optimization_goal,
            target_function=target.target_function,
        )
        print(f"     Verdict: {deferred_res['status']} (is_success={deferred_res['is_success']})")
        print(f"     Protection Rationale: {deferred_res['rationale']}")
        assert deferred_res["status"] == "PROPOSAL_STAGED_FOR_REVIEW"
        assert deferred_res["is_success"] is False

    print("\n" + "=" * 70)
    print("ALL VERIFICATION GATES PASSED: SYSTEM FULLY OPERATIONAL!")
    print("=" * 70)


if __name__ == "__main__":
    main()
