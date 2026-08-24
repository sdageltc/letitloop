"""
scripts/run_live_self_evolution.py
CLI runner for LetItLoop live self-evolution cycles with Cognitive Feasibility Deliberation, Adaptive Research, and HITL Proposal Ledger.
"""

import argparse
import json
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from orchestrator.feasibility_gate import CognitiveFeasibilityGate
from orchestrator.live_evolution_engine import LiveEvolutionEngine
from orchestrator.proposal_ledger import ProposalLedger
from orchestrator.sensory_radar import SensoryRadar


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s}s"


def main():
    parser = argparse.ArgumentParser(
        description="LetItLoop Live Self-Evolution Runner & Human-in-the-Loop Decision Matrix"
    )
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=0.0,
        help="Run self-evolution loop continuously for specified duration in minutes",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Maximum iterations to execute (if duration-minutes is not set)",
    )
    parser.add_argument(
        "--model", type=str, default="cli:agy", help="LLM model identifier"
    )
    parser.add_argument(
        "--enable-research",
        action="store_true",
        help="Enable external multi-tier research provider (DuckDuckGo, arXiv, GitHub API)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform discovery scan and feasibility evaluation without mutating",
    )
    parser.add_argument(
        "--approve-proposal",
        type=str,
        default="",
        help="Approve and execute a staged architectural proposal by ID",
    )
    parser.add_argument(
        "--list-proposals",
        action="store_true",
        help="List all staged architectural proposals awaiting human review",
    )
    args = parser.parse_args()

    ledger = ProposalLedger(root)

    # 1. Handle Listing Staged Proposals
    if args.list_proposals:
        proposals = ledger.list_proposals()
        print("\n" + "=" * 70)
        print(f"STAGED ARCHITECTURAL PROPOSALS ({len(proposals)} Total)")
        print("=" * 70)
        if not proposals:
            print("  No pending architectural proposals.")
        for p in proposals:
            print(f"\n  * [{p.status}] {p.proposal_id}")
            print(f"    Symbol: {p.target_module}::{p.target_function}")
            print(
                f"    Risk Score: {p.risk_score:.2f} | Strategy: {p.suggested_strategy}"
            )
            print(
                f"    Proposal Document: scratch/evolution_state/proposals/{p.proposal_id}.md"
            )
            print(
                f"    Approve Command: python scripts/run_live_self_evolution.py --approve-proposal {p.proposal_id}"
            )
        print("\n" + "=" * 70)
        sys.exit(0)

    # 2. Handle Executing an Approved Proposal
    if args.approve_proposal:
        proposal_id = args.approve_proposal.strip()
        proposal = ledger.get_proposal(proposal_id)
        if not proposal:
            print(f"[-] Proposal '{proposal_id}' not found in ledger.")
            sys.exit(1)

        print("\n" + "=" * 70)
        print(f"EXECUTING HUMAN-APPROVED PROPOSAL: {proposal.proposal_id}")
        print(f"   Target: {proposal.target_module}::{proposal.target_function}")
        print(f"   Strategy: {proposal.suggested_strategy}")
        print("=" * 70)

        engine = LiveEvolutionEngine(
            workspace_root=root,
            model_name=args.model,
            enable_research=args.enable_research,
        )

        ledger.mark_status(proposal_id, "APPROVED")
        res = engine.execute_live_optimization_cycle(
            module_path=proposal.target_module,
            optimization_goal=f"Human-approved refactoring: {proposal.suggested_strategy}",
            target_function=proposal.target_function,
            force_approved=True,
        )

        if res.get("is_success"):
            ledger.mark_status(proposal_id, "EXECUTED")
            print(
                f"\n[OK] PROPOSAL EXECUTED & VERIFIED IN SANDBOX (Status: {res['status']})"
            )
        else:
            ledger.mark_status(proposal_id, "FAILED_VERIFICATION")
            print(f"\n[-] PROPOSAL EXECUTION FAILED (Status: {res['status']})")
            if "violations" in res:
                for v in res["violations"]:
                    print(f"   Violation: {v}")
        sys.exit(0 if res.get("is_success") else 1)

    # 3. Standard Autonomous Self-Evolution Loop
    radar = SensoryRadar(root)
    tasks = radar.scan_workspace()
    print(
        f"[SensoryRadar] Discovered {len(tasks)} evolutionary hotspot vectors."
    )

    if args.dry_run:
        print("\n[Dry Run - Discovered Hotspots & Initial Targets]:")
        for t in tasks[: args.max_iterations]:
            print(
                f"  * {t.task_id} ({t.target_module}::{t.target_function}) - Score: {t.complexity_score:.1f}"
            )
        sys.exit(0)

    engine = LiveEvolutionEngine(
        workspace_root=root,
        model_name=args.model,
        enable_research=args.enable_research,
    )
    telemetry_dir = root / "scratch/evolution_state"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    telemetry_file = telemetry_dir / "live_evolution_telemetry.jsonl"

    applied_mutations = []
    staged_proposals = []
    failed_attempts = []

    start_time = time.time()
    deadline = (
        start_time + (args.duration_minutes * 60)
        if args.duration_minutes > 0
        else float("inf")
    )
    max_count = (
        len(tasks) if args.duration_minutes > 0 else args.max_iterations
    )

    print("\n" + "=" * 70)
    print(
        f"STARTING LIVE SELF-EVOLUTION LOOP "
        f"({'Duration: ' + str(args.duration_minutes) + ' mins' if args.duration_minutes > 0 else 'Max Iterations: ' + str(args.max_iterations)})"
    )
    print(f"Backend Model: {args.model} | Research: {args.enable_research}")
    print("=" * 70)

    iteration = 0
    while iteration < len(tasks) and iteration < max_count:
        if time.time() >= deadline:
            print(f"\n[Timer] Reached duration limit of {args.duration_minutes} minutes. Concluding loop.")
            break

        task = tasks[iteration]
        iteration += 1
        elapsed = time.time() - start_time
        remaining = deadline - time.time() if args.duration_minutes > 0 else 0

        time_str = (
            f" [Elapsed: {format_duration(elapsed)} | Remaining: {format_duration(max(0, remaining))}]"
            if args.duration_minutes > 0
            else f" [Elapsed: {format_duration(elapsed)}]"
        )

        print(
            f"\n[Iteration {iteration}]{time_str} Evaluating & Evolving {task.target_module}::{task.target_function} (Score: {task.complexity_score:.1f})..."
        )
        iter_t0 = time.time()
        res = engine.execute_live_optimization_cycle(
            module_path=task.target_module,
            optimization_goal=task.optimization_goal,
            target_function=task.target_function,
        )
        iter_dur = time.time() - iter_t0

        print(
            f"  Result: {res['status']} in {format_duration(iter_dur)} (Success: {res.get('is_success', False)})"
        )
        if "rationale" in res:
            clean_rat = res['rationale'].encode('ascii', errors='replace').decode('ascii')
            print(f"  Deliberation Rationale: {clean_rat}")

        if res.get("is_success"):
            applied_mutations.append(task.task_id)
            print(f"  [MUTATION APPLIED] Complexity reduced & verified in fast sandbox!")
        elif res.get("status") == "PROPOSAL_STAGED_FOR_REVIEW":
            staged_proposals.append(res)
            print(f"  [PROPOSAL STAGED] Risk score={res.get('risk_score', 1.0):.2f}. Awaiting human review.")
        else:
            failed_attempts.append(task.task_id)

        with open(telemetry_file, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "iteration": iteration,
                        "task": task.task_id,
                        "result": res,
                        "duration_s": iter_dur,
                        "timestamp": time.time(),
                    }
                )
                + "\n"
            )

    total_elapsed = time.time() - start_time

    # 4. Executive Decision Report
    print("\n" + "=" * 70)
    print("EXECUTIVE SELF-EVOLUTION DECISION REPORT")
    print("=" * 70)
    print(f"Total Execution Time: {format_duration(total_elapsed)}")
    print(f"Total Iterations Completed: {iteration}")
    print(f"Autonomous Mutations Verified & Applied: {len(applied_mutations)}")
    print(f"Architectural Proposals Staged for Review: {len(staged_proposals)}")
    print(f"Exhausted / Refactoring Attempts: {len(failed_attempts)}")

    if applied_mutations:
        print("\nAPPLIED & VERIFIED CODE MUTATIONS:")
        for am in applied_mutations:
            print(f"  * [VERIFIED] {am}")

    if staged_proposals:
        print("\nPENDING HUMAN APPROVAL DECISIONS:")
        for sp in staged_proposals:
            p_id = sp.get("proposal_id", "Unknown")
            print(f"\n  * Proposal ID: {p_id}")
            print(
                f"    Risk Score: {sp.get('risk_score', 1.0):.2f} ({sp.get('verdict', 'DEFER')})"
            )
            clean_rat = sp.get('rationale', '').encode('ascii', errors='replace').decode('ascii')
            print(f"    Rationale: {clean_rat}")
            print(f"    Review Doc: scratch/evolution_state/proposals/{p_id}.md")
            print(
                f'    Approve Command: python scripts/run_live_self_evolution.py --approve-proposal "{p_id}"'
            )

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
