"""Filesystem scope enforcement — snapshot/diff for contract execution boundaries."""

import json
import os
import shutil
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

from . import evidence as ev

_snapshot_cache: Dict[str, Dict[str, str]] = {}


SCOPE_SNAPSHOT_FILE = "scope_snapshot.json"


def _is_under_path(child: str, parent_dir: str) -> bool:
    """Check if child is under parent_dir using path component comparison.

    Avoids raw prefix-match false positives: /app/src_malicious/f.py vs /app/src.
    """
    parent_parts = os.path.normcase(os.path.normpath(parent_dir)).rstrip(os.sep).split(os.sep)
    child_parts = os.path.normcase(os.path.normpath(child)).split(os.sep)
    return child_parts[: len(parent_parts)] == parent_parts


class ScopeViolation:
    """A single scope violation — file in denied path or outside allowed scope."""

    def __init__(
        self,
        path: str,
        violation_type: str,
        detail: str = "",
        created_new: bool = False,
        old_hash: str = "",
        current_hash: str = "",
    ):
        self.path = path
        self.violation_type = violation_type
        self.detail = detail
        # AUT-018: machine-readable provenance for safe auto-cleanup — only
        # newly-created files may be deleted automatically; modified
        # pre-existing files must never be auto-removed.
        self.created_new = created_new
        self.old_hash = old_hash
        self.current_hash = current_hash

    def to_dict(self) -> Dict[str, str]:
        return {
            "path": self.path,
            "violation_type": self.violation_type,
            "detail": self.detail,
            "created_new": self.created_new,
            "old_hash": self.old_hash,
            "current_hash": self.current_hash,
        }

    def __repr__(self) -> str:
        return f"[{self.violation_type}] {self.path}"


class ScopeCheckResult:
    """Result of scope violation check."""

    def __init__(self, passed: bool, violations: List[ScopeViolation], snapshot_path: str = ""):
        self.passed = passed
        self.violations = violations
        self.snapshot_path = snapshot_path

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "snapshot_path": self.snapshot_path,
        }


def _walk_matching(root: str, prefixes: List[str]) -> Dict[str, str]:
    """Walk files under root matching any prefix via component-safe comparison."""
    files: Dict[str, str] = {}
    root_abs = os.path.abspath(root)
    for dirpath, _dirnames, filenames in os.walk(root_abs):
        if ".opencode" in dirpath.split(os.sep) or ".git" in dirpath.split(os.sep):
            _dirnames[:] = []
            continue
        for fn in filenames:
            abs_path = os.path.join(dirpath, fn)
            rel_path = os.path.relpath(abs_path, root_abs)
            for prefix in prefixes:
                prefix_abs = os.path.abspath(os.path.join(root_abs, prefix))
                if _is_under_path(abs_path, prefix_abs):
                    files[rel_path] = ev._sha256(abs_path)
                    break
    return files


def _walk_all(root: str, exclude_dir: str = None) -> Dict[str, str]:
    """Walk all files under root, optionally excluding a directory."""
    files: Dict[str, str] = {}
    root_abs = os.path.abspath(root)
    exclude_abs = os.path.abspath(exclude_dir) if exclude_dir else None
    for dirpath, _dirnames, filenames in os.walk(root_abs):
        # Skip orchestrator-internal state (.opencode, .git) — not worker output.
        if ".opencode" in dirpath.split(os.sep) or ".git" in dirpath.split(os.sep):
            _dirnames[:] = []
            continue
        for fn in filenames:
            abs_path = os.path.join(dirpath, fn)
            if exclude_abs and _is_under_path(abs_path, exclude_abs):
                continue
            rel_path = os.path.relpath(abs_path, root_abs)
            files[rel_path] = ev._sha256(abs_path)
    return files


def snapshot_scope(workspace_root: str, allowed_paths: List[str], run_dir: str, denied_paths: List[str] = None) -> str:
    """Take pre-execution snapshot of the entire workspace (excl. run_dir).

    Captures all files under workspace_root so post-execution check can
    distinguish worker-created files from pre-existing ones.
    Returns path to snapshot file.
    """
    snapshot = _walk_all(workspace_root, exclude_dir=run_dir)
    cache_key = f"{workspace_root}|{run_dir}"
    _snapshot_cache[cache_key] = snapshot
    os.makedirs(run_dir, exist_ok=True)
    snapshot_path = os.path.join(run_dir, SCOPE_SNAPSHOT_FILE)
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
    return snapshot_path


def load_snapshot(run_dir: str) -> Dict[str, str]:
    """Load scope snapshot from disk."""
    snapshot_path = os.path.join(run_dir, SCOPE_SNAPSHOT_FILE)
    if not os.path.isfile(snapshot_path):
        return {}
    with open(snapshot_path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_path_exempt(
    target_path: str,
    exemption_paths: List[str],
    workspace_root: Optional[str] = None,
) -> bool:
    """Return whether target_path is an exemption path or descendant.

    Uses normalized absolute paths and os.path.commonpath so a declared output
    DIRECTORY exempts nested outputs (prefix semantics, not exact match).
    Different Windows drives are never considered related.
    """

    def _absolute(path: str) -> str:
        path = path.replace("\\", os.sep).replace("/", os.sep)
        if workspace_root and not os.path.isabs(path):
            path = os.path.join(workspace_root, path)
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    target_abs = _absolute(target_path)
    for exemption_path in exemption_paths:
        if not exemption_path:
            continue
        exemption_abs = _absolute(exemption_path)
        try:
            if os.path.commonpath([target_abs, exemption_abs]) == exemption_abs:
                return True
        except ValueError:
            continue
    return False


class FileBackedScopeRegistry:
    """Multiprocess registry for declared-output scope leases (autonomy fix).

    Serialized through a short-lived lock file; persisted with temp+os.replace.
    Stale leases are TTL-pruned on read/write. Lets a parallel task's scope
    check ignore sibling tasks' declared outputs that are still being written.
    """

    LEASE_TTL_SEC = 300
    LOCK_TTL_SEC = 30

    def __init__(self, workspace_root: str, ttl_sec: int = LEASE_TTL_SEC):
        self.workspace_root = os.path.abspath(workspace_root)
        self.ttl_sec = ttl_sec
        self.lock_dir = os.path.join(self.workspace_root, ".opencode", "locks")
        self.path = os.path.join(self.lock_dir, "scope_leases.json")
        self.lock_path = f"{self.path}.lock"

    def _acquire(self) -> None:
        os.makedirs(self.lock_dir, exist_ok=True)
        deadline = time.time() + self.LOCK_TTL_SEC
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "created_at": time.time()}, f)
                return
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.lock_path) > self.LOCK_TTL_SEC:
                        os.remove(self.lock_path)
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    raise TimeoutError("timed out acquiring scope lease registry lock")
                time.sleep(0.01)

    def _release(self) -> None:
        try:
            os.remove(self.lock_path)
        except OSError:
            pass

    def _load_unlocked(self) -> List[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            payload = {}
        leases = payload.get("leases", []) if isinstance(payload, dict) else []
        if not isinstance(leases, list):
            leases = []
        now = time.time()
        return [
            lease
            for lease in leases
            if isinstance(lease, dict)
            and isinstance(lease.get("created_at"), (int, float))
            and now - lease["created_at"] <= self.ttl_sec
        ]

    def _write_unlocked(self, leases: List[Dict[str, Any]]) -> None:
        os.makedirs(self.lock_dir, exist_ok=True)
        temp_path = f"{self.path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"leases": leases}, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    def register(self, task_id: str, declared_outputs: List[str]) -> None:
        self._acquire()
        try:
            leases = [lease for lease in self._load_unlocked() if lease.get("task_id") != task_id]
            leases.append(
                {
                    "task_id": task_id,
                    "pid": os.getpid(),
                    "created_at": time.time(),
                    "declared_outputs": list(declared_outputs),
                }
            )
            self._write_unlocked(leases)
        finally:
            self._release()

    def unregister(self, task_id: str) -> None:
        try:
            self._acquire()
        except TimeoutError:
            return
        try:
            leases = [lease for lease in self._load_unlocked() if lease.get("task_id") != task_id]
            self._write_unlocked(leases)
        finally:
            self._release()

    def sibling_declared_outputs(self, task_id: Optional[str] = None) -> List[str]:
        try:
            self._acquire()
        except TimeoutError:
            return []
        try:
            leases = self._load_unlocked()
            self._write_unlocked(leases)
            outputs: List[str] = []
            for lease in leases:
                if task_id is not None and lease.get("task_id") == task_id:
                    continue
                declared = lease.get("declared_outputs", [])
                if isinstance(declared, list):
                    outputs.extend(str(p) for p in declared if p)
            return outputs
        finally:
            self._release()


def check_scope(
    contract, workspace_root: str, run_dir: str, exclude_dir: Optional[str] = None, task_id: Optional[str] = None
) -> ScopeCheckResult:
    """Check if contract execution caused scope violations.

    Compares current filesystem state against pre-execution snapshot.
    Detects:
    - New or modified files outside allowed paths
    - New or modified files in denied paths
    - Files in scratch_dir (temp_dir) are exempt from new-file violations

    exclude_dir: an additional directory tree to exclude from the walk —
    used by the supervisor to hide its own run-state (state.json, journals,
    sibling task dirs) from the scope check (parallel-mode fix 2026-07-31).

    task_id: when set, sibling tasks' active declared-output leases are exempt
    (they may be writing their own declared outputs concurrently).
    """
    cache_key = f"{workspace_root}|{run_dir}"
    before = _snapshot_cache.get(cache_key) or load_snapshot(run_dir)
    snapshot_path = os.path.join(run_dir, SCOPE_SNAPSHOT_FILE)
    if not before:
        missing_snapshot = not os.path.isfile(snapshot_path)
        if missing_snapshot:
            return ScopeCheckResult(
                passed=False,
                violations=[
                    ScopeViolation(
                        path=snapshot_path,
                        violation_type="missing_snapshot",
                        detail="scope snapshot file missing — cannot verify scope integrity",
                    )
                ],
                snapshot_path=snapshot_path,
            )
        return ScopeCheckResult(passed=True, violations=[], snapshot_path=snapshot_path)

    allowed = contract.workspace_scope.get("allow", [])
    denied = contract.workspace_scope.get("deny", [])
    scratch_dir = contract.workspace_scope.get("scratch_dir", "")

    after = _walk_all(workspace_root, exclude_dir=exclude_dir or run_dir)
    violations: List[ScopeViolation] = []

    sibling_outputs: List[str] = []
    if task_id:
        try:
            sibling_outputs = FileBackedScopeRegistry(workspace_root).sibling_declared_outputs(task_id=task_id)
        except Exception:
            sibling_outputs = []
    violations: List[ScopeViolation] = []

    allowed_abs = [os.path.abspath(os.path.join(workspace_root, a)) for a in allowed]
    denied_abs = [os.path.abspath(os.path.join(workspace_root, d)) for d in denied]
    scratch_abs = os.path.abspath(os.path.join(workspace_root, scratch_dir)) if scratch_dir else ""

    for path, current_hash in after.items():
        abs_path = os.path.abspath(os.path.join(workspace_root, path))

        # Sibling tasks' active declared-output leases are exempt — they may be
        # writing their own declared outputs concurrently.
        if sibling_outputs and is_path_exempt(abs_path, sibling_outputs, workspace_root=workspace_root):
            continue

        # Skip scratch_dir files — they are temp helper artifacts
        if scratch_abs and _is_under_path(abs_path, scratch_abs):
            old_hash = before.get(path)
            if old_hash is None:
                continue

        in_denied = any(_is_under_path(abs_path, d_abs) for d_abs in denied_abs)
        in_allowed = any(_is_under_path(abs_path, a_abs) for a_abs in allowed_abs)

        if in_denied:
            old_hash = before.get(path)
            if old_hash is None:
                violations.append(
                    ScopeViolation(
                        path=path,
                        violation_type="denied_new",
                        detail="new file created in denied path",
                        created_new=True,
                        current_hash=current_hash,
                    )
                )
            elif old_hash != current_hash:
                violations.append(
                    ScopeViolation(
                        path=path,
                        violation_type="denied_modified",
                        detail="file in denied path was modified",
                        created_new=False,
                        old_hash=old_hash,
                        current_hash=current_hash,
                    )
                )
        elif not in_allowed:
            old_hash = before.get(path)
            if old_hash is None:
                violations.append(
                    ScopeViolation(
                        path=path,
                        violation_type="outside_scope",
                        detail="new file created outside allowed scope",
                        created_new=True,
                        current_hash=current_hash,
                    )
                )
            elif old_hash != current_hash:
                violations.append(
                    ScopeViolation(
                        path=path,
                        violation_type="outside_scope_modified",
                        detail="file outside allowed scope was modified",
                        created_new=False,
                        old_hash=old_hash,
                        current_hash=current_hash,
                    )
                )

    return ScopeCheckResult(
        passed=len(violations) == 0,
        violations=violations,
        snapshot_path=snapshot_path,
    )


def cleanup_scratch_dir(workspace_root: str, contract) -> None:
    """Remove scratch_dir if defined in contract workspace_scope."""
    scratch_dir = contract.workspace_scope.get("scratch_dir", "")
    if not scratch_dir:
        return
    full = os.path.join(workspace_root, scratch_dir) if not os.path.isabs(scratch_dir) else scratch_dir
    if os.path.isdir(full):
        try:
            shutil.rmtree(full, ignore_errors=True)
            print(f"[scope] cleaned scratch_dir: {scratch_dir}", file=sys.stderr)
        except OSError as e:
            print(f"[scope] failed to clean scratch_dir {scratch_dir}: {e}", file=sys.stderr)


def format_scope_result(result: ScopeCheckResult) -> str:
    """Format scope check result as human-readable string."""
    if result.passed:
        return "Scope check PASSED — all files within allowed boundaries."
    lines = [f"Scope check FAILED — {len(result.violations)} violation(s):"]
    for v in result.violations:
        lines.append(f"  [{v.violation_type}] {v.path}")
        if v.detail:
            lines.append(f"    {v.detail}")
    return "\n".join(lines)
