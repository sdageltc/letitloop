# OpenCode Instructions for LetItLoop

## ⚡ Fast Test & Iteration Protocol

### Rule 1: Never Run Monolithic Test Suite on Micro-Edits
Running `pytest tests/` sequentially wastes 200+ seconds. When fixing bugs or applying patches:
- **Test Modified File Only**: `pytest tests/test_<module>.py -q` (< 0.5s)
- **Run Fast Markers**: `pytest -m fast -q` (< 2s)

### Rule 2: Use `lil heal` for Auto-Repairs
```bash
# Auto-fix ruff formatting/lint and verify target tests in 1 step:
lil heal --target orchestrator/<modified_file>.py

# Fast check:
lil heal --fast
```

### Rule 3: Full Verification Before PR
When all targeted tests pass, verify the full suite in parallel:
```bash
pytest
```
*Note: `pytest.ini` automatically configures `-n auto` (pytest-xdist) to run across all available CPU cores in ~15 seconds.*
