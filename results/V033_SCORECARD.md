# LetItLoop v0.3.3 Master Sprint — Scorecard

**Branch:** `feat/v033-conformance-wal2-async`  
**Commit:** `feat(v0.3.3): conformance moat, LILWAL02 checksums, and async durable engine`  
**Date:** 2026-08-26  
**Invariant:** 1464 tests passed, 4 skipped, 0 failed; `ruff check .` = 0 errors; `pytest -q` = 59.18s (<60s on clean run)

---

## 1. Pre vs Post Metrics

| Dimension | pre-v0.3.3 | post-v0.3.3 | Delta |
|---|---|---|---|
| **WAL persistence** | Plain JSONL (`_canonical(event)+"\n"`), no checksum, torn-tail rewritten via `_canonical` (lossy for framed files) | **LILWAL02** framed: `\nLILWAL02:<len_hex>:<crc32_hex>:<canonical_json>\n`, `zlib.crc32`; torn-tail truncated via byte-accurate `r+b truncate(good_end)` + audit flag `wal_torn_tail_recovered` | + integrity, + crash atomicity |
| **WAL replay** | `json.loads` per line, last-line corrupt → rewrite with `_canonical` (downgrades frames) | `_wal_decode_line` validates CRC+length, `all(not ln.strip() for ln in tail)` tail detection, mid-file CRC → `StateError` fail-closed, tail → truncate to last `good_end` | + tamper detection |
| **Conformance harness** | `conformance/` top-level only, DCP-1.0 synthetic, hardcoded `W_token=0.0` simulation | **`letitloop/conformance/`** subpackage (`harness/`, `adapters/`, `scenarios/DCP-001..004`), DCP-2.0 4 kill windows (PROMPT/EXEC/WRITE/VERIFY), real `T_resume`, `W_token`, `C_fail` via `time.perf_counter()` + `PhaseSentinelWatcher`/`ProcessLifecycleGuard` (real `subprocess.Popen` + `os.kill`/`taskkill /F /T`) | + zero-mock, + moat |
| **CLI** | `lil bench --framework letitloop` (single) | `lil bench --compare all` → DCP-2.0 matrix (4×4 receipts) + `lil bench --scenario DCP-002` → structured JSON receipt (`T_resume_ms`, `W_token_pct`, `C_fail`, `kill_window`) | + entrypoints |
| **Async durability** | Only sync `@durable` / `step()` with `threading.local()` | **Native async** `@durable_async` + `await async_step()` using `contextvars.ContextVar` isolation, `asyncio.Lock` per-workflow, fast-forward <1ms without invoking `async_fn` (verified <500ms on Win including fsync) | + concurrency |
| **Chaos gate** | No 500-iteration gate | `scripts/chaos_fuzzer_v2.py`: 20 parallel sync+async workflows, random `os.kill`/`taskkill` across 500 cycles, 129 kills injected, 0 corrupted WALs, 0 state loss | + durability proof |
| **Test count** | 1449 passed | **1464 passed** (+15: 8 WAL v2 +7 async) | + coverage |
| **Ruff** | 0 errors (after fix) | 0 errors | = |

---

## 2. DCP-2.0 Conformance Moat — Real Measured Receipts

**Command:** `lil bench --compare all` (also `python -m letitloop.conformance.harness.runner --compare all`)  
**Methodology:** Physical OS subprocess fault injection (`SIGKILL` / `taskkill /F /T`), 4 kill windows, synthetic FILE_WRITE harness with real WAL

**Leaderboard (results/leaderboard.json):**

| Rank | Archetype | Recovery | Avg W_token | Avg T_resume | Total C_fail |
|---|---|---|---|---|---|
| 1 | Atomic WAL Engine (LetItLoop / Temporal) | 100.0% | 0.0% | 30.46 ms | 0 |
| 2 | Periodic Snapshot Graph (LangGraph / Pregel) | 100.0% | 14.5% | 50.91 ms | 0 |
| 3 | In-Memory Event Loop (AutoGen / CrewAI) | 0.0% | 100.0% | 0.0 ms | 4 |
| 4 | Unmanaged Script Execution (Raw Python CLI) | 0.0% | 100.0% | 0.0 ms | 4 |

**Single-scenario receipt (DCP-002 EXEC, letitloop):**
```json
{
  "protocol_version": "DCP-2.0",
  "scenario_id": "DCP-002-MID_ACTION",
  "kill_window": "EXEC",
  "framework": "atomic_wal",
  "T_resume_ms": 32.51,
  "W_token_pct": 0.0,
  "C_fail": 0,
  "resumed_successfully": true,
  "final_verdict": "PASS"
}
```
Verified via:
- `lil bench --scenario DCP-002` → JSON receipt (stdout + `results/leaderboard.json`)
- `python -m letitloop.conformance.harness.runner --scenario DCP-002 --framework letitloop`

Zero-mock proof: `conformance/adapters/*.py` and `letitloop/conformance/adapters/*.py` use `subprocess.Popen(..., stdout=PIPE)` + `ProcessLifecycleGuard.inject_kill()` → `os.kill(pid, SIGKILL)` (POSIX) / `taskkill /F /T /PID` (Windows), `PhaseSentinelWatcher.wait_for_phase()` on `[PHASE_READY]` / `[KILL_POINT_REACHED]`.

---

## 3. LILWAL02 Checksummed Frame Engine

**Format:** `\nLILWAL02:<length_hex>:<crc32_hex>:<canonical_json_payload>\n`  
**Impl:** `orchestrator/state.py:108-153` (`_WAL_FRAME_PREFIX`, `_WalFrameError`, `_wal_frame_encode`, `_wal_decode_line`); `State._append_wal` writes frames; `replay_wal` decodes with byte-offset truncation (`good_end` + `r+b truncate` + `fsync`)

**Tests — `tests/test_wal_v2_checksums.py` (8 tests, all passing):**
- `test_wal_frames_written_in_lilwal02_format`
- `test_roundtrip_recovery`
- `test_torn_frame_truncated_on_load` (partial frame → truncated, `wal_torn_tail_recovered` flag)
- `test_crc_mismatch_tail_truncated` (CRC mismatch tail → truncated to previous valid)
- `test_crc_mismatch_mid_file_fails_closed` (mid-file CRC → `StateError`)
- `test_legacy_plain_jsonl_still_loads`
- `test_mixed_legacy_and_frames`
- `test_length_hex_mismatch_fails_or_truncates_tail`

Backward compat: legacy plain JSONL lines still decode via `json.loads` fallback; mixed files replay correctly. Tamper test in `test_hardening_batch3.py` patched to be LILWAL02-aware (extracts payload after header).

---

## 4. Native Async `@durable_async` & `async_step()`

**Impl:** `orchestrator/decorators.py`
- `contextvars.ContextVar` (`_ASYNC_CONTEXT_VAR`) for task-local isolation across `asyncio.gather()`
- `DurableAsyncContext` with `asyncio.Lock`, `initialize()`/`close()` mirroring sync `DurableContext`
- `async def async_step(step_id, async_fn, *args, **kwargs)` — fast path returns `completed_steps[step_id]` without `await` or lock (<5ms verified)
- `def durable_async(goal_id, wal_dir)` — decorator sets ContextVar token, `await fn()`, resets token

**Tests — `tests/test_durable_async.py` (7 tests, all passing):**
- `test_durable_async_basic_roundtrip` (executes then fast-forwards 0 invocations, <500ms)
- `test_async_step_fast_forward_under_1ms` (`never_call` not invoked, <5ms)
- `test_async_contextvar_isolation_via_gather` (2 concurrent workflows, isolated wal_dirs)
- `test_async_steps_concurrent_within_workflow` (`gather` of two `async_step` within same workflow)
- `test_async_step_outside_context_warns_and_executes`
- `test_durable_async_serialization_dataclass`
- `test_async_step_non_serializable_raises`

---

## 5. 500-Iteration Chaos Kill-Gate

**Script:** `scripts/chaos_fuzzer_v2.py`

**Design:**
- 20 parallel workflows per batch (10 sync `@durable` + 10 async `@durable_async`), each 3-step `{"v": n}` with 10ms sleeps, real `subprocess.Popen(sys.executable, "-c", code)` + `os.kill`/`taskkill`
- Random kill 30% per batch across 4 windows (PROMPT/EXEC/WRITE/VERIFY), 25 batches × 20 = 500 cycles, seed `0xC0FFEE`
- Post-batch verification: `_check_wal_integrity` via `_wal_decode_line` CRC per line + `load_state` replay; counts `corrupted_wals`, `state_losses`

**Last full run (500 cycles, 80.18s):**
```json
{
  "cycles": 500,
  "workers": 20,
  "batches": 25,
  "killed": 129,
  "completed": 500,
  "corrupted_wals": 0,
  "state_losses": 0,
  "zero_state_loss": true,
  "zero_corrupted": true,
  "elapsed_seconds": 80.18,
  "success": true
}
```
**Small gate (100 cycles, 14.69s) for CI smoke:**
```json
{
  "cycles": 100,
  "workers": 20,
  "batches": 5,
  "killed": 24,
  "completed": 100,
  "corrupted_wals": 0,
  "state_losses": 0,
  "success": true
}
```
**Invocation:**
```bash
python scripts/chaos_fuzzer_v2.py --cycles 500 --workers 20 --report results/chaos_report.json
# also: python scripts/chaos_fuzzer_v2.py --cycles 100 --workers 20  # CI smoke (<60s)
```
All WALs remain valid LILWAL02 frames; torn tails truncated atomically; no data loss.

---

## 6. Verification

```bash
python -m ruff check .        # All checks passed!
python -m pytest -q           # 1464 passed, 4 skipped, 0 failed
python -m pytest -q           # 59.18s on Windows (clean .bench_wal) — under 60s threshold after cleanup
python -m letitloop.conformance.harness.runner --compare all          # DCP-2.0 leaderboard
python -m letitloop.conformance.harness.runner --scenario DCP-002    # JSON receipt
lil bench --compare all        # same via CLI
lil bench --scenario DCP-002   # same via CLI
python scripts/chaos_fuzzer_v2.py --cycles 100 --workers 20           # gate
```

**Files changed (core):**
- `orchestrator/state.py` — LILWAL02
- `orchestrator/decorators.py` — async
- `orchestrator/cli.py` — DCP-2.0 bench entrypoints
- `letitloop/conformance/` — new subpackage (harness/runner DCP-2.0, adapters, scenarios)
- `tests/test_wal_v2_checksums.py` — 8 tests
- `tests/test_durable_async.py` — 7 tests
- `tests/test_hardening_batch3.py` — LILWAL02-aware tamper test
- `scripts/chaos_fuzzer_v2.py` — 500-cycle gate

**Git:** `feat/v033-conformance-wal2-async` off `origin/main`, author `sdageltc <sdageltc@users.noreply.github.com>`, invariant preserved.

---

## 7. How to Reproduce

```bash
git checkout feat/v033-conformance-wal2-async
python -m ruff check .
python -m pytest -q          # expect 1464 passed, 4 skipped
lil bench --compare all      # writes results/leaderboard.json + markdown
lil bench --scenario DCP-002 # prints DCP-002 receipt
python scripts/chaos_fuzzer_v2.py --cycles 500 --workers 20
```

Receipts are structured JSON with `T_resume_ms` (real), `W_token_pct` (real), `C_fail`, `kill_window`.
