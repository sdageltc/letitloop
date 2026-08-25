---
name: code-review
description: Comprehensive code review agent skill for letitloop pull requests and diffs. Enforces deterministic contract contracts, typed exceptions, zero blind except Exception blocks, Bandit SAST security checks, and triple-OS test verification.
metadata:
  author: sdageltc (@sdageltc)
  version: "1.0.0"
compatibility: Cross-platform (Python 3.11+, pytest, ruff, bandit)
---

# Code Review Protocol for letitloop

Use this skill when reviewing pull requests, diffs, or architecture changes in the `letitloop` repository.

## 1. Non-Negotiable Invariants

Every code contribution to `letitloop` MUST adhere to these 5 invariants:

1. **Typed Domain Exceptions (Zero Blind Catches)**:
   - Prohibited: `except Exception:`, `except: pass`, or unlogged exception swallowing.
   - Required: Explicit typed exceptions (`StateError`, `IllegalTransitionError`, `TimeoutError`, `OSError`, `json.JSONDecodeError`, `KeyError`, `ValueError`, `TypeError`).
   - If an error is caught during recovery, it must output diagnostic logging or be explicitly raised.

2. **Decoupled Architecture & Minimal Coupling**:
   - Subsystems (Budget, MemoryBridge, Scope, Replanner, Doctor) must avoid circular imports.
   - Core modules must import defaults from `orchestrator.config`, not from `orchestrator.cli`.
   - APIs must accept optional path/config overrides to allow hermetic unit testing without host directory side-effects.

3. **Hermetic Test Isolation**:
   - Tests must NOT pollute the host filesystem. Use `@pytest.fixture(autouse=True)` with `tmp_path` and `monkeypatch.setenv("LIL_RUN_DIR", ...)` or isolated temp paths.
   - Mock all external network calls (`urllib.request.urlopen`, socket connections) with unit test fixtures.

4. **Security & SAST Invariants**:
   - Never use weak MD5 hashing for approach or state fingerprints; use SHA-256 (`hashlib.sha256`).
   - Always whitelist URL schemes (`http://` or `https://`) before invoking `urllib.request.urlopen`.
   - When subprocess execution with `shell=True` is intentionally required for user-configured script workers, annotate the call with `# nosec B602`.

5. **Cross-Platform Parity**:
   - Code must execute reliably across macOS (Darwin / Apple Silicon), Linux, and Windows.
   - Never assume `/proc/{pid}/stat` exists; use platform-aware fallbacks for PID inspection (e.g. `ps -p <pid> -o lstart=` on macOS).
   - Handle Windows file lock semantics `(FileExistsError, PermissionError)` on file creation collisions.

## 2. Review Checklist

When reviewing a PR, evaluate:
- [ ] Are all new CLI flags covered by unit tests in `tests/`?
- [ ] Does `ruff check .` and `ruff format --check .` pass with 0 warnings?
- [ ] Does `bandit -r orchestrator/ -ll -ii` pass with 0 Medium/High findings?
- [ ] Do all 1,120+ unit and integration tests pass cleanly via `python fast_test_runner.py`?
- [ ] Is backward compatibility preserved for existing state files and contract JSON schemas?
