# Claude Code Guidelines for LetItLoop

## Fast Inner-Loop Testing
- **Targeted test**: `pytest tests/test_<target>.py -q`
- **Fast suite**: `pytest -m fast -q`
- **Auto-healer**: `lil heal --target orchestrator/<file>.py`
- **Full multi-core suite**: `pytest` (auto-parallelized via pytest-xdist)
