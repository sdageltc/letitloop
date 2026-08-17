"""Standalone proof runner — runs phase-1 proof contracts programmatically.

Usage:
    python orchestrator/fixtures/run_proofs.py [--run-dir PATH]
    python orchestrator/fixtures/run_proofs.py --only HAPPY
    python orchestrator/fixtures/run_proofs.py --all --json

Each proof is run through the full control loop via direct function calls.
Exit 0 if all proofs pass, exit 1 if any fail.
"""

import argparse
import glob
import os
import sys
import shutil
import json
import time
import threading

# Ensure the package root is on sys.path
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from orchestrator.cli import (
    cmd_create, cmd_preflight, cmd_work, cmd_verify,
    cmd_retry, cmd_qc_pass,
    WORKSPACE_ROOT, DEFAULT_RUN_DIR,
)
from orchestrator.state import load_state, IllegalTransitionError
from argparse import Namespace


def _clean_run_dir(run_dir, task_id):
    path = os.path.join(run_dir, task_id)
    if os.path.isdir(path):
        shutil.rmtree(path)


def _state_path(run_dir, task_id):
    return os.path.join(run_dir, task_id, "state.json")


def _check_state(run_dir, task_id, expected_statuses):
    sp = _state_path(run_dir, task_id)
    if not os.path.isfile(sp):
        return False, f"state.json not found at {sp}"
    with open(sp, encoding="utf-8") as f:
        state = json.load(f)
    actual = state["status"]
    if actual in expected_statuses:
        return True, actual
    return False, f"expected one of {expected_statuses}, got {actual}"


def run_proof(contract_rel_path, run_dir, verbose=False, proof_name=None):
    """Run a single proof contract end-to-end. Returns (pass: bool, message: str)."""
    contract_path = os.path.join(WORKSPACE_ROOT, contract_rel_path)

    if not os.path.isfile(contract_path):
        return False, f"contract not found: {contract_path}"

    # Load contract to get task_id
    from orchestrator.contract import load_contract
    contract_obj, errors = load_contract(contract_path, workspace_root=WORKSPACE_ROOT)
    if errors or contract_obj is None:
        return False, f"contract load failed: {errors}"

    task_id = contract_obj.task_id
    max_att = contract_obj.worker.get("max_attempts", 3)
    qc_required = contract_obj.qc.get("required", False)

    if not proof_name:
        proof_name = os.path.basename(contract_rel_path).replace("_contract.json", "").replace("phase1_", "")

    _clean_run_dir(run_dir, task_id)
    total_start = time.time()

    try:
        # CREATE
        print(f'  [{proof_name}] create...', file=sys.stderr)
        cmd_create(Namespace(contract=contract_rel_path))
        print(f"  create: OK")

        # PREFLIGHT
        print(f'  [{proof_name}] preflight...', file=sys.stderr)
        cmd_preflight(Namespace(task_id=task_id))
        print(f"  preflight: OK")

        # WORK
        print(f'  [{proof_name}] work...', file=sys.stderr)
        if verbose:
            stop = threading.Event()
            t0 = time.time()
            def _heartbeat():
                while not stop.is_set():
                    stop.wait(15)
                    if not stop.is_set():
                        print(f'  [{proof_name}] work still running ({time.time()-t0:.0f}s)...', file=sys.stderr)
            ht = threading.Thread(target=_heartbeat, daemon=True)
            ht.start()
        cmd_work(Namespace(task_id=task_id))
        if verbose:
            stop.set()
            ht.join(timeout=2)
        print(f"  work (attempt 1): OK")

        # VERIFY
        print(f'  [{proof_name}] verify...', file=sys.stderr)
        cmd_verify(Namespace(task_id=task_id))
        print(f"  verify: OK")

        # Check post-verify state
        ok, msg = _check_state(run_dir, task_id,
                                ["COMPLETE", "VERIFIED", "VERIFICATION_FAILED", "RETRY_PENDING"])
        if not ok:
            return False, msg

        state_data = _get_state_dict(run_dir, task_id)
        status = state_data["status"]

        # Handle repair loop: if VERIFICATION_FAILED, retry + work + verify
        if status == "VERIFICATION_FAILED":
            print(f'  [{proof_name}] retry...', file=sys.stderr)
            if max_att <= 1:
                # Will escalate on retry
                cmd_retry(Namespace(task_id=task_id, approach="retry from escalation proof"))
                print(f"  retry: OK")
                ok, msg = _check_state(run_dir, task_id, ["ESCALATED", "RETRY_PENDING"])
                if not ok:
                    return False, msg
                state_data = _get_state_dict(run_dir, task_id)
                if state_data["status"] == "ESCALATED":
                    print(f"  escalated as expected")
                    return True, "ESCALATED as expected (max_attempts=1)"
                # Otherwise fall through to work again
            else:
                # Normal retry: record approach and work again
                cmd_retry(Namespace(task_id=task_id, approach="changed approach: produce correct output"))
                print(f"  retry: OK")
                print(f'  [{proof_name}] work...', file=sys.stderr)
                if verbose:
                    stop = threading.Event()
                    t0 = time.time()
                    def _heartbeat_retry():
                        while not stop.is_set():
                            stop.wait(15)
                            if not stop.is_set():
                                print(f'  [{proof_name}] work still running ({time.time()-t0:.0f}s)...', file=sys.stderr)
                    ht = threading.Thread(target=_heartbeat_retry, daemon=True)
                    ht.start()
                cmd_work(Namespace(task_id=task_id))
                if verbose:
                    stop.set()
                    ht.join(timeout=2)
                print(f"  work (attempt 2): OK")
                print(f'  [{proof_name}] verify...', file=sys.stderr)
                cmd_verify(Namespace(task_id=task_id))
                print(f"  verify: OK")
                ok, msg = _check_state(run_dir, task_id,
                                        ["COMPLETE", "VERIFIED", "VERIFICATION_FAILED"])
                if not ok:
                    return False, msg
                state_data = _get_state_dict(run_dir, task_id)
                if state_data["status"] == "VERIFICATION_FAILED":
                    return False, "still VERIFICATION_FAILED after retry+work+verify"

        # Check post-repair state
        state_data = _get_state_dict(run_dir, task_id)
        status = state_data["status"]

        if qc_required and status == "VERIFIED":
            print(f'  [{proof_name}] qc...', file=sys.stderr)
            cmd_qc_pass(Namespace(task_id=task_id, passed=True, reason="proof-runner QC pass"))
            print(f"  qc: OK")
            ok, msg = _check_state(run_dir, task_id, ["COMPLETE"])
            if not ok:
                return False, msg
        elif not qc_required and status == "COMPLETE":
            pass  # already complete
        elif status == "COMPLETE":
            pass  # already complete
        else:
            return False, f"unexpected final status: {status}"

        elapsed_total = time.time() - total_start
        print(f'  [{proof_name}] total elapsed: {elapsed_total:.0f}s', file=sys.stderr)
        return True, f"PASS (final: {status})"

    except Exception as e:
        elapsed_total = time.time() - total_start
        print(f'  [{proof_name}] total elapsed: {elapsed_total:.0f}s', file=sys.stderr)
        return False, f"exception: {type(e).__name__}: {e}"


def _get_state_dict(run_dir, task_id):
    sp = _state_path(run_dir, task_id)
    with open(sp, encoding="utf-8") as f:
        return json.load(f)


PROOF_NAME_MAP = {
    "HAPPY": "phase1_proof_happy_path_contract.json",
    "REPAIR": "phase1_proof_repair_contract.json",
    "ESCALATION": "phase1_proof_escalation_contract.json",
    "QC": "phase1_proof_qc_contract.json",
}


def _resolve_proof_files(only=None):
    fixture_dir = os.path.join(_PROJECT_ROOT, "orchestrator", "fixtures")
    if only:
        fname = PROOF_NAME_MAP.get(only.upper())
        if not fname:
            valid = ", ".join(PROOF_NAME_MAP)
            print(f"error: unknown proof '{only}'. Valid: {valid}", file=sys.stderr)
            sys.exit(1)
        path = os.path.join(fixture_dir, fname)
        return [path] if os.path.isfile(path) else []
    pattern = os.path.join(fixture_dir, "phase1_proof_*.json")
    return sorted(glob.glob(pattern))


def main():
    parser = argparse.ArgumentParser(description="Run phase-1 proofs")
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR,
                        help=f"run state directory (default: {DEFAULT_RUN_DIR})")
    parser.add_argument("--all", action="store_true", dest="run_all",
                        help="run all proofs (default)")
    parser.add_argument("--only", type=str, default=None,
                        help="run a single proof by name: HAPPY, REPAIR, ESCALATION, QC")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="output results as JSON")
    parser.add_argument("--continue-on-fail", action="store_true",
                        help="continue running remaining proofs after a failure")
    parser.add_argument("--verbose", action="store_true",
                        help="print heartbeat every 15s during long worker calls")
    args = parser.parse_args()

    run_dir = args.run_dir
    os.makedirs(run_dir, exist_ok=True)

    # Override the CLI's DEFAULT_RUN_DIR so cmd_* functions use our dir
    import orchestrator.cli as cli_mod
    cli_mod.DEFAULT_RUN_DIR = run_dir

    proof_files = _resolve_proof_files(only=args.only)

    if not proof_files:
        print("No proof contracts found matching phase1_proof_*.json")
        sys.exit(1)

    results = []
    for pf in proof_files:
        rel_path = os.path.relpath(pf, WORKSPACE_ROOT)
        task_name = os.path.basename(pf).replace("_contract.json", "").replace("phase1_", "")
        print(f"\n{'='*60}")
        print(f"PROOF: {task_name}")
        print(f"{'='*60}")

        passed, msg = run_proof(rel_path, run_dir, verbose=args.verbose, proof_name=task_name)
        status = "PASS" if passed else "FAIL"
        print(f"  >>> {status}: {msg}")
        results.append((task_name, status, msg))

        if not passed and not args.continue_on_fail:
            print("Stopping on first failure (use --continue-on-fail to override)")
            break

    if args.json_output:
        json_results = [
            {"name": name, "status": status, "detail": msg}
            for name, status, msg in results
        ]
        print(json.dumps(json_results, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"{'Proof':40s} {'Status':6s}  Detail")
        print("-" * 60)
        for name, status, msg in results:
            flag = "+" if status == "PASS" else "X"
            print(f"  [{flag}] {name:37s} {status:6s}  {msg}")
        print(f"\n{sum(1 for _, s, _ in results if s == 'PASS')}/{len(results)} passed")

    all_pass = all(s == "PASS" for _, s, _ in results)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
