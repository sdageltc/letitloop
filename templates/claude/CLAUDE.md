# LetItLoop — Claude Code Preset (MCP + Gate)

You are an AI coding agent running in Claude Code with LetItLoop durability.

## Workflow

1. **Checkpoint before edit:**
   ```bash
   python -m orchestrator.mcp_server  # stdio, or lil mcp
   # Tool: checkpoint_state(goal_id="fix-123", payload={"files": ["orchestrator/state.py"]})
   ```

2. **Edit files** — use `splice_ast_function` or `patch_applier` (never `shell=True` with `subprocess.run`; use `shlex.split` + `shell=False`).

3. **Verify scope:**
   ```bash
   # Tool: verify_scope(file_path="orchestrator/state.py", allowed_patterns=["orchestrator/*.py"])
   ```

4. **Gate before commit:**
   ```bash
   lil gate --check --json
   # or: python -m orchestrator.cli gate --check
   ```
   - Fails closed on `forbidden_files` (`.github/workflows/ci.yml`), `sk-...` secrets (scrubbed to `<secret:REDACTED>`), `BudgetExceededError`.
   - On `FAIL`, run `rollback_ast` then retry.

5. **Emit receipt on success:**
   ```bash
   # Tool: emit_receipt(goal_id="fix-123")
   # → proof_*.json + .sig (HMAC) + Ed25519 sig
   ```

## MCP Setup (`~/.config/claude/mcp.json` or `.mcp.json`):

```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"]
    }
  }
}
```

## Invariants (DO NOT VIOLATE)

- **Zero Shell Injection:** `subprocess.run(shlex.split(cmd), shell=False)` only.
- **Stable Action SHAs:** Use `@v4`/`@v5` in `.github/workflows/`, never hallucinate 40-char SHAs.
- **Cross-platform:** Use `pathlib.Path` + `os.path`, `taskkill /F /T` on Windows vs `os.kill(SIGKILL)` on Unix.
