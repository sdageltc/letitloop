"""Persistent evidence ledger — tracks contract outputs with metadata."""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from .lock import FileLock

LEDGER_FILENAME = "evidence_ledger.json"

# AUT-009: serializes load->mutate->save so parallel worker threads cannot
# lose ledger entries.
_LEDGER_LOCK = threading.Lock()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def load_ledger(run_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """Load evidence ledger from disk. Returns dict keyed by task_id."""
    ledger_path = os.path.join(run_dir, LEDGER_FILENAME)
    if not os.path.isfile(ledger_path):
        return {}
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_ledger(run_dir: str, ledger: Dict[str, List[Dict[str, Any]]]) -> None:
    """Save evidence ledger to disk."""
    os.makedirs(run_dir, exist_ok=True)
    ledger_path = os.path.join(run_dir, LEDGER_FILENAME)
    lock_path = ledger_path + ".lock"
    with FileLock(lock_path):
        tmp = ledger_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ledger_path)


def append_output(run_dir: str, task_id: str, output_path: str, workspace_root: str) -> Dict[str, Any]:
    """Record an output file in the ledger with metadata."""
    abs_path = os.path.join(workspace_root, output_path) if not os.path.isabs(output_path) else output_path
    entry = {
        "task_id": task_id,
        "relative_path": output_path,
        "absolute_path": abs_path,
        "sha256": _sha256(abs_path),
        "size_bytes": _file_size(abs_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # AUT-009: parallel supervisor runs call append_output from worker threads —
    # the load-mutate-save sequence must be atomic to avoid lost ledger entries.
    with _LEDGER_LOCK:
        ledger = load_ledger(run_dir)
        if task_id not in ledger:
            ledger[task_id] = []
        # Replace existing entries for this output path
        ledger[task_id] = [e for e in ledger[task_id] if e.get("relative_path") != output_path]
        ledger[task_id].append(entry)
        save_ledger(run_dir, ledger)
    return entry


def rebuild_evidence_store(ledger: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[str]]:
    """Rebuild evidence_store dict from ledger — maps task_id to list of absolute paths."""
    store = {}
    for task_id, entries in ledger.items():
        paths = []
        for e in entries:
            ap = e.get("absolute_path", "")
            if ap and os.path.isfile(ap):
                paths.append(ap)
        if paths:
            store[task_id] = paths
    return store


def check_evidence_freshness(run_dir: str) -> List[Dict[str, Any]]:
    """Check all ledger entries for staleness (missing or changed files). Returns issues list."""
    ledger = load_ledger(run_dir)
    issues = []
    for task_id, entries in ledger.items():
        for e in entries:
            ap = e.get("absolute_path", "")
            if not ap:
                issues.append({"task_id": task_id, "path": e.get("relative_path", "?"), "issue": "no absolute_path"})
                continue
            if not os.path.isfile(ap):
                issues.append({"task_id": task_id, "path": e.get("relative_path", "?"), "issue": "file_missing"})
                continue
            stored_hash = e.get("sha256", "")
            if stored_hash:
                current_hash = _sha256(ap)
                if stored_hash != current_hash:
                    issues.append({"task_id": task_id, "path": e.get("relative_path", "?"), "issue": "hash_changed"})
    return issues


def write_run_manifest(
    run_dir: str, goal_id: str, plan, results: Dict[str, Dict[str, Any]], workspace_root: str
) -> None:
    """Write run_manifest.json with goal metadata, input/output hashes, and exit codes."""
    manifest_path = os.path.join(run_dir, "run_manifest.json")
    inputs = []
    outputs = []
    exit_codes = {}

    for c_info in plan.contracts:
        task_id = c_info["task_id"]
        task_dir = os.path.join(run_dir, task_id)

        contract_dict = c_info.get("contract", {})
        contract_path = os.path.join(task_dir, "contract.json")
        if not contract_dict and os.path.isfile(contract_path):
            try:
                with open(contract_path, "r", encoding="utf-8") as f:
                    contract_dict = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        for inp in contract_dict.get("inputs", []):
            if isinstance(inp, dict):
                path = inp.get("path", "")
            else:
                path = str(inp)
            if path:
                abs_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
                inputs.append({"path": path, "sha256": _sha256(abs_path)})

        for out in contract_dict.get("outputs", []):
            if isinstance(out, dict):
                path = out.get("path", "")
            else:
                path = str(out)
            if path:
                abs_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
                qc_path = os.path.join(task_dir, "qc_verdict.json")
                qc_status = "MISSING"
                if os.path.isfile(qc_path):
                    try:
                        with open(qc_path, "r", encoding="utf-8") as f:
                            qc_data = json.load(f)
                        qc_status = qc_data.get("status", "MISSING")
                    except (json.JSONDecodeError, OSError):
                        pass
                status = results.get(task_id, {}).get("status", "UNKNOWN") if results else "UNKNOWN"
                outputs.append(
                    {
                        "task_id": task_id,
                        "path": path,
                        "sha256": _sha256(abs_path),
                        "status": status,
                        "qc_verdict": qc_status,
                    }
                )

        state_file = os.path.join(task_dir, "state.json")
        exit_code = None
        if os.path.isfile(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                worker_results = state_data.get("worker_results", [])
                if worker_results:
                    last_wr = worker_results[-1]
                    exit_code = last_wr.get("exit_code") if isinstance(last_wr, dict) else None
            except (json.JSONDecodeError, OSError):
                pass
        exit_codes[task_id] = exit_code

    manifest = {
        "goal_id": goal_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs,
        "outputs": outputs,
        "exit_codes": exit_codes,
    }
    os.makedirs(run_dir, exist_ok=True)
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)
