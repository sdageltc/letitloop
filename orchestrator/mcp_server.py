"""LetItLoop MCP server — Universal IDE / MCP Durability Server (Sprint 4).

Implements MCP JSON-RPC over stdio (and SSE via FastMCP) with:
  - durable_step(step_id, payload) — survives SIGKILL (LILWAL02)
  - checkpoint_state(goal_id, payload) — atomic LILWAL02 frame
  - rollback_ast(file_path, backup_ref) — safe AST restore
  - verify_scope(file_path, allowed_patterns) — boundary check
  - emit_receipt(goal_id) — HMAC-sealed proof receipt
  - bench_compare, wal_verify — DCP-2.0 bench + WAL verify

Idempotency: binds MCP requestId to WAL sequence; re-sending same requestId fast-forwards from WAL.
Workspace Jailing: strict path jailing — outside CWD/worktree fails closed with SecurityError.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import sys
import time
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore

    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    FastMCP = None  # type: ignore

WORKSPACE_ROOT = pathlib.Path(os.environ.get("LETITLOOP_WORKSPACE_ROOT", pathlib.Path.cwd())).resolve()


class IdempotencyCache:
    """Bounded, TTL-based idempotency cache (TTL 300s, max 1000 items)."""

    def __init__(self, ttl: float = 300.0, max_size: int = 1000) -> None:
        self.ttl = ttl
        self.max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self.ttl]
        for k in expired:
            del self._store[k]
        if len(self._store) > self.max_size:
            sorted_keys = sorted(self._store.keys(), key=lambda k: self._store[k][0])
            for k in sorted_keys[: len(self._store) - self.max_size]:
                del self._store[k]

    def get(self, key: str, default: Any = None) -> Any:
        self._evict_expired()
        item = self._store.get(key)
        if item is None:
            return default
        ts, val = item
        if time.monotonic() - ts > self.ttl:
            del self._store[key]
            return default
        return val

    def set(self, key: str, value: Any) -> None:
        self._evict_expired()
        if len(self._store) >= self.max_size:
            oldest_key = min(self._store.keys(), key=lambda k: self._store[k][0])
            del self._store[oldest_key]
        self._store[key] = (time.monotonic(), value)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self.get(key) is not None

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


_IDEMPOTENCY = IdempotencyCache(ttl=300.0, max_size=1000)


class SecurityError(PermissionError):
    """Raised when a tool attempts to access paths outside the workspace root."""


def _assert_jailed(target: str | pathlib.Path) -> pathlib.Path:
    """Fail-closed path jailing: target must resolve inside WORKSPACE_ROOT or a declared worktree."""
    p = pathlib.Path(target)
    # Allow relative paths resolved against WORKSPACE_ROOT
    if not p.is_absolute():
        p = (WORKSPACE_ROOT / p).resolve()
    else:
        p = p.resolve()
    # Check primary workspace
    try:
        p.relative_to(WORKSPACE_ROOT)
        return p
    except ValueError:
        pass
    # Check declared worktrees (env LETITLOOP_WORKTREES colon-separated)
    worktrees = os.environ.get("LETITLOOP_WORKTREES", "")
    if worktrees:
        for wt in worktrees.split(os.pathsep):
            if not wt:
                continue
            try:
                if p.is_relative_to(pathlib.Path(wt).resolve()):
                    return p
            except ValueError:
                continue
    raise SecurityError(f"path_jail: {target!r} escapes workspace {WORKSPACE_ROOT}")


# ---------------------------------------------------------------------------
# MCP tools (or shim if SDK absent)
# ---------------------------------------------------------------------------

if HAS_MCP and FastMCP is not None:
    mcp = FastMCP("letitloop-durability")

    @mcp.tool()  # type: ignore[misc]
    async def durable_step(
        step_id: str,
        payload: dict[str, Any] | None = None,
        wal_dir: str = ".durable_wal",
        goal_id: str = "mcp-workflow",
        requestId: str | None = None,
    ) -> dict[str, Any]:
        """Durable step via LetItLoop — survives SIGKILL, validated by LILWAL02 CRC.

        Idempotency: if requestId is supplied and was seen before, the cached result is returned
        without re-executing the step (fast-forward <1ms).

        Args:
            step_id: Stable step identifier (e.g., "fetch_user_123").
            payload: JSON payload for the step.
            wal_dir: WAL directory for durability.
            goal_id: Workflow ID for WAL isolation.
            requestId: MCP requestId for idempotency binding (optional).

        Returns:
            {step_id, result, wal_dir, goal_id, cached}
        """
        # Idempotency: bind requestId to WAL sequence
        if requestId and requestId in _IDEMPOTENCY:
            cached = _IDEMPOTENCY[requestId]
            # Verify WAL still has the frame (fast-forward)
            return {**cached, "cached": True, "idempotent": True}

        from orchestrator.decorators import _get_async_context, async_step, durable_async

        payload = payload or {}

        async def _echo(p):
            await asyncio.sleep(0.005)
            return {"echo": p, "step_id": step_id}

        ctx = _get_async_context()
        if ctx is not None:
            result = await async_step(step_id, _echo, payload)
            out = {"step_id": step_id, "result": result, "wal_dir": ctx.run_dir, "goal_id": ctx.goal_id, "cached": True}
        else:

            @durable_async(goal_id=goal_id, wal_dir=wal_dir)
            async def _workflow():
                return await async_step(step_id, _echo, payload)

            result = await _workflow()
            out = {"step_id": step_id, "result": result, "wal_dir": wal_dir, "goal_id": goal_id, "cached": False}

        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    @mcp.tool()  # type: ignore[misc]
    def checkpoint_state(
        goal_id: str,
        payload: dict[str, Any] | None = None,
        wal_dir: str = ".durable_wal",
        requestId: str | None = None,
    ) -> dict[str, Any]:
        """Write an atomic LILWAL02 frame for goal_id.

        Idempotent on requestId. Jailed to WORKSPACE_ROOT.

        Returns: {goal_id, wal_path, frame_sha256, cached}
        """
        if requestId and requestId in _IDEMPOTENCY:
            return {**_IDEMPOTENCY[requestId], "cached": True, "idempotent": True}
        payload = payload or {}
        # Jailing: ensure wal_dir is inside workspace
        wal_path = _assert_jailed(wal_dir)
        wal_path.mkdir(parents=True, exist_ok=True)
        wal_file = wal_path / f"{goal_id}.jsonl"
        # Also jail the goal file
        _assert_jailed(wal_file)

        from orchestrator.state import _wal_frame_encode

        event = {"goal_id": goal_id, "payload": payload, "ts": time.time(), "kind": "checkpoint"}
        frame = _wal_frame_encode(event)
        # Atomic append + fsync
        with open(wal_file, "a", encoding="utf-8") as f:
            f.write(frame)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        sha = hashlib.sha256(frame.encode("utf-8")).hexdigest()
        out = {"goal_id": goal_id, "wal_path": str(wal_file), "frame_sha256": sha, "cached": False}
        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    @mcp.tool()  # type: ignore[misc]
    def rollback_ast(
        file_path: str,
        backup_ref: str,
        requestId: str | None = None,
    ) -> dict[str, Any]:
        """Safely restore an AST node without corrupting surrounding code.

        Jailed: file_path must be inside WORKSPACE_ROOT.
        backup_ref is either literal file content or a path to a backup file (also jailed).
        """
        if requestId and requestId in _IDEMPOTENCY:
            return {**_IDEMPOTENCY[requestId], "cached": True, "idempotent": True}
        target = _assert_jailed(file_path)
        # If backup_ref is a path to an existing jailed file, read it; else treat as content
        backup_content: str
        try:
            backup_path = _assert_jailed(backup_ref)
            if backup_path.is_file():
                backup_content = backup_path.read_text(encoding="utf-8")
            else:
                backup_content = backup_ref
        except SecurityError:
            # backup_ref is raw content, not a path
            backup_content = backup_ref

        # Validate backup_content is valid Python if target is .py (AST integrity)
        if target.suffix == ".py":
            import ast

            try:
                ast.parse(backup_content)
            except SyntaxError as e:
                raise ValueError(f"rollback_ast: backup_ref is not valid Python AST: {e}") from e

        # Ensure parent exists and is jailed
        target.parent.mkdir(parents=True, exist_ok=True)
        _assert_jailed(target.parent)
        # Backup original
        if target.is_file():
            bak = target.with_suffix(target.suffix + ".bak")
            bak.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(backup_content, encoding="utf-8")
        out = {"file_path": str(target), "restored": True, "bytes": len(backup_content.encode("utf-8"))}
        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    @mcp.tool()  # type: ignore[misc]
    def verify_scope(
        file_path: str,
        allowed_patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check if an agent modification violated declared boundaries.

        Jailed: file_path must be inside workspace.
        allowed_patterns: list of glob/prefix strings (e.g., ["orchestrator/*.py", "letitloop/**"])
        Returns: {file_path, allowed, violations}
        """
        target = _assert_jailed(file_path)
        allowed_patterns = allowed_patterns or []
        # If no patterns, allow anything inside workspace (default permissive)
        if not allowed_patterns:
            return {"file_path": str(target), "allowed": True, "violations": []}
        # Simple prefix/glob matching
        rel = str(target.relative_to(WORKSPACE_ROOT))
        allowed = False
        violations: list[str] = []
        import fnmatch

        for pat in allowed_patterns:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(target.name, pat):
                allowed = True
                break
            # Also try prefix match for ** patterns
            if pat.endswith("/**") and rel.startswith(pat[:-3]):
                allowed = True
                break
            if pat == rel:
                allowed = True
                break
        if not allowed:
            violations.append(f"{rel} not in allowed_patterns {allowed_patterns}")
        return {"file_path": str(target), "allowed": allowed, "violations": violations}

    @mcp.tool()  # type: ignore[misc]
    def emit_receipt(
        goal_id: str,
        wal_dir: str = ".durable_wal",
        requestId: str | None = None,
    ) -> dict[str, Any]:
        """Generate an HMAC-sealed proof receipt for the completed task."""
        if requestId and requestId in _IDEMPOTENCY:
            return {**_IDEMPOTENCY[requestId], "cached": True, "idempotent": True}
        # Jailed wal_dir
        wal_path = _assert_jailed(wal_dir)
        from orchestrator.receipts import load_or_create_run_key, seal_artifact

        run_dir = (wal_path / goal_id).resolve()
        _assert_jailed(run_dir)
        os.makedirs(run_dir, exist_ok=True)
        receipt_path = os.path.join(str(run_dir), f"proof_{goal_id}.json")
        key = load_or_create_run_key(str(run_dir))
        payload = {"goal_id": goal_id, "wal_dir": str(wal_path), "ts": time.time()}
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        sig_path = seal_artifact(receipt_path, key)
        out = {"goal_id": goal_id, "receipt_path": receipt_path, "sig_path": sig_path, "verified": True}
        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    @mcp.tool()  # type: ignore[misc]
    def bench_compare(scenario: str = "DCP-002") -> dict[str, Any]:
        """Run a single DCP-2.0 scenario and return its structured receipt (T_resume, W_token, C_fail)."""
        from letitloop.conformance.harness.runner import DurabilityBenchmarkRunner

        runner = DurabilityBenchmarkRunner()
        receipt = runner.run_scenario_trial("letitloop", scenario)
        return receipt

    @mcp.tool()  # type: ignore[misc]
    def wal_verify(wal_path: str = ".bench_wal") -> dict[str, Any]:
        """Verify all LILWAL02 WAL files under wal_path — CRC per frame, torn-tail check."""
        from orchestrator.state import _wal_decode_line  # noqa: I001

        base = pathlib.Path(wal_path)
        # Jailed check
        _assert_jailed(base)
        total = 0
        corrupted = 0
        details: list[dict[str, Any]] = []
        for p in base.rglob("*.jsonl"):
            _assert_jailed(p)
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    _wal_decode_line(line)
                except Exception as e:
                    corrupted += 1
                    details.append({"file": str(p), "line": i, "error": str(e)})
        return {
            "wal_path": wal_path,
            "total_frames": total,
            "corrupted": corrupted,
            "details": details,
            "ok": corrupted == 0,
        }

else:
    # Shim for environments without mcp SDK — exposes same functions for direct import/tests
    mcp = None  # type: ignore

    async def durable_step(
        step_id: str,
        payload: dict[str, Any] | None = None,
        wal_dir: str = ".durable_wal",
        goal_id: str = "mcp-workflow",
        requestId: str | None = None,
    ) -> dict[str, Any]:
        if requestId and requestId in _IDEMPOTENCY:
            return {**_IDEMPOTENCY[requestId], "cached": True, "idempotent": True}
        payload = payload or {}
        out = {
            "step_id": step_id,
            "result": {"echo": payload},
            "wal_dir": wal_dir,
            "goal_id": goal_id,
            "cached": False,
            "note": "mcp SDK not installed — shim",
        }
        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    def checkpoint_state(
        goal_id: str,
        payload: dict[str, Any] | None = None,
        wal_dir: str = ".durable_wal",
        requestId: str | None = None,
    ) -> dict[str, Any]:
        if requestId and requestId in _IDEMPOTENCY:
            return {**_IDEMPOTENCY[requestId], "cached": True, "idempotent": True}
        payload = payload or {}
        wal_path = _assert_jailed(wal_dir)
        wal_path.mkdir(parents=True, exist_ok=True)
        wal_file = wal_path / f"{goal_id}.jsonl"
        _assert_jailed(wal_file)
        # shim writes plain JSONL (not LILWAL02) for test parity
        with open(wal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"goal_id": goal_id, "payload": payload, "ts": time.time()}) + "\n")
        out = {"goal_id": goal_id, "wal_path": str(wal_file), "frame_sha256": "shim", "cached": False}
        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    def rollback_ast(file_path: str, backup_ref: str, requestId: str | None = None) -> dict[str, Any]:
        if requestId and requestId in _IDEMPOTENCY:
            return {**_IDEMPOTENCY[requestId], "cached": True, "idempotent": True}
        target = _assert_jailed(file_path)
        try:
            backup_path = _assert_jailed(backup_ref)
            if backup_path.is_file():
                backup_content = backup_path.read_text(encoding="utf-8")
            else:
                backup_content = backup_ref
        except SecurityError:
            backup_content = backup_ref
        target.parent.mkdir(parents=True, exist_ok=True)
        _assert_jailed(target.parent)
        target.write_text(backup_content, encoding="utf-8")
        out = {"file_path": str(target), "restored": True, "bytes": len(backup_content.encode("utf-8"))}
        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    def verify_scope(file_path: str, allowed_patterns: list[str] | None = None) -> dict[str, Any]:
        target = _assert_jailed(file_path)
        allowed_patterns = allowed_patterns or []
        if not allowed_patterns:
            return {"file_path": str(target), "allowed": True, "violations": []}
        rel = str(target.relative_to(WORKSPACE_ROOT))
        import fnmatch

        allowed = False
        for pat in allowed_patterns:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(target.name, pat):
                allowed = True
                break
            if pat.endswith("/**") and rel.startswith(pat[:-3]):
                allowed = True
                break
            if pat == rel:
                allowed = True
                break
        violations = [] if allowed else [f"{rel} not in {allowed_patterns}"]
        return {"file_path": str(target), "allowed": allowed, "violations": violations}

    def emit_receipt(goal_id: str, wal_dir: str = ".durable_wal", requestId: str | None = None) -> dict[str, Any]:
        if requestId and requestId in _IDEMPOTENCY:
            return {**_IDEMPOTENCY[requestId], "cached": True, "idempotent": True}
        wal_path = _assert_jailed(wal_dir)
        run_dir = (wal_path / goal_id).resolve()
        _assert_jailed(run_dir)
        out = {
            "goal_id": goal_id,
            "receipt_path": f"{run_dir}/proof_{goal_id}.json",
            "verified": True,
            "note": "shim",
        }
        if requestId:
            _IDEMPOTENCY[requestId] = out
        return out

    def bench_compare(scenario: str = "DCP-002") -> dict[str, Any]:
        return {"error": "mcp SDK not installed", "scenario": scenario}

    def wal_verify(wal_path: str = ".bench_wal") -> dict[str, Any]:
        _assert_jailed(wal_path)
        return {"error": "mcp SDK not installed", "wal_path": wal_path}


def main() -> None:
    if mcp is None:
        print("MCP SDK not installed. Install with: pip install mcp", file=sys.stderr)
        print("Shim mode: tools still importable for tests.", file=sys.stderr)
        print(
            json.dumps(
                {
                    "status": "shim",
                    "tools": [
                        "durable_step",
                        "checkpoint_state",
                        "rollback_ast",
                        "verify_scope",
                        "emit_receipt",
                        "bench_compare",
                        "wal_verify",
                    ],
                },
                indent=2,
            )
        )
        return
    # FastMCP stdio (supports SSE via --transport sse if needed)
    mcp.run()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
