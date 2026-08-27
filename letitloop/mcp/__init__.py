"""LetItLoop MCP package entrypoint.

Exposes the MCP server for `npx @modelcontextprotocol/inspector` and `lil mcp`.

Usage:
  python -m letitloop.mcp
  python -m orchestrator.mcp_server
  lil mcp
"""

from orchestrator.mcp_server import (  # noqa: F401
    SecurityError,
    bench_compare,
    checkpoint_state,
    durable_step,
    emit_receipt,
    mcp,
    rollback_ast,
    verify_scope,
    wal_verify,
)

__all__ = [
    "mcp",
    "durable_step",
    "checkpoint_state",
    "rollback_ast",
    "verify_scope",
    "emit_receipt",
    "bench_compare",
    "wal_verify",
    "SecurityError",
]
