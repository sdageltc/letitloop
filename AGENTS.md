# LetItLoop Agent Execution & Fast Testing Guidelines

## ⚡ Fast Inner-Loop Test Protocol (Sub-Second Iteration)

When modifying code or debugging bug chains in `letitloop`, **NEVER** run the entire 1,500+ test suite (`pytest tests/`) sequentially on every micro-edit. Follow this tiered protocol:

### 1. Targeted File Testing (Sub-Second: < 0.5s)
Run only the unit test file matching your modified source file:
```bash
# When modifying orchestrator/state.py:
pytest tests/test_state.py -q

# When modifying orchestrator/auto_healer.py:
pytest tests/test_auto_healer.py -q

# When modifying orchestrator/ast_node_splicer.py:
pytest tests/test_ast_node_splicer.py -q
```

### 2. Fast Marker Suite (< 2s)
To verify all core unit and pure-logic invariants:
```bash
pytest -m fast -q
```

### 3. Autonomous Self-Healing with Smart Scoping (`lil heal`)
LetItLoop includes an automated repair loop that fixes ruff lint/formatting and runs targeted tests:
```bash
# Auto-heal targeting a specific module (auto-maps to tests/test_<module>.py):
lil heal --target orchestrator/state.py

# Fast auto-heal across fast markers:
lil heal --fast

# Output JSON telemetry for IDE / Agent integration:
lil heal --target orchestrator/state.py --json
```

### 4. Full Parallel Verification Gate (< 20s)
Before opening a PR or executing `git push`, run the full multi-core parallel suite:
```bash
# Runs across all CPU cores in parallel with telemetry suppression:
pytest
```

---

## 🔒 Code & Architecture Invariants
- **Append-Only State & WAL**: All state mutations must route through `orchestrator/state.py` with atomic write semantics.
- **Pure Gate Invariance**: `letitloop-action` in GitHub Actions is strictly a deterministic AST & HMAC-SHA256 validator (no live LLM loops in CI).
- **Generic Infrastructure**: Never commit personal account names, cloud subscription credits, or local filesystem paths into markdown docs or PR descriptions.
- **Git Author Attribution**: All commits must be authored as `sdageltc <sdageltc@users.noreply.github.com>`.
