# Show HN: We benchmarked AI agent crash resilience – most lose 100% of tokens on SIGKILL

We built **LetItLoop** because every AI coding agent we tested loses all progress when the process dies. LangGraph, CrewAI, AutoGen — kill the process, lose the tokens.

LetItLoop is a 3-line Python decorator that makes any function crash-proof via a Write-Ahead Log. Completed steps are skipped on resume. Zero duplicate API calls.

```python
from letitloop import durable, step

@durable(goal_id="customer_sync")
def main():
    user = step("fetch", fetch_crm_record, user_id=123)
    summary = step("summarize", call_claude, user)
    step("notify", send_slack, summary)
```

If your script crashes or gets `kill -9`'d midway:
1. Re-run `main()`
2. Completed steps are skipped instantly (<1ms fast-forward)
3. Zero duplicate LLM tokens burned

## Key Features
- **Zero Daemon, Zero Server**: Runs completely in-process using stdlib append-only WAL.
- **LILWAL02 Checksummed Frames**: CRC32 + SHA-256 Merkle hash chain guarantees 0% corruption.
- **GitHub Action Gate**: Zero-dependency PR gatekeeper for CI.
- **Universal MCP Server**: Exposes durability tools to Cursor, Claude Code, and Windsurf.

- GitHub: https://github.com/sdageltc/letitloop
- PyPI: `pip install letitloop`
- Interactive Benchmark: https://sdageltc.github.io/agent-durability-bench/
