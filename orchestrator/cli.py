"""CLI for Phase-1 durable macro-task control loop.

Commands:
    create      Validate a contract JSON and create initial state
    preflight   Run preflight checks on a drafted/blocked task
    work        Invoke worker on a ready/retry_pending task
    verify      Run verification checks on a worked task
    resume      Show current state and next legal action
    handoff     Generate session handoff from current state
    status      Show task state summary
"""

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from .approval import format_approval_reasons, requires_approval
from .contract import load_contract
from .generator import generate_contracts
from .goal import Goal, Plan
from .handoff import build_handoff
from .plan_preview import render_plan_preview, write_plan_preview
from .preferences import apply_preferences_to_goal, collect_preferences
from .preflight import run_preflight
from .state import (
    IllegalTransitionError,
    create_initial_state,
    load_state,
    save_state,
)
from .supervisor import Supervisor
from .verifier import run_verification
from .worker import run_worker

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_RUN_DIR = os.environ.get("LIL_RUN_DIR", os.path.join(WORKSPACE_ROOT, "scratch", "orchestrator_runs"))
IMPOSSIBILITY_LOG = os.path.join(WORKSPACE_ROOT, "scratch", "impossibility_theorems.log")


def _run_dir(task_id):
    return os.path.join(DEFAULT_RUN_DIR, task_id)


def _plan_digest(goal_dict: dict, plan_dict: dict) -> str:
    """SHA-256 over canonical goal+plan JSON to bind approvals to content."""
    canonical = json.dumps(
        {"goal": goal_dict, "plan": plan_dict},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _state_path(task_id):
    return os.path.join(_run_dir(task_id), "state.json")


def _get_state(task_id):
    sp = _state_path(task_id)
    if not os.path.isfile(sp):
        print(f"error: no state found for task {task_id} at {sp}", file=sys.stderr)
        sys.exit(1)
    return load_state(sp)


def _save(state):
    save_state(state, _state_path(state.task_id))


def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_create(args):
    contract_path = os.path.join(WORKSPACE_ROOT, args.contract)
    contract, errors = load_contract(contract_path, workspace_root=WORKSPACE_ROOT)
    if errors:
        print("Contract validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    state = create_initial_state(contract.task_id, journal_dir=_run_dir(contract.task_id))
    state.patch_data({"contract_path": contract_path})

    contract_out = os.path.join(_run_dir(contract.task_id), "contract.json")
    os.makedirs(os.path.dirname(contract_out), exist_ok=True)
    with open(contract_out, "w", encoding="utf-8") as f:
        json.dump(contract.to_dict(), f, indent=2, ensure_ascii=False)

    _save(state)
    print(f"Task {contract.task_id} created (state: {state.status})")
    print(f"  State: {_state_path(contract.task_id)}")
    print(f"  Contract: {contract_out}")


def cmd_preflight(args):
    state = _get_state(args.task_id)
    contract, _ = load_contract(state.data.get("contract_path", ""), workspace_root=WORKSPACE_ROOT)
    if contract is None:
        print("error: contract not found in state", file=sys.stderr)
        sys.exit(1)

    run_dir = _run_dir(args.task_id)
    try:
        state.transition("PREFLIGHT_RUNNING", reason="preflight started")
        _save(state)
    except IllegalTransitionError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[orchestrator] preflight: running checks for {args.task_id}", file=sys.stderr)
    passed, results, evidence_path = run_preflight(contract, WORKSPACE_ROOT, run_dir)
    print(f"[orchestrator] preflight: complete for {args.task_id}", file=sys.stderr)

    if evidence_path:
        state.add_evidence("preflight", evidence_path)

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['check_id']}: {r['message']}")

    if passed:
        state.transition("READY", reason="all preflight checks passed", evidence_path=evidence_path)
    else:
        state.transition("PREFLIGHT_FAILED", reason="one or more preflight checks failed", evidence_path=evidence_path)
        state.transition("BLOCKED", reason="preflight failures block execution", evidence_path=evidence_path)

    _save(state)
    print(f"Task {args.task_id} now: {state.status}")


def cmd_work(args):
    state = _get_state(args.task_id)
    contract, _ = load_contract(state.data.get("contract_path", ""), workspace_root=WORKSPACE_ROOT)
    if contract is None:
        print("error: contract not found", file=sys.stderr)
        sys.exit(1)

    run_dir = _run_dir(args.task_id)
    try:
        state.transition("WORKING", reason="worker invocation started")
        _save(state)
    except IllegalTransitionError:
        print(f"error: cannot work from state {state.status}", file=sys.stderr)
        legal = sorted(state.legal_transitions())
        print(f"  legal next states: {legal}", file=sys.stderr)
        if "RETRY_PENDING" in legal or "READY" in legal:
            print("  suggest: ensure task is in READY or RETRY_PENDING state before running work", file=sys.stderr)
        sys.exit(1)

    max_attempts = contract.worker.get("max_attempts", 3)
    if state.attempt > max_attempts:
        state.transition("ESCALATED", reason=f"attempt {state.attempt} exceeds max {max_attempts}")
        _save(state)
        _write_impossibility(contract, state)
        print(f"Task {args.task_id} ESCALATED: all {max_attempts} attempts exhausted")
        return

    previous_failures = None
    changed_approach = None
    if state.worker_results:
        previous_failures = [
            {"message": r.get("stderr", "") or f"exit code {r.get('exit_code', '?')}"}
            for r in state.worker_results[-3:]
        ]
        if state.changed_approaches:
            changed_approach = state.changed_approaches[-1]

    print(f"[orchestrator] work: starting worker for {args.task_id}", file=sys.stderr)
    result = run_worker(
        contract, WORKSPACE_ROOT, run_dir, previous_failures=previous_failures, changed_approach=changed_approach
    )
    print("[orchestrator] work: worker returned", file=sys.stderr)

    state.add_worker_result(result)
    state.add_evidence(f"worker_attempt_{state.attempt}", os.path.join(run_dir, "worker_output.log"))

    kind = "initial" if state.attempt == 1 else "retry"
    state.transition("VERIFYING", reason=f"worker {kind} attempt {state.attempt} complete (exit={result['exit_code']})")
    _save(state)

    print(f"Worker attempt {state.attempt} finished (exit={result['exit_code']}, {result['elapsed_sec']:.1f}s)")
    print(f"Task {args.task_id} now: {state.status}")


def cmd_verify(args):
    state = _get_state(args.task_id)
    contract, _ = load_contract(state.data.get("contract_path", ""), workspace_root=WORKSPACE_ROOT)
    if contract is None:
        print("error: contract not found", file=sys.stderr)
        sys.exit(1)

    run_dir = _run_dir(args.task_id)
    try:
        state.transition("VERIFYING", reason="verification started")
        _save(state)
    except IllegalTransitionError:
        if state.status != "VERIFYING":
            print(f"error: cannot verify from state {state.status}", file=sys.stderr)
            legal = sorted(state.legal_transitions())
            print(f"  legal next states: {legal}", file=sys.stderr)
            print("  suggest: verify requires VERIFYING state", file=sys.stderr)
            sys.exit(1)

    print(f"[orchestrator] verify: running checks for {args.task_id}", file=sys.stderr)
    all_passed, results, evidence_path = run_verification(contract, WORKSPACE_ROOT, run_dir)
    print(f"[orchestrator] verify: checks complete for {args.task_id}", file=sys.stderr)

    if evidence_path:
        state.add_evidence("verification", evidence_path)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.check_id}: {r.message}")

    if all_passed:
        qc_required = contract.qc.get("required", False)
        state.transition("VERIFIED", reason="all checks passed")
        if not qc_required:
            state.transition("COMPLETE", reason="all checks passed, no QC required")
    else:
        state.transition("VERIFICATION_FAILED", reason="one or more checks failed", evidence_path=evidence_path)

    _save(state)
    print(
        f"Task {args.task_id} now: {state.status} ({state.attempt}/{contract.worker.get('max_attempts', 3)} attempts)"
    )


def cmd_retry(args):
    state = _get_state(args.task_id)
    contract, _ = load_contract(state.data.get("contract_path", ""), workspace_root=WORKSPACE_ROOT)
    if contract is None:
        print("error: contract not found", file=sys.stderr)
        sys.exit(1)

    max_attempts = contract.worker.get("max_attempts", 3)
    if state.status not in ("RETRY_PENDING", "VERIFICATION_FAILED", "QC_REJECTED", "QC_CONDITIONAL_PASS"):
        print(f"error: cannot retry from state {state.status}", file=sys.stderr)
        print(
            "  legal retry source states: VERIFICATION_FAILED, QC_REJECTED, QC_CONDITIONAL_PASS, RETRY_PENDING",
            file=sys.stderr,
        )
        print(f"  current state transitions: {sorted(state.legal_transitions())}", file=sys.stderr)
        sys.exit(1)

    state.increment_attempt()
    if args.approach:
        state.record_approach(args.approach)

    if state.attempt > max_attempts:
        if state.status == "VERIFICATION_FAILED":
            state.transition("RETRY_PENDING", reason=f"retrying as attempt {state.attempt}")
        state.transition("ESCALATED", reason=f"max attempts ({max_attempts}) reached, no further retries")
        _save(state)
        _write_impossibility(contract, state)
        print(f"Task {args.task_id} ESCALATED: all {max_attempts} attempts exhausted")
        return

    if state.status == "VERIFICATION_FAILED":
        state.transition("RETRY_PENDING", reason=f"verification failed, retrying as attempt {state.attempt}")
    elif state.status == "QC_REJECTED":
        state.transition("RETRY_PENDING", reason=f"QC rejected, retrying as attempt {state.attempt}")
    elif state.status == "QC_CONDITIONAL_PASS":
        state.transition("RETRY_PENDING", reason=f"QC conditional pass, retrying as attempt {state.attempt}")

    _save(state)
    print(f"Task {args.task_id} retrying as attempt {state.attempt} (state: {state.status})")


def cmd_qc_pass(args):
    state = _get_state(args.task_id)
    if state.status == "VERIFIED":
        state.transition("QC_RUNNING", reason="QC review started")
    elif state.status in ("QC_RUNNING", "QC_REJECTED", "QC_CONDITIONAL_PASS"):
        pass
    else:
        print(f"error: cannot start QC from state {state.status}", file=sys.stderr)
        print("  expected states: VERIFIED, QC_RUNNING, QC_REJECTED, QC_CONDITIONAL_PASS", file=sys.stderr)
        sys.exit(1)

    if args.passed:
        state.transition("QC_PASSED", reason=args.reason or "QC review passed")
        state.transition("COMPLETE", reason="all checks passed, QC passed")
    else:
        state.transition("QC_REJECTED", reason=args.reason or "QC review failed")

    _save(state)
    print(f"Task {args.task_id} now: {state.status}")


def cmd_resume(args):
    state = _get_state(args.task_id)
    if state.is_terminal():
        print(f"Task {args.task_id} is terminal ({state.status}); no resume possible")
        return

    print(f"Task: {state.task_id}")
    print(f"Status: {state.status}")
    print(f"Attempt: {state.attempt}")
    print(f"Events: {len(state.events)} total")
    print(f"Worker runs: {len(state.worker_results)}")
    print(f"Next legal actions: {sorted(state.legal_transitions())}")
    print(f"Suggest: {_suggest_next_action(state)}")


def cmd_handoff(args):
    state = _get_state(args.task_id)
    run_dir = _run_dir(args.task_id)
    handoff = build_handoff(state, run_dir)
    _print_json(handoff)


def cmd_status(args):
    state = _get_state(args.task_id)
    _print_json(state.to_dict())


def cmd_doctor(args):
    if not args.task_id:
        from .env_doctor import run_env_doctor

        sys.exit(run_env_doctor(check_connectivity=getattr(args, "probe", False)))

    sp = _state_path(args.task_id)
    if not os.path.isfile(sp):
        print(f"error: no state found for task {args.task_id} at {sp}", file=sys.stderr)
        sys.exit(1)
    state = load_state(sp)

    contract, _ = load_contract(state.data.get("contract_path", ""), workspace_root=WORKSPACE_ROOT)

    print(f"Task:           {state.task_id}")
    print(f"Status:         {state.status}")
    print(f"Terminal:       {state.is_terminal()}")
    print(f"Attempt:        {state.attempt}")
    max_att = contract.worker.get("max_attempts", 3) if contract else "?"
    print(f"Max attempts:   {max_att}")
    print(f"Events:         {len(state.events)}")
    print(f"Worker runs:    {len(state.worker_results)}")
    if state.worker_results:
        last = state.worker_results[-1]
        print(f"Last worker exit: {last.get('exit_code', '?')}")
        print(f"Last worker elapsed: {last.get('elapsed_sec', '?')}s")

    run_dir = _run_dir(args.task_id)
    evidence_files = {
        "preflight_evidence.json": os.path.join(run_dir, "preflight_evidence.json"),
        "worker_output.log": os.path.join(run_dir, "worker_output.log"),
        "verification_evidence.json": os.path.join(run_dir, "verification_evidence.json"),
        "handoff.json": os.path.join(run_dir, "handoff.json"),
    }
    print("Evidence files:")
    for label, fpath in evidence_files.items():
        present = "present" if os.path.isfile(fpath) else "missing"
        print(f"  {label}: {present}")

    contract_present = os.path.isfile(os.path.join(run_dir, "contract.json"))
    print(f"Contract file:  {'present' if contract_present else 'missing'}")

    legal = sorted(state.legal_transitions())
    print(f"Next actions:   {legal}")
    print(f"Suggest:        {_suggest_next_action(state)}")

    imp_exists = os.path.isfile(IMPOSSIBILITY_LOG)
    has_imp = False
    if imp_exists:
        with open(IMPOSSIBILITY_LOG, encoding="utf-8") as f:
            has_imp = args.task_id in f.read()
    print(f"Impossibility log entries for this task: {has_imp}")

    blocked = state.status == "BLOCKED"
    print(f"Blocked:        {blocked}")
    print(f"In progress:    {state.status in ('WORKING', 'VERIFYING')}")
    print(f"Run dir:        {run_dir}")

    sys.exit(0)


def _suggest_next_action(state):
    transitions = state.legal_transitions()
    if "PREFLIGHT_RUNNING" in transitions:
        return "orchestrator preflight <task_id>"
    if "WORKING" in transitions:
        return "orchestrator work <task_id>"
    if "VERIFYING" in transitions:
        return "orchestrator verify <task_id>"
    if "QC_RUNNING" in transitions:
        return "orchestrator qc_pass <task_id> --passed/--rejected"
    return "orchestrator resume <task_id>"


def _write_impossibility(contract, state):
    entry = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "task_id": contract.task_id,
        "title": contract.title,
        "objective": contract.objective,
        "max_attempts": contract.worker.get("max_attempts", 3),
        "attempts_made": state.attempt,
        "worker_results": [
            {
                "exit_code": r.get("exit_code"),
                "elapsed_sec": r.get("elapsed_sec"),
                "stderr_preview": r.get("stderr", "")[:500],
            }
            for r in state.worker_results
        ],
        "rejected_approaches": list(state.changed_approaches),
        "events": [{"from": e["from"], "to": e["to"], "reason": e["reason"]} for e in state.events],
    }
    os.makedirs(os.path.dirname(IMPOSSIBILITY_LOG), exist_ok=True)
    with open(IMPOSSIBILITY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, indent=2, ensure_ascii=False))
        f.write("\n---\n")


def _latest_goal_id(status_filter: Optional[str] = None) -> Optional[str]:
    """Find the most recently updated goal directory in DEFAULT_RUN_DIR."""
    if not os.path.isdir(DEFAULT_RUN_DIR):
        return None
    candidates = []
    for d in os.listdir(DEFAULT_RUN_DIR):
        full = os.path.join(DEFAULT_RUN_DIR, d)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, "goal.json")):
            mtime = os.path.getmtime(full)
            candidates.append((mtime, d))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def cmd_propose(args):
    """Propose a plan for a natural-language task: goal → plan → preview → approval check."""
    prompt = args.prompt
    goal_id = args.goal_id or prompt.lower().replace(" ", "-")[:40]

    # Create goal
    description = args.description or prompt
    constraints = {}
    if args.constraints:
        try:
            constraints = json.loads(args.constraints)
        except json.JSONDecodeError as e:
            print(f"error: invalid constraints JSON: {e}", file=sys.stderr)
            sys.exit(1)

    goal = Goal(
        goal_id=goal_id,
        title=args.title or prompt,
        description=description,
        constraints=constraints,
    )

    # Collect preferences
    prefs = collect_preferences(WORKSPACE_ROOT, user_hints=constraints.get("preferences"))
    goal = Goal.from_dict(apply_preferences_to_goal(goal.to_dict(), prefs))

    # Generate plan
    try:
        plan = generate_contracts(goal, workspace_root=WORKSPACE_ROOT, prefs=prefs)
    except Exception as e:
        print(f"error: plan generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Save goal and plan after plan generation so on-disk state matches the
    # digest computed below (generate_contracts mutates goal._preferences)
    run_dir = _run_dir(goal_id)
    os.makedirs(run_dir, exist_ok=True)
    goal_dict = goal.to_dict()
    goal_path = os.path.join(run_dir, "goal.json")
    with open(goal_path, "w", encoding="utf-8") as f:
        json.dump(goal_dict, f, indent=2, ensure_ascii=False)

    plan_dict = plan.to_dict()
    plan_path = os.path.join(run_dir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan_dict, f, indent=2, ensure_ascii=False)

    # Approval check
    approval = requires_approval(plan, prefs=prefs)

    # Write approval marker
    approval_path = os.path.join(run_dir, "approval.json")
    approval_data = {
        "status": "pending" if approval["requires_approval"] else "approved",
        "requires_approval": approval["requires_approval"],
        "reasons": approval["reasons"],
        "plan_digest": _plan_digest(goal_dict, plan_dict),
    }
    with open(approval_path, "w", encoding="utf-8") as f:
        json.dump(approval_data, f, indent=2, ensure_ascii=False)

    # Write plan preview
    preview_path = os.path.join(run_dir, "plan_preview.md")
    write_plan_preview(plan, preview_path, goal_dict=goal.to_dict(), prefs=prefs)

    # Output summary
    print(f"Goal: {goal_id}")
    print("  Status: created")
    print(f"  Goal file: {goal_path}")
    print(f"  Plan file: {plan_path}")
    print(f"  Preview:   {preview_path}")
    print()

    auto_run = getattr(args, "run", False) or getattr(args, "yes", False)

    if approval["requires_approval"]:
        if auto_run:
            print("Auto-approving proposed plan (--run / -y specified)...")
            approval_data["status"] = "approved"
            with open(approval_path, "w", encoding="utf-8") as f:
                json.dump(approval_data, f, indent=2, ensure_ascii=False)
            print("Executing approved plan...")
            _execute_approved_plan(goal, plan, run_dir)
            return

        print("Approval required.")
        print(format_approval_reasons(approval))
        print()
        print(f"To approve: lil approve {goal_id}")
        print(f"To execute: lil run-approved {goal_id}")
    else:
        print("Approval not required — ready to execute.")
        print(f"To execute: lil run-approved {goal_id}")
        if auto_run:
            print()
            print("Auto-executing plan...")
            _execute_approved_plan(goal, plan, run_dir)


def _execute_approved_plan(goal, plan, run_dir, force: bool = False):
    """Execute an approved plan via the supervisor."""
    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    result = supervisor.execute_plan(force=force)
    print(f"Execution complete for goal {goal.goal_id}")
    _print_json(result)


def cmd_approve(args):
    """Mark a plan as approved."""
    goal_id = args.goal_id or _latest_goal_id()
    if not goal_id:
        print("error: no goal_id specified and no existing goals found in run directory.", file=sys.stderr)
        sys.exit(1)

    run_dir = _run_dir(goal_id)
    approval_path = os.path.join(run_dir, "approval.json")

    if not os.path.isfile(approval_path):
        print(f"error: no approval record found for goal {goal_id}. Run 'lil propose' first.", file=sys.stderr)
        sys.exit(1)

    with open(approval_path, "r", encoding="utf-8") as f:
        approval_data = json.load(f)

    if approval_data.get("status") == "approved":
        print(f"Goal {goal_id} is already approved.")
        print(f"To execute: lil run-approved {goal_id}")
        return

    # Verify the plan being approved matches the one that was proposed
    goal_path = os.path.join(run_dir, "goal.json")
    plan_path = os.path.join(run_dir, "plan.json")
    if os.path.isfile(goal_path) and os.path.isfile(plan_path):
        with open(goal_path, "r", encoding="utf-8") as f:
            goal_dict = json.load(f)
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_dict = json.load(f)
        current_digest = _plan_digest(goal_dict, plan_dict)
        recorded_digest = approval_data.get("plan_digest")
        if recorded_digest and current_digest != recorded_digest:
            print(
                f"error: plan for goal {goal_id} changed since propose "
                f"(digest mismatch). Re-run 'lil propose' before approving.",
                file=sys.stderr,
            )
            sys.exit(1)

    approval_data["status"] = "approved"
    with open(approval_path, "w", encoding="utf-8") as f:
        json.dump(approval_data, f, indent=2, ensure_ascii=False)

    print(f"Goal {goal_id} approved.")
    print(f"To execute: lil run-approved {goal_id}")


def cmd_run_approved(args):
    """Execute an approved plan."""
    goal_id = args.goal_id or _latest_goal_id()
    if not goal_id:
        print("error: no goal_id specified and no existing goals found in run directory.", file=sys.stderr)
        sys.exit(1)

    run_dir = _run_dir(goal_id)

    # Load goal
    goal_path = os.path.join(run_dir, "goal.json")
    if not os.path.isfile(goal_path):
        print(f"error: no goal found for {goal_id}. Run 'lil propose' first.", file=sys.stderr)
        sys.exit(1)
    with open(goal_path, "r", encoding="utf-8") as f:
        goal = Goal.from_dict(json.load(f))

    # Load plan
    plan_path = os.path.join(run_dir, "plan.json")
    if not os.path.isfile(plan_path):
        print(f"error: no plan found for {goal_id}. Run 'lil propose' first.", file=sys.stderr)
        sys.exit(1)
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = Plan.from_dict(json.load(f))

    # Check approval
    approval_path = os.path.join(run_dir, "approval.json")
    if not os.path.isfile(approval_path):
        print(
            f"error: no approval record found for goal {goal_id}; run 'lil propose' and 'lil approve' before execution",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        with open(approval_path, "r", encoding="utf-8") as f:
            approval_data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"error: approval record for goal {goal_id} is invalid: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    if approval_data.get("status") != "approved":
        print(
            f"error: goal {goal_id} has not been approved. Run 'lil approve {goal_id}' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Verify the plan has not changed since it was approved
    recorded_digest = approval_data.get("plan_digest")
    if recorded_digest:
        current_digest = _plan_digest(goal.to_dict(), plan.to_dict())
        if current_digest != recorded_digest:
            print(
                f"error: plan for goal {goal_id} changed after approval "
                f"(digest mismatch). Plan must be re-proposed and re-approved.",
                file=sys.stderr,
            )
            sys.exit(1)

    _execute_approved_plan(goal, plan, run_dir, force=getattr(args, "force", False))


def cmd_plan_preview(args):
    """Render and display a plan preview."""
    goal_id = args.goal_id
    run_dir = _run_dir(goal_id)
    plan_path = os.path.join(run_dir, "plan.json")

    if not os.path.isfile(plan_path):
        print(f"error: no plan found for {goal_id}. Run 'propose' first.", file=sys.stderr)
        sys.exit(1)

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = Plan.from_dict(json.load(f))

    goal_path = os.path.join(run_dir, "goal.json")
    goal_dict = None
    if os.path.isfile(goal_path):
        with open(goal_path, "r", encoding="utf-8") as f:
            goal_dict = json.load(f)

    preview = render_plan_preview(plan, goal_dict=goal_dict)
    print(preview)


def cmd_goal_create(args):
    goal_id = args.goal_id
    title = args.title or goal_id
    description = args.description or ""
    constraints = {}
    if args.constraints:
        try:
            constraints = json.loads(args.constraints)
        except json.JSONDecodeError as e:
            print(f"error: invalid constraints JSON: {e}", file=sys.stderr)
            sys.exit(1)

    goal = Goal(
        goal_id=goal_id,
        title=title,
        description=description,
        constraints=constraints,
    )
    run_dir = _run_dir(goal_id)
    os.makedirs(run_dir, exist_ok=True)
    out_path = args.out if args.out else os.path.join(run_dir, "goal.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(goal.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Goal {goal_id} created (status: {goal.status})")
    print(f"  Saved to: {out_path}")


def _load_goal(goal_id_or_path):
    candidates = [
        goal_id_or_path,
        os.path.join(WORKSPACE_ROOT, goal_id_or_path),
        os.path.join(_run_dir(goal_id_or_path), "goal.json"),
        os.path.join(WORKSPACE_ROOT, "orchestrator", "fixtures", f"{goal_id_or_path}.json"),
        os.path.join(WORKSPACE_ROOT, "orchestrator", "fixtures", goal_id_or_path),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return Goal.from_dict(data)
            except (OSError, json.JSONDecodeError, KeyError):
                pass

    print(f"error: goal file not found for {goal_id_or_path}", file=sys.stderr)
    sys.exit(1)


def cmd_plan(args):
    goal = _load_goal(args.goal_id)
    plan = generate_contracts(goal, workspace_root=WORKSPACE_ROOT)
    run_dir = _run_dir(goal.goal_id)
    os.makedirs(run_dir, exist_ok=True)
    plan_path = os.path.join(run_dir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Plan generated for goal {goal.goal_id} ({len(plan.contracts)} contracts)")
    print(f"  Plan: {plan_path}")


def _load_plan(goal_id):
    candidates = [
        os.path.join(_run_dir(goal_id), "plan.json"),
        os.path.join(WORKSPACE_ROOT, goal_id),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            with open(cand, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Plan.from_dict(data)

    print(f"error: plan not found for goal {goal_id}", file=sys.stderr)
    sys.exit(1)


def cmd_plan_check(args):
    """Run quality checks on a plan without executing."""
    from .plan_quality import check_plan_quality, format_warnings, plan_is_safe

    _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    warnings = check_plan_quality(plan, workspace_root=WORKSPACE_ROOT)
    print(format_warnings(warnings))
    if not plan_is_safe(warnings):
        sys.exit(1)


def cmd_supervise(args):
    goal = _load_goal(args.goal_id)
    plan = _load_plan(goal.goal_id)
    run_dir = _run_dir(goal.goal_id)
    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    res = supervisor.execute_plan()
    print(f"Supervision complete for goal {goal.goal_id} (status: {goal.status})")
    _print_json(res)


def cmd_supervise_resume(args):
    goal = _load_goal(args.goal_id)
    plan = _load_plan(goal.goal_id)
    run_dir = _run_dir(goal.goal_id)
    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    res = supervisor.resume_plan()
    print(f"Resume complete for goal {goal.goal_id} (status: {goal.status})")
    _print_json(res)


def cmd_supervise_status(args):
    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    agg = supervisor.aggregate_results()
    _print_json(agg)


def cmd_goal_result(args):
    cmd_supervise_status(args)


def cmd_failure_report(args):
    """Show failure classification and remediation for each contract in a goal."""
    from .failure import classify_failure, count_consecutive_same_class, suggest_remediation
    from .state import load_state

    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)

    reports = []
    for c in plan.contracts:
        tid = c["task_id"]
        state_file = os.path.join(run_dir, tid, "state.json")
        if not os.path.isfile(state_file):
            reports.append(
                {
                    "task_id": tid,
                    "status": "no_state",
                    "failure_class": "none",
                    "attempt": 0,
                    "max_attempts": 1,
                    "consecutive_same_class": 0,
                    "remediation": "none",
                    "reason": "not yet executed",
                }
            )
            continue

        state = load_state(state_file)
        contract_path = os.path.join(run_dir, tid, "contract.json")
        contract, _ = load_contract(contract_path, workspace_root=WORKSPACE_ROOT)
        max_attempts = contract.worker.get("max_attempts", 1) if contract else 1

        if state.status in ("COMPLETE", "complete", "VERIFIED", "QC_PASSED"):
            reports.append(
                {
                    "task_id": tid,
                    "status": state.status,
                    "failure_class": "none",
                    "attempt": state.attempt,
                    "max_attempts": max_attempts,
                    "consecutive_same_class": 0,
                    "remediation": "none",
                    "reason": "completed successfully",
                }
            )
            continue

        fclass = classify_failure(state, contract)
        strikes = count_consecutive_same_class(state, fclass) if state.worker_results else 0
        rem = suggest_remediation(fclass, state.attempt, max_attempts)

        reports.append(
            {
                "task_id": tid,
                "status": state.status,
                "failure_class": fclass,
                "attempt": state.attempt,
                "max_attempts": max_attempts,
                "consecutive_same_class": strikes,
                "remediation": rem["action"],
                "reason": rem["reason"],
            }
        )

    if args.json:
        _print_json(reports)
        return

    print(f"=== Failure Report: {args.goal_id} ===")
    print()
    for r in reports:
        print(f"  {r['task_id']}:")
        print(f"    Status: {r['status']}")
        print(f"    Failure: {r['failure_class']}")
        print(f"    Attempt: {r.get('attempt', 0)}/{r.get('max_attempts', 1)}")
        print(f"    Consecutive: {r.get('consecutive_same_class', 0)} same class")
        print(f"    Remediation: {r['remediation']}")
        print(f"    Reason: {r['reason']}")
        print()


def cmd_evidence_flow(args):
    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    agg = supervisor.aggregate_results()

    if getattr(args, "json", False):
        # Build a structured flow graph
        flow_graph = {
            "goal_id": agg["goal_id"],
            "contracts": agg.get("contracts", {}),
            "evidence_store": agg.get("evidence_store", {}),
            "nodes": {},
            "edges": [],
        }
        for c in plan.contracts:
            tid = c["task_id"]
            node_info = agg["contracts"].get(tid, {})
            flow_graph["nodes"][tid] = {
                "status": node_info.get("status", "UNKNOWN"),
                "outputs": node_info.get("outputs", []),
            }
            # Edges from dependency
            for dep in c.get("depends_on", []):
                edge = {
                    "from": dep,
                    "to": tid,
                    "type": "dependency",
                }
                evidence = agg.get("evidence_store", {}).get(dep, [])
                if evidence:
                    edge["evidence_paths"] = evidence
                flow_graph["edges"].append(edge)
        # Also add evidence-ledger edges (where evidence was actually injected)
        for tid, info in agg.get("contracts", {}).items():
            for ei_key, ei_path in info.get("evidence", {}).items():
                edge = {
                    "from": tid,
                    "to": tid,
                    "type": "evidence",
                    "evidence_key": ei_key,
                    "evidence_path": ei_path,
                }
                flow_graph["edges"].append(edge)
        _print_json(flow_graph)
        return

    print(f"=== Evidence Flow: {agg['goal_id']} ===")

    # Build evidence flow map
    # For each contract, find what evidence it injected into downstream contracts
    evidence_map = {}  # source -> list of (downstream_task_id, path)
    for c in plan.contracts:
        tid = c["task_id"]
        deps = c.get("depends_on", [])
        for dep in deps:
            # For each dependency, the upstream outputs feed into this contract
            if tid not in evidence_map:
                evidence_map[tid] = {"receives_from": [], "injects_into": []}
            evidence_map[tid]["receives_from"].append(dep)

    # Also build reverse map (who injects into whom)
    for c in plan.contracts:
        tid = c["task_id"]
        # Look at the contract's inputs for evidence entries
        contract_dict = c.get("contract", {})
        inputs = contract_dict.get("inputs", [])
        evidence_inputs = [i for i in inputs if i.get("evidence")]
        for ei in evidence_inputs:
            source = ei.get("source", "?")
            if source not in evidence_map:
                evidence_map[source] = {"receives_from": [], "injects_into": []}
            evidence_map[source].setdefault("injects_into", []).append(tid)

    # Print the evidence flow
    print()
    for c in plan.contracts:
        tid = c["task_id"]
        info = agg["contracts"].get(tid, {})
        status = info.get("status", "UNKNOWN")
        outputs = info.get("outputs", [])
        print(f"Contract: {tid}")
        print(f"Status: {status}")
        print(f"Outputs: {', '.join(outputs) if outputs else '(none)'}")

        # Show evidence injected INTO this contract
        e_info = evidence_map.get(tid, {})
        receives = e_info.get("receives_from", [])
        if receives:
            print(f"Receives evidence from: {receives}")
            for src in receives:
                src_outputs = agg["contracts"].get(src, {}).get("outputs", [])
                for out_path in src_outputs:
                    print(f"  \u2192 {out_path}")
        print()

    # Print the evidence store summary
    evidence_store = agg.get("evidence_store", {})
    if evidence_store:
        print()
        print("Evidence Store (completed outputs):")
        for tid, paths in evidence_store.items():
            for p in paths:
                print(f"  {tid}: {p}")
    print()


def cmd_reconcile(args):
    """Run reconciliation check on a goal's plan."""
    from . import reconcile as rec

    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    report = rec.run_reconciliation(goal.goal_id, plan, WORKSPACE_ROOT, run_dir)
    if args.json:
        _print_json(report.to_dict())
        return
    print(rec.format_report(report))
    if not report.passed:
        sys.exit(1)


def cmd_scope_check(args):
    """Run scope check on a completed goal to detect scope violations."""
    from . import scope as sc
    from .state import load_state

    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)

    all_clean = True
    for c in plan.contracts:
        tid = c["task_id"]
        task_dir = os.path.join(run_dir, tid)
        state_file = os.path.join(task_dir, "state.json")
        if not os.path.isfile(state_file):
            continue
        state = load_state(state_file)
        contract_file = os.path.join(task_dir, "contract.json")
        contract, _ = load_contract(contract_file, workspace_root=WORKSPACE_ROOT)
        if contract is None:
            continue
        scope_result = sc.check_scope(contract, WORKSPACE_ROOT, task_dir)
        if not args.json:
            print(f"{tid}: {sc.format_scope_result(scope_result)}")
            print()
        if not scope_result.passed:
            all_clean = False
            # Store violations in state data for failure_class
            state.patch_data({"scope_violations": [v.to_dict() for v in scope_result.violations]})

    if args.json:
        result = {"goal_id": goal.goal_id, "passed": all_clean}
        _print_json(result)

    if not all_clean:
        sys.exit(1)


def cmd_provenance(args):
    """Show provenance graph for a goal — contract lineage, I/O chains."""
    from . import provenance as prov

    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    graph = prov.build_provenance(goal.goal_id, goal.title, plan, WORKSPACE_ROOT, run_dir)
    if args.json:
        _print_json(graph.to_dict())
        return
    print(prov.format_provenance(graph))


def cmd_pause(args):
    """Pause all active tasks in a goal."""
    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    sup = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    res = sup.pause_plan(reason=args.reason or "operator pause")
    print(f"Pause result for goal {goal.goal_id}:")
    for tid, status in res.items():
        print(f"  {tid}: {status}")
    print(f"Goal status: {goal.status}")


def cmd_cancel(args):
    """Cancel all non-terminal tasks in a goal."""
    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    sup = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    res = sup.cancel_plan(reason=args.reason or "operator cancel")
    print(f"Cancel result for goal {goal.goal_id}:")
    for tid, status in res.items():
        print(f"  {tid}: {status}")
    print(f"Goal status: {goal.status}")


def cmd_inspect(args):
    """Deep inspect a single task's state and evidence."""
    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    sup = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    info = sup.inspect_task(args.task_id)
    if "error" in info:
        print(f"Error: {info['error']}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        _print_json(info)
        return
    print(f"=== Inspect: {info['task_id']} ===")
    print(f"Status:          {info['status']}")
    print(f"Attempt:         {info['attempt']}")
    print(f"Terminal:        {info['is_terminal']}")
    print(f"Can resume:      {info['can_resume']}")
    print(f"Next actions:    {info['legal_transitions']}")
    print(f"Events:          {info['events_count']}")
    print(f"Worker runs:     {info['worker_runs']}")
    if info["last_worker_result"]:
        wr = info["last_worker_result"]
        print(f"Last exit code:  {wr.get('exit_code', '?')}")
        print(f"Last elapsed:    {wr.get('elapsed_sec', '?')}s")
    if info.get("failure_class"):
        print(f"Failure class:   {info['failure_class']}")
    if info.get("crash_reason"):
        print(f"Crash reason:    {info['crash_reason']}")
    if info.get("scope_violations"):
        print(f"Scope violations: {len(info['scope_violations'])}")
    print(f"Contract:        {info.get('contract_title', '?')}")
    print(f"Objective:       {info.get('contract_objective', '?')}")
    print(f"Task dir:        {info['task_dir']}")
    print("Evidence files:")
    for key, ef in info.get("evidence_files", {}).items():
        print(f"  [{ef['exists'] and 'OK' or 'MISSING'}] {key}: {ef['path']}")


def cmd_dryrun(args):
    """Simulate plan execution without real worker calls."""
    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    sup = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir, dry_run=True)
    res = sup.execute_plan()
    print(f"Dry-run complete for goal {goal.goal_id} — {len(res)} tasks simulated")


def cmd_config(args):
    """Show current orchestrator configuration."""
    from .config import OrchestratorConfig

    cfg = OrchestratorConfig.load(args.path) if args.path else OrchestratorConfig.load()
    print(cfg.display())


def cmd_audit(args):
    """Show audit log for a goal."""
    from . import audit as audit_mod

    goal = _load_goal(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    entries = audit_mod.query_audit(run_dir, goal_id=args.goal_id, action_type=args.action, task_id=args.task_id)
    if args.json:
        _print_json(entries)
        return
    print(audit_mod.format_audit_entries(entries))


def cmd_metrics(args):
    """Show runtime metrics for a goal."""
    from . import metrics as metrics_mod

    goal = _load_goal(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    metrics_path = os.path.join(run_dir, "metrics.json")
    if not os.path.isfile(metrics_path):
        print(f"No metrics found for goal {args.goal_id}")
        return
    mc = metrics_mod.MetricsCollector.load(metrics_path)
    if args.json:
        _print_json(mc.to_dict())
        return
    print(mc.summary())


def cmd_safety_check(args):
    """Run pre-execution safety checks on a plan."""
    from .limits import DEFAULT_LIMITS
    from .safety import format_safety_report, run_safety_checks

    _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    report = run_safety_checks(plan, WORKSPACE_ROOT, limits=DEFAULT_LIMITS)
    if args.json:
        _print_json(report.to_dict())
        return
    print(format_safety_report(report))
    if not report.passed:
        sys.exit(1)


def cmd_checkpoint_list(args):
    """List checkpoints for a goal."""
    from . import checkpoint as cp_mod

    goal = _load_goal(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    entries = cp_mod.list_checkpoints(run_dir)
    if args.json:
        _print_json(entries)
        return
    if not entries:
        print(f"No checkpoints found for goal {args.goal_id}")
        return
    print(f"Checkpoints for goal {args.goal_id}:")
    for e in entries:
        print(
            f"  iter={e['iteration']} status={e['goal_status']} contracts={e['total_contracts']} time={e['timestamp']}"
        )


def cmd_checkpoint_recover(args):
    """Recover plan state from latest checkpoint."""
    from . import checkpoint as cp_mod

    goal = _load_goal(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    recovery = cp_mod.recover_from_checkpoint(run_dir)
    if not recovery.get("recovered"):
        print(f"No checkpoint to recover for goal {args.goal_id}")
        sys.exit(1)
    if args.json:
        _print_json(recovery)
        return
    print(f"Recovery available for goal {args.goal_id}")
    print(f"  Iteration: {recovery['iteration']}")
    print(f"  Goal status: {recovery['goal_status']}")
    print(f"  Contracts: {len(recovery['plan_contracts'])}")
    print(f"  Results: {len(recovery['results'])} tasks")
    if args.apply:
        applied = cp_mod.apply_checkpoint(run_dir, WORKSPACE_ROOT)
        print(f"Successfully applied checkpoint (iteration {applied['iteration']}) to goal {args.goal_id}.")
        print(f"Restored {len(applied['plan_contracts'])} contracts and {len(applied['graph_statuses'])} task states.")
        print(f"You can now resume execution via: lil resume {args.goal_id}")


def cmd_checkpoint_clear(args):
    """Remove all checkpoints for a goal."""
    from . import checkpoint as cp_mod

    goal = _load_goal(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    count = cp_mod.clear_checkpoints(run_dir)
    print(f"Cleared {count} checkpoints for goal {args.goal_id}")


def cmd_feedback(args):
    """Show failure feedback history for a goal."""
    from . import feedback as fb_mod

    goal = _load_goal(args.goal_id)
    _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    records = fb_mod.load_feedback(goal.goal_id, run_dir)
    if args.json:
        _print_json([r.to_dict() for r in records])
        return
    print(fb_mod.format_feedback(records))


def cmd_error_inspect(args):
    """Inspect structured errors for a goal's contracts."""
    from . import errors as err_mod

    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    errors = err_mod.inspect_goal(goal.goal_id, plan, WORKSPACE_ROOT, run_dir)
    if args.json:
        _print_json([e.to_dict() for e in errors])
        return
    print(err_mod.format_error_list(errors))
    if errors:
        sys.exit(1)


def cmd_evidence_ledger(args):
    goal = _load_goal(args.goal_id)
    _load_plan(args.goal_id)
    run_dir = _run_dir(goal.goal_id)
    from . import evidence as ev

    ledger = ev.load_ledger(run_dir)
    if not ledger:
        print(f"No evidence ledger found for goal {args.goal_id}")
        return

    issues = ev.check_evidence_freshness(run_dir)

    print(f"=== Evidence Ledger: {args.goal_id} ===")
    print(f"Total entries: {sum(len(v) for v in ledger.values())}")
    print()

    for task_id in sorted(ledger.keys()):
        entries = ledger[task_id]
        print(f"  {task_id} ({len(entries)} outputs):")
        for e in entries:
            status = "OK"
            if not os.path.isfile(e.get("absolute_path", "")):
                status = "MISSING"
            elif e.get("sha256", "") and e["sha256"] != ev._sha256(e["absolute_path"]):
                status = "MODIFIED"
            print(f"    [{status}] {e.get('relative_path', '?')} ({e.get('size_bytes', 0)} bytes)")
        print()

    if issues:
        print("  Issues:")
        for iss in issues:
            print(f"    [{iss['issue']}] {iss['path']} (task: {iss['task_id']})")
        print()


def cmd_impossibility(args):
    """Show impossibility artifact for an escalated task."""
    from . import impossibility as imp

    goal = _load_goal(args.goal_id)
    plan = _load_plan(args.goal_id)
    _run_dir(goal.goal_id)

    artifacts_found = []
    for c in plan.contracts:
        tid = c["task_id"]
        art_dir = os.path.join(WORKSPACE_ROOT, imp.artifact_dir(args.goal_id, tid))
        json_path = os.path.join(art_dir, "impossibility.json")
        os.path.join(art_dir, "impossibility.md")
        if os.path.isfile(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                artifact = json.load(f)
            artifacts_found.append(artifact)

    if not artifacts_found:
        print(f"No impossibility artifacts found for goal {args.goal_id}")
        return

    if args.json:
        _print_json(artifacts_found)
        return

    for art in artifacts_found:
        print(f"=== Impossibility: {art['task_id']} ===")
        print(f"  Title: {art['title']}")
        print(f"  Failure Class: {art['failure_class']}")
        print(f"  Attempts: {art['attempts_made']}/{art['max_attempts']}")
        print(f"  Objective: {art['objective'][:80]}...")
        print(f"  Artifacts: scratch/impossibility/{args.goal_id}/{art['task_id']}/")
        print()


def cmd_trace(args):
    """Render comprehensive step-by-step reasoning, verification and QC trace for a goal."""
    goal_id = getattr(args, "goal_id", None) or _latest_goal_id()
    if not goal_id:
        print("error: no goal_id specified and no existing goals found in run directory.", file=sys.stderr)
        sys.exit(1)

    run_dir = _run_dir(goal_id)
    goal_path = os.path.join(run_dir, "goal.json")
    if not os.path.isfile(goal_path):
        print(f"error: goal {goal_id} not found at {run_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(goal_path, "r", encoding="utf-8") as f:
            goal_data = json.load(f)
    except Exception:
        goal_data = {}

    print("\n" + "=" * 78)
    print(f"  LETITLOOP TRACE TIMELINE: {goal_id}")
    print(f"  Title:  {goal_data.get('title', 'Untitled')}")
    print(f"  Status: {goal_data.get('status', 'UNKNOWN')}")
    print("=" * 78)

    subdirs = sorted([d for d in os.listdir(run_dir) if os.path.isdir(os.path.join(run_dir, d)) and d not in ("state_backups", "checkpoints")])
    if not subdirs:
        print("  (No step executions recorded for this goal)\n")
        return

    for idx, sdir in enumerate(subdirs, 1):
        step_dir = os.path.join(run_dir, sdir)
        state_file = os.path.join(step_dir, "state.json")
        qc_file = os.path.join(step_dir, "qc_verdict.json")
        v_file = os.path.join(step_dir, "verification_evidence.json")
        c_file = os.path.join(step_dir, "contract.json")

        st_name = "UNKNOWN"
        att = 1
        if os.path.isfile(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as sf:
                    s_data = json.load(sf)
                st_name = s_data.get("status", "UNKNOWN")
                att = s_data.get("attempt", 1)
            except Exception:
                pass

        print(f"\n  [{idx:02d}] Step: {sdir}")
        print(f"       * Final Status: {st_name} (Attempts: {att})")

        # Contract info
        if os.path.isfile(c_file):
            try:
                with open(c_file, "r", encoding="utf-8") as cf:
                    c_data = json.load(cf)
                worker_m = c_data.get("worker", {}).get("model", "default")
                outs = [o.get("path", "") for o in c_data.get("outputs", []) if isinstance(o, dict)]
                print(f"       * Worker Model: {worker_m}")
                if outs:
                    print(f"       * Target Outputs: {', '.join(outs)}")
            except Exception:
                pass

        # Verification Evidence
        if os.path.isfile(v_file):
            try:
                with open(v_file, "r", encoding="utf-8") as vf:
                    v_data = json.load(vf)
                v_res = v_data.get("verification_results", [])
                all_p = v_data.get("all_passed", False)
                print(f"       * Verification Checks: {'[ALL PASS]' if all_p else '[FAIL]'} ({len(v_res)} checks)")
                for chk in v_res[:3]:
                    c_kind = chk.get("kind", chk.get("check_id", "check"))
                    c_pass = "[PASS]" if chk.get("passed") else "[FAIL]"
                    print(f"           - {c_pass} {c_kind}")
            except Exception:
                pass

        # QC Verdict
        if os.path.isfile(qc_file):
            try:
                with open(qc_file, "r", encoding="utf-8") as qf:
                    q_data = json.load(qf)
                q_stat = q_data.get("status", "UNKNOWN")
                q_score = q_data.get("score", 0.0)
                q_reason = q_data.get("reason", "")
                print(f"       * Multi-Lens QC: [{q_stat}] (Score: {q_score:.2f})")
                if q_reason:
                    print(f"           - {q_reason[:75]}...")
            except Exception:
                pass

    print("\n" + "=" * 78 + "\n")


def cmd_dashboard(args):
    """Render rich terminal UI dashboard for active run directory."""
    from .tui import print_dashboard

    run_dir = getattr(args, "run_dir", DEFAULT_RUN_DIR)
    print_dashboard(run_dir)


def cmd_install_skill(args):
    """Install letitloop skill across supported AI agent environments."""
    from pathlib import Path

    from .skill_installer import run_skill_install

    ws = Path(args.workspace).resolve() if args.workspace else None
    installed = run_skill_install(target=args.target, workspace=ws)
    print("\n========================================================")
    print("letitloop Multi-Platform Skill Installation Summary")
    print("========================================================")
    for name, path in installed:
        print(f"[OK] {name:<22} -> {path}")
    print("========================================================\n")


def main():
    global DEFAULT_RUN_DIR
    parser = argparse.ArgumentParser(
        description="Phase-1 Durable Macro-Task Orchestrator",
    )
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR, help=f"run state directory (default: {DEFAULT_RUN_DIR})")

    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Validate contract and create task state")
    p_create.add_argument("contract", help="path to contract JSON (relative to workspace root)")

    p_preflight = sub.add_parser("preflight", help="Run preflight checks")
    p_preflight.add_argument("task_id", help="task ID")

    p_work = sub.add_parser("work", help="Invoke worker")
    p_work.add_argument("task_id", help="task ID")

    p_verify = sub.add_parser("verify", help="Run verification checks")
    p_verify.add_argument("task_id", help="task ID")

    p_retry = sub.add_parser("retry", help="Retry a failed task (increment attempt, record changed approach)")
    p_retry.add_argument("task_id", help="task ID")
    p_retry.add_argument("--approach", required=True, help="description of changed approach")

    p_qc = sub.add_parser("qc", help="Record QC decision")
    p_qc.add_argument("task_id", help="task ID")
    p_qc.add_argument("--passed", action="store_true", help="QC passed")
    p_qc.add_argument("--reason", default="", help="reason for QC decision")

    p_resume = sub.add_parser("resume", help="Show resume info for a task")
    p_resume.add_argument("task_id", help="task ID")

    p_handoff = sub.add_parser("handoff", help="Generate session handoff")
    p_handoff.add_argument("task_id", help="task ID")

    p_status = sub.add_parser("status", help="Show full task state")
    p_status.add_argument("task_id", help="task ID")

    p_doctor = sub.add_parser(
        "doctor", help="Diagnose task health and next actions, or system environment if no task_id given"
    )
    p_doctor.add_argument("task_id", nargs="?", default=None, help="task ID")
    p_doctor.add_argument(
        "--probe", action="store_true", help="Perform lightweight network reachability probe on configured endpoints"
    )

    p_propose = sub.add_parser("propose", help="Propose a plan: intake -> plan -> preview -> approval check")
    p_propose.add_argument("--goal-id", help="Goal ID (auto-generated from prompt if not given)")
    p_propose.add_argument("--title", default="", help="Goal title (defaults to prompt)")
    p_propose.add_argument("--description", default="", help="Goal description (defaults to prompt)")
    p_propose.add_argument("--constraints", default="{}", help="Goal constraints JSON string")
    p_propose.add_argument("--run", "-y", "--yes", action="store_true", help="Auto-approve and execute immediately")
    p_propose.add_argument("prompt", nargs="?", default="", help="Natural language task prompt")

    p_approve = sub.add_parser("approve", help="Approve a proposed plan (defaults to latest goal if omitted)")
    p_approve.add_argument("goal_id", nargs="?", default=None, help="Goal ID")

    p_run_approved = sub.add_parser("run-approved", help="Execute an approved plan (defaults to latest goal if omitted)")
    p_run_approved.add_argument("goal_id", nargs="?", default=None, help="Goal ID")
    p_run_approved.add_argument("--force", action="store_true", help="Force acquire lock if stale")

    p_plan_preview = sub.add_parser("plan-preview", help="Show plan preview for a goal")
    p_plan_preview.add_argument("goal_id", help="Goal ID")

    p_goal_create = sub.add_parser("goal-create", help="Create a Goal JSON")
    p_goal_create.add_argument("--goal-id", required=True, help="Goal ID")
    p_goal_create.add_argument("--title", default="", help="Goal title")
    p_goal_create.add_argument("--description", default="", help="Goal description")
    p_goal_create.add_argument("--constraints", default="{}", help="Goal constraints JSON string")
    p_goal_create.add_argument("--out", help="Output file path for goal JSON")

    p_plan = sub.add_parser("plan", help="Generate plan for a Goal")
    p_plan.add_argument("goal_id", help="Goal ID or path")

    p_plan_check = sub.add_parser("plan-check", help="Run quality checks on a plan")
    p_plan_check.add_argument("goal_id", help="Goal ID or path")

    p_supervise = sub.add_parser("supervise", help="Supervise and execute contracts for a Goal")
    p_supervise.add_argument("goal_id", help="Goal ID")

    p_sup_resume = sub.add_parser("supervise-resume", help="Resume supervision for a partially-executed Goal")
    p_sup_resume.add_argument("goal_id", help="Goal ID")

    p_sup_status = sub.add_parser("supervise-status", help="Show goal supervise status")
    p_sup_status.add_argument("goal_id", help="Goal ID")

    p_goal_result = sub.add_parser("goal-result", help="Aggregate results for a Goal")
    p_goal_result.add_argument("goal_id", help="Goal ID")

    p_evidence = sub.add_parser("evidence-flow", help="Show evidence propagation for a supervised goal")
    p_evidence.add_argument("goal_id", help="Goal ID")
    p_evidence.add_argument("--json", action="store_true", help="Output as JSON")

    p_evidence_ledger = sub.add_parser("evidence-ledger", help="Show evidence ledger for a supervised goal")
    p_evidence_ledger.add_argument("goal_id", help="Goal ID")

    p_scope = sub.add_parser("scope-check", help="Check for filesystem scope violations")
    p_scope.add_argument("goal_id", help="Goal ID")
    p_scope.add_argument("--json", action="store_true", help="Output as JSON")

    p_reconcile = sub.add_parser("reconcile", help="Run reconciliation checks on a goal")
    p_reconcile.add_argument("goal_id", help="Goal ID")
    p_reconcile.add_argument("--json", action="store_true", help="Output as JSON")

    p_prov = sub.add_parser("provenance", help="Show provenance graph for a goal")
    p_prov.add_argument("goal_id", help="Goal ID")
    p_prov.add_argument("--json", action="store_true", help="Output as JSON")

    p_err = sub.add_parser("error-inspect", help="Show structured errors for a goal")
    p_err.add_argument("goal_id", help="Goal ID")
    p_err.add_argument("--json", action="store_true", help="Output as JSON")

    p_imp = sub.add_parser("impossibility", help="Show impossibility artifacts for an escalated goal")
    p_imp.add_argument("goal_id", help="Goal ID")
    p_imp.add_argument("--json", action="store_true", help="Output as JSON")

    p_fail = sub.add_parser("failure-report", help="Show failure classification and remediation for a goal")
    p_fail.add_argument("goal_id", help="Goal ID")
    p_fail.add_argument("--json", action="store_true", help="Output as JSON")

    p_fb = sub.add_parser("feedback", help="Show failure feedback history for a goal")
    p_fb.add_argument("goal_id", help="Goal ID")
    p_fb.add_argument("--json", action="store_true", help="Output as JSON")

    p_config = sub.add_parser("config", help="Show orchestrator configuration")
    p_config.add_argument("--path", default="", help="Path to config JSON file")

    p_audit = sub.add_parser("audit", help="Show audit log for a goal")
    p_audit.add_argument("goal_id", help="Goal ID")
    p_audit.add_argument("--action", default="", help="Filter by action type")
    p_audit.add_argument("--task-id", default="", help="Filter by task ID")
    p_audit.add_argument("--json", action="store_true", help="Output as JSON")

    p_metrics = sub.add_parser("metrics", help="Show runtime metrics for a goal")
    p_metrics.add_argument("goal_id", help="Goal ID")
    p_metrics.add_argument("--json", action="store_true", help="Output as JSON")

    p_safety = sub.add_parser("safety-check", help="Run pre-execution safety checks on a plan")
    p_safety.add_argument("goal_id", help="Goal ID")
    p_safety.add_argument("--json", action="store_true", help="Output as JSON")

    p_cp_list = sub.add_parser("checkpoint-list", help="List checkpoints for a goal")
    p_cp_list.add_argument("goal_id", help="Goal ID")
    p_cp_list.add_argument("--json", action="store_true", help="Output as JSON")

    p_cp_recover = sub.add_parser("checkpoint-recover", help="Recover plan state from latest checkpoint")
    p_cp_recover.add_argument("goal_id", help="Goal ID")
    p_cp_recover.add_argument("--json", action="store_true", help="Output as JSON")
    p_cp_recover.add_argument("--apply", action="store_true", help="Apply recovery (not yet implemented)")

    p_cp_clear = sub.add_parser("checkpoint-clear", help="Remove all checkpoints for a goal")
    p_cp_clear.add_argument("goal_id", help="Goal ID")

    p_pause = sub.add_parser("pause", help="Pause all active tasks in a goal")
    p_pause.add_argument("goal_id", help="Goal ID")
    p_pause.add_argument("--reason", default="", help="Reason for pausing")

    p_cancel = sub.add_parser("cancel", help="Cancel all non-terminal tasks in a goal")
    p_cancel.add_argument("goal_id", help="Goal ID")
    p_cancel.add_argument("--reason", default="", help="Reason for cancelling")

    p_inspect = sub.add_parser("inspect", help="Deep inspect a single task")
    p_inspect.add_argument("goal_id", help="Goal ID")
    p_inspect.add_argument("task_id", help="Task ID")
    p_inspect.add_argument("--json", action="store_true", help="Output as JSON")

    p_dryrun = sub.add_parser("dry-run", help="Simulate plan execution without real worker calls")
    p_dryrun.add_argument("goal_id", help="Goal ID")

    sub.add_parser("dashboard", help="Render rich terminal UI dashboard")

    p_trace = sub.add_parser("trace", help="Render step-by-step reasoning and verification trace for a goal")
    p_trace.add_argument("goal_id", nargs="?", default=None, help="Goal ID (defaults to latest)")

    p_skill = sub.add_parser("install-skill", help="Install letitloop skill across AI coding assistant environments")
    p_skill.add_argument(
        "--target",
        choices=["all", "claude", "antigravity", "hermes", "opencode", "cursor", "cline", "windsurf", "codex"],
        default="all",
        help="Target AI assistant environment (default: all)",
    )
    p_skill.add_argument(
        "--workspace",
        type=str,
        default=".",
        help="Target workspace path for project-local skill installation (default: current directory)",
    )

    cmds = {
        "create": cmd_create,
        "preflight": cmd_preflight,
        "work": cmd_work,
        "verify": cmd_verify,
        "retry": cmd_retry,
        "qc": cmd_qc_pass,
        "resume": cmd_resume,
        "handoff": cmd_handoff,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "dashboard": cmd_dashboard,
        "trace": cmd_trace,
        "install-skill": cmd_install_skill,
        "install_skill": cmd_install_skill,
        "goal-create": cmd_goal_create,
        "propose": cmd_propose,
        "approve": cmd_approve,
        "run-approved": cmd_run_approved,
        "run_approved": cmd_run_approved,
        "plan": cmd_plan,
        "plan-preview": cmd_plan_preview,
        "plan_preview": cmd_plan_preview,
        "plan-check": cmd_plan_check,
        "plan_check": cmd_plan_check,
        "supervise": cmd_supervise,
        "supervise-resume": cmd_supervise_resume,
        "supervise_resume": cmd_supervise_resume,
        "supervise-status": cmd_supervise_status,
        "supervise_status": cmd_supervise_status,
        "goal-result": cmd_goal_result,
        "goal_result": cmd_goal_result,
        "evidence-flow": cmd_evidence_flow,
        "evidence_flow": cmd_evidence_flow,
        "evidence-ledger": cmd_evidence_ledger,
        "evidence_ledger": cmd_evidence_ledger,
        "scope-check": cmd_scope_check,
        "scope_check": cmd_scope_check,
        "reconcile": cmd_reconcile,
        "provenance": cmd_provenance,
        "error-inspect": cmd_error_inspect,
        "error_inspect": cmd_error_inspect,
        "impossibility": cmd_impossibility,
        "failure-report": cmd_failure_report,
        "failure_report": cmd_failure_report,
        "feedback": cmd_feedback,
        "config": cmd_config,
        "audit": cmd_audit,
        "metrics": cmd_metrics,
        "safety-check": cmd_safety_check,
        "safety_check": cmd_safety_check,
        "checkpoint-list": cmd_checkpoint_list,
        "checkpoint_list": cmd_checkpoint_list,
        "checkpoint-recover": cmd_checkpoint_recover,
        "checkpoint_recover": cmd_checkpoint_recover,
        "checkpoint-clear": cmd_checkpoint_clear,
        "checkpoint_clear": cmd_checkpoint_clear,
        "dry-run": cmd_dryrun,
        "dry_run": cmd_dryrun,
        "pause": cmd_pause,
        "cancel": cmd_cancel,
        "inspect": cmd_inspect,
    }

    a = parser.parse_args()
    DEFAULT_RUN_DIR = a.run_dir  # noqa: PLW0603
    cmds[a.command](a)


if __name__ == "__main__":
    main()
