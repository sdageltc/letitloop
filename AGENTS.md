# Repository Guide for Coding Agents (`letitloop v0.7.2`)

## Architecture Overview
- `orchestrator/`: Core runtime kernel (`state.py`, `decorators.py`, `codecs.py`, `lock.py`, `process_guard.py`, `cli.py`, `supervisor/liveness.py`).
- `letitloop/`: Public package exports (`durable`, `durable_async`, `step`, `async_step`, `atomic_marker`, `supervise`, `LivenessSupervisor`) and optional framework adapters (`letitloop/adapters/`).
- `tests/`: Automated pytest suite covering WAL framing, torn-tail recovery, codecs, locks, async concurrency, supervisor circuit breaking, and examples.

## Verification Commands
```bash
ruff check orchestrator letitloop tests examples
ruff format --check orchestrator letitloop tests examples
pytest tests/ -q
```
