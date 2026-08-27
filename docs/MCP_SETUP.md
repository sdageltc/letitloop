# LetItLoop MCP Setup — Universal IDE Durability Server

The LetItLoop MCP server (`orchestrator/mcp_server.py` / `letitloop/mcp`) exposes durable checkpointing to any MCP-compatible IDE (Cursor, Windsurf, Claude Code, Cline) with **zero code changes**.

## Tools Exposed

| Tool | Purpose | Idempotent on `requestId` | Jailed |
|---|---|---|---|
| `durable_step(step_id, payload, wal_dir, goal_id)` | Echo step via `@durable_async` — survives `SIGKILL` (LILWAL02 CRC) | ✅ | WAL dir jailed |
| `checkpoint_state(goal_id, payload, wal_dir)` | Writes atomic `LILWAL02` frame | ✅ | ✅ |
| `rollback_ast(file_path, backup_ref)` | Safely restores AST node without corrupting surrounding code | ✅ | ✅ |
| `verify_scope(file_path, allowed_patterns)` | Checks if modification violated `allowed_patterns` | — | ✅ |
| `emit_receipt(goal_id, wal_dir)` | Generates HMAC-sealed `proof_*.json` + `.sig` | ✅ | ✅ |
| `bench_compare(scenario)` | Runs single DCP-2.0 scenario | — | — |
| `wal_verify(wal_path)` | Verifies LILWAL02 CRC per frame | — | ✅ |

- **Reconnect resilience**: re-sending the same `requestId` fast-forwards from WAL without duplicate side effects.
- **Workspace jailing**: any path outside `CWD` (or `LETITLOOP_WORKTREES` / `LETITLOOP_WORKSPACE_ROOT`) fails closed with `SecurityError`.

## Cursor

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"],
      "env": {}
    }
  }
}
```

## Windsurf / Cline / Claude Desktop

`mcp_settings.json` or `.vscode/settings.json`:

```json
{
  "mcp": {
    "servers": {
      "letitloop": {
        "command": "python",
        "args": ["-m", "letitloop.mcp"],
        "cwd": "/path/to/your/repo"
      }
    }
  }
}
```

## CLI

```bash
# stdio (default, for inspector)
python -m orchestrator.mcp_server
python -m letitloop.mcp

# via lil
lil mcp
# → {"status":"shim","tools":["durable_step","checkpoint_state",...]} when `mcp` SDK absent

# inspector
npx @modelcontextprotocol/inspector python -m orchestrator.mcp_server
# Then call: durable_step, checkpoint_state, rollback_ast, verify_scope, emit_receipt
```

## Testing

```bash
pytest tests/test_mcp_server.py -q   # 4 tests, ~1s (fast)
```

## Security

All file arguments are resolved and checked with `Path.resolve().relative_to(WORKSPACE_ROOT)` — symlink swaps and `..` traversal are rejected with `SecurityError` (fail-closed). Declare extra worktrees via `LETITLOOP_WORKTREES=/tmp/wt1:/tmp/wt2`.
