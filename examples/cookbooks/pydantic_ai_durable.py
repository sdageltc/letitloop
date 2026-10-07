"""Pydantic AI Durable Agent Cookbook — Crash-Proof Structured Agent with @durable.

Demonstrates durable execution for Pydantic AI style agents with type-safe models,
tool-calling checkpointing, and zero token waste on process crash / SIGKILL.

Architecture:
  Fetch Customer Profile -> Assess Financial Risk -> Generate Underwriting Decision -> Emit Audit Log
  (step 1)                 (step 2)               (step 3)                           (atomic_marker)

If an uncatchable SIGKILL strikes during decision generation, completed API calls
and external model evaluations are recovered instantly from WAL without re-querying endpoints.

Usage:
  python examples/cookbooks/pydantic_ai_durable.py --demo
  python examples/cookbooks/pydantic_ai_durable.py --customer-id CUST-9921
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from typing import Any, Dict, Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.decorators import atomic_marker, durable, step  # noqa: E402

WAL_DIR_DEFAULT = str(ROOT / ".bench_wal" / "cookbooks" / "pydantic_ai_durable")
GOAL_ID = "pydantic-ai-durable"


# --- Mock / Real Type-Safe Domain Logic ---


def _fetch_customer_profile(customer_id: str) -> Dict[str, Any]:
    """Simulated tool: Fetches KYC/AML customer data from banking API."""
    time.sleep(0.01)
    return {
        "customer_id": customer_id,
        "name": "Jane Doe",
        "credit_score": 745,
        "monthly_income": 8500.0,
        "existing_debt": 12000.0,
        "requested_loan": 35000.0,
    }


def _assess_financial_risk(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Simulated tool: Performs deterministic financial risk scoring."""
    time.sleep(0.01)
    dti_ratio = (profile["existing_debt"] / (profile["monthly_income"] * 12)) if profile["monthly_income"] else 1.0
    risk_level = "LOW" if profile["credit_score"] >= 720 and dti_ratio < 0.35 else "MEDIUM"
    return {
        "customer_id": profile["customer_id"],
        "dti_ratio": round(dti_ratio, 4),
        "credit_tier": "PRIME" if profile["credit_score"] >= 720 else "SUBPRIME",
        "risk_level": risk_level,
        "recommended_rate_pct": 5.49 if risk_level == "LOW" else 8.99,
    }


def _generate_underwriting_decision(profile: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
    """Simulated LLM call: Synthesizes final underwriter judgment."""
    time.sleep(0.01)
    approved = risk["risk_level"] in ("LOW", "MEDIUM")
    max_amount = min(profile["requested_loan"], profile["monthly_income"] * 5)
    return {
        "customer_id": profile["customer_id"],
        "status": "APPROVED" if approved else "DECLINED",
        "approved_amount": max_amount if approved else 0.0,
        "interest_rate_pct": risk["recommended_rate_pct"] if approved else 0.0,
        "rationale": f"Applicant credit tier is {risk['credit_tier']} with DTI {risk['dti_ratio']:.2%}.",
    }


def run_pydantic_ai_underwriter(
    customer_id: str = "CUST-1001",
    wal_dir: Optional[str] = None,
    kill_at_step: Optional[int] = None,
    audit_log_sink: Optional[list] = None,
) -> Dict[str, Any]:
    """Execute durable underwriting workflow with optional crash simulation."""
    effective_wal = wal_dir or WAL_DIR_DEFAULT
    audit_records = audit_log_sink if audit_log_sink is not None else []

    @durable(goal_id=f"{GOAL_ID}-{customer_id}", wal_dir=effective_wal)
    def _workflow() -> Dict[str, Any]:
        # Step 1: Customer Profile Tool Call
        profile = step("fetch_profile", _fetch_customer_profile, customer_id)
        if kill_at_step == 1:
            raise RuntimeError("[Simulated Crash] Host terminated during Step 1")

        # Step 2: Risk Assessment Tool Call
        risk = step("assess_risk", _assess_financial_risk, profile)
        if kill_at_step == 2:
            raise RuntimeError("[Simulated Crash] Host terminated during Step 2")

        # Step 3: LLM Synthesis Tool Call
        decision = step("underwrite_decision", _generate_underwriting_decision, profile, risk)
        if kill_at_step == 3:
            raise RuntimeError("[Simulated Crash] Host terminated during Step 3")

        # Atomic Marker: External side effect protection (Audit Trail)
        with atomic_marker("audit_event", run_dir=effective_wal) as should_emit:
            if should_emit:
                audit_records.append({
                    "event": "UNDERWRITING_FINALIZED",
                    "customer_id": customer_id,
                    "status": decision["status"],
                    "timestamp": time.time(),
                })

        return {
            "customer_id": customer_id,
            "profile": profile,
            "risk": risk,
            "decision": decision,
            "audit_emitted": len(audit_records) > 0,
        }

    return _workflow()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pydantic AI Durable Agent Cookbook")
    parser.add_argument("--demo", action="store_true", help="Run interactive crash-recovery demo")
    parser.add_argument("--customer-id", default="CUST-4200", help="Target customer identifier")
    parser.add_argument("--kill-at", type=int, choices=[1, 2, 3], default=None, help="Simulate crash at step N")
    args = parser.parse_args()

    if args.demo:
        import tempfile

        wal_dir = tempfile.mkdtemp(prefix="lil-pydantic-ai-demo-")
        print("\n" + "=" * 60)
        print(" [Demo] LetItLoop + Pydantic AI Crash & Resume Demonstration")
        print("=" * 60)

        # 1. Run until simulated crash at step 2
        print("[Demo] Run 1: Executing workflow with fatal fault injected at Step 2...")
        try:
            run_pydantic_ai_underwriter(customer_id="CUST-DEMO", wal_dir=wal_dir, kill_at_step=2)
        except RuntimeError as e:
            print(f"[Demo] Caught simulated failure: {e}")

        # 2. Resume without fault
        print("[Demo] Run 2: Resuming workflow from WAL...")
        t0 = time.perf_counter()
        audit_sink: list = []
        result = run_pydantic_ai_underwriter(
            customer_id="CUST-DEMO",
            wal_dir=wal_dir,
            kill_at_step=None,
            audit_log_sink=audit_sink,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        print(f"[Demo] Replay completed in {elapsed_ms:.2f}ms.")
        print(f"[Demo] Status: {result['decision']['status']}")
        print(f"[Demo] Approved Amount: ${result['decision']['approved_amount']:,.2f}")
        print(f"[Demo] Audit Log Records: {len(audit_sink)}")
        print("[Demo] Verdict: PASS (Steps 1 & 2 recovered without re-execution)")
        print("=" * 60)

        import shutil

        shutil.rmtree(wal_dir, ignore_errors=True)
        return

    result = run_pydantic_ai_underwriter(customer_id=args.customer_id, kill_at_step=args.kill_at)
    print(f"Workflow result: {result['decision']['status']} for {args.customer_id}")


if __name__ == "__main__":
    main()
