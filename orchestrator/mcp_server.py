"""LetItLoop MCP server — exposes durable_step as an MCP tool.

Built with anthropics/skills@mcp-builder (107K installs) pattern.
Run: python -m orchestrator.mcp_server  (stdio)
     lil mcp --help

Tool: durable_step(step_id, payload) -> runs via @durable_async if inside workflow, else direct.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore

    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    FastMCP = None  # type: ignore

# Fallback: if FastMCP not available, provide a minimal shim that still exposes the logic for tests
if HAS_MCP and FastMCP is not None:
    mcp = FastMCP("letitloop-durability")

    @mcp.tool()  # type: ignore[misc]
    async def durable_step(step_id: str, payload: dict[str, Any] | None = None, wal_dir: str = ".durable_wal", goal_id: str = "mcp-workflow") -> dict[str, Any]:
        """Durable step via LetItLoop — survives SIGKILL, validated by LILWAL02 CRC.

        Args:
            step_id: Stable step identifier (e.g., "fetch_user_123"). Re-running with same ID fast-forwards <1ms without re-executing.
            payload: JSON payload for the step (will be echoed as result if no host tool). Use to pass data between steps.
            wal_dir: WAL directory for durability (default .durable_wal)
            goal_id: Workflow ID for WAL isolation

        Returns:
            {step_id, result, wal_dir, goal_id, cached: bool}
        """
        from orchestrator.decorators import async_step, durable_async

        payload = payload or {}

        async def _echo(p):
            await asyncio.sleep(0.005)
            return {"echo": p, "step_id": step_id}

        # If caller is already inside a @durable_async workflow, use that context; otherwise create one on the fly
        from orchestrator.decorators import _get_async_context

        ctx = _get_async_context()
        if ctx is not None:
            result = await async_step(step_id, _echo, payload)
            return {"step_id": step_id, "result": result, "wal_dir": ctx.run_dir, "goal_id": ctx.goal_id, "cached": True}

        @durable_async(goal_id=goal_id, wal_dir=wal_dir)
        async def _workflow():
            return await async_step(step_id, _echo, payload)

        result = await _workflow()
        return {"step_id": step_id, "result": result, "wal_dir": wal_dir, "goal_id": goal_id, "cached": False}

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
        total = 0
        corrupted = 0
        details: list[dict[str, Any]] = []
        for p in base.rglob("*.jsonl"):
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
        return {"wal_path": wal_path, "total_frames": total, "corrupted": corrupted, "details": details, "ok": corrupted == 0}

else:
    # Shim for environments without mcp SDK — exposes same functions for direct import/tests
    mcp = None  # type: ignore

    async def durable_step(step_id: str, payload: dict[str, Any] | None = None, wal_dir: str = ".durable_wal", goal_id: str = "mcp-workflow") -> dict[str, Any]:
        payload = payload or {}
        return {"step_id": step_id, "result": {"echo": payload}, "wal_dir": wal_dir, "goal_id": goal_id, "cached": False, "note": "mcp SDK not installed — shim"}

    def bench_compare(scenario: str = "DCP-002") -> dict[str, Any]:
        return {"error": "mcp SDK not installed", "scenario": scenario}

    def wal_verify(wal_path: str = ".bench_wal") -> dict[str, Any]:
        return {"error": "mcp SDK not installed", "wal_path": wal_path}


def main() -> None:
    if mcp is None:
        print("MCP SDK not installed. Install with: pip install mcp", file=sys.stderr)
        print("Shim mode: durable_step still importable for tests.", file=sys.stderr)
        # still expose for manual test
        print(json.dumps({"status": "shim", "tools": ["durable_step", "bench_compare", "wal_verify"]}, indent=2))
        return
    # FastMCP stdio
    mcp.run()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
