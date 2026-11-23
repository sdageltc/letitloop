# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.3] - 2026-11-23

### 🔧 CI Determinism, Metadata Alignment & Workflow Maintenance
- **Deterministic Async Concurrency Test (`tests/test_durable_async.py`)**: Replaced the wall-clock `< 0.35s` timing assertion in `test_async_steps_gather_runs_truly_concurrently` with a deterministic 3-task `asyncio.Event` entry barrier (`max_in_flight == 3`), eliminating false CI flakes caused by Windows NTFS `fsync()` jitter.
- **Package & CLI Description Alignment (`pyproject.toml`, `orchestrator/cli.py`)**: Aligned PyPI package summary and CLI help text to the single-process local `LILWAL02` WAL & process supervisor contract.
- **CI Dependency & Action Upgrades**: Updated `actions/setup-python` (`v7.1.0`), `actions/github-script` (`v9.0.0`), `release-drafter` (`v7`), `ossf/scorecard-action` (`v2.6.0`), `coverage` (`>=7.17.3`), and `pytest-benchmark` (`>=5.3.1`).

---

## [0.7.2] - 2026-11-23

### 🔒 Kernel Correctness Fixes, Async Parallelism & Protocol Hardening
- **True `asyncio.gather()` Concurrency (`orchestrator/decorators.py`)**: Scoped `DurableAsyncContext._lock` strictly to WAL commit and cache mutation rather than holding the lock across `await async_fn(...)`, allowing parallel `async_step()` calls in `asyncio.gather()` to execute concurrently.
- **WAL Key Deletion Replay Fix (`orchestrator/state.py`)**: Fixed `_apply_event` sentinel comparison (`v == _DELETE_SENTINEL` instead of object identity `is`), preventing deleted state keys from resurrecting after JSON deserialization on WAL replay.
- **$O(1)$ In-Memory Delta Rollback (`orchestrator/state.py`)**: Eliminated per-step `copy.deepcopy(self.data)` and `copy.deepcopy(self.events)` on `DATA_PATCH` events, preserving $O(1)$ per-step memory and CPU overhead for multi-hundred-step pipelines.
- **Nested `@durable` Context Preservation (`orchestrator/decorators.py`)**: Restores prior active thread-local context on exit instead of unconditionally resetting to `None`.
- **Live Process Lock Preservation (`orchestrator/lock.py`)**: Prevents `_lock_is_stale` and `FileLock._lock_file_is_stale` from stealing locks held by a demonstrably live local process with a matching OS process start token after 300 seconds.
- **Orphaned `.pending.<pid>` Reclamation & NTFS Retry (`orchestrator/decorators.py`)**: `atomic_marker` now sweeps orphaned `.pending.<dead_pid>` files left by `SIGKILL` crashes and applies a 6-attempt exponential backoff around `os.replace` on Windows NTFS.
- **Recursive Custom Codec Hydration (`orchestrator/codecs.py`)**: Custom codecs registered via `register_codec()` now recursively encode and hydrate nested built-in types (`datetime`, `uuid.UUID`, `pathlib.Path`, `tuple`) with full cycle detection.
- **Adapter Hardening & Lazy Spec Detection (`letitloop/adapters/*`)**:
  - Replaced eager package imports in `get_available_adapters()` with `importlib.util.find_spec()`, reducing availability detection overhead from ~40s to <1ms.
  - Updated `LetItLoopCheckpointSaver` (`langgraph.py`) to use `DurableCodecRegistry` instead of `pickle`, yield an async iterator from `alist()`, support `task_path` in `put_writes()`/`aput_writes()`, and preserve `parent_checkpoint_id=None` on root checkpoints.
  - Updated `SmolagentsWALCallback.wrap_agent()` (`smolagents.py`) to register with `smolagents 1.26+` `CallbackRegistry` objects without overwriting them with a raw list.
  - Added `LILWAL02` CRC32 frame verification to `lil inspect` (`orchestrator/cli.py`).
- **Bounded SHA-256 Markers (`SEC-PROTO-01` & `SEC-PROTO-02`)**: Replaced regex identifier sanitization with canonical SHA-256 digest filenames (`m-{sha256[:32]}`). Eliminates identifier collisions (`stripe/charge` vs `stripe_charge`), path traversal attempts (`../../`), and Windows NTFS `MAX_PATH` (>260 char) crashes.
- **Strict Directory Jailing**: Legacy marker compatibility lookups now strictly verify containment within `markers_dir` via `os.path.commonpath`, preventing path hijacking.
- **Split-Brain Elimination in `FileLock.release()` (`SEC-PROTO-03`)**: Added CAS-style lock ownership token (`process_start_token` + PID) verification before unlinking lock files. Prevents stolen or replaced locks from being blindly deleted by slow contender processes.
- **Codec Recursion & Cycle Protection**: Added in-flight visited object cycle detection and maximum recursion depth (256) guards to `DurableCodecRegistry.encode()`, failing closed with `DurableCodecError` instead of leaking raw unhandled `RecursionError`.
- **Safe Hydration Exception Handling**: Wrapped tagged payload hydration in `DurableCodecRegistry.decode()` to catch malformed payload data (invalid tuple iterables, malformed datetime/uuid strings) and fail closed with `DurableCodecError`.
- **Timestamp Sanity & Replay Preservation**: Added ISO-8601 parsing validation and clock-drift bounds on live appends, while maintaining clean backward compatibility for offline replay of historical WAL files.
- **Durability Contract Boundary Transparency**: Formally clarified in documentation that `atomic_marker` guarantees serialized execution and durable local completion records; recommended upstream `Idempotency-Key` headers for remote non-idempotent HTTP API side-effects.

---

## [0.7.1] - 2026-10-12

### 🛡️ Concurrency Mutex, O(N) WAL Compaction & Type Preservation
- **2-Tier `atomic_marker` Mutex**: Upgraded atomic step markers with process-local `threading.Lock` and cross-process `FileLock` mutual exclusion, moving `.committed` checks inside the acquired lock and publishing markers via atomic tempfile `os.replace` (`os.fsync` + atomic rename) to eliminate multi-process and multi-thread race conditions.
- **Elimination of $O(N^2)$ Write Amplification**: Replaced full-dictionary serialization in `step()` and `async_step()` with discrete subkey delta patching under `step_outputs`. Compresses WAL size by 168x (from 83.2 MB down to 378 KB for 1,000 steps) and accelerates replay recovery by 17.6x (down to 72.39 $\mu$s/step).
- **Safe Type Preservation (`DurableCodecRegistry`)**: Introduced safe, extensible codec registry for standard Python objects (`datetime`, `uuid.UUID`, `pathlib.Path`, `tuple`, dataclasses, and Pydantic models). Enforces zero dynamic import execution and fails closed with `DurableSerializationError` on unhandled types.
- **Conformance Transparency (DCP-2.0)**: Added explicit `adapter_kind` (`native_kernel` vs `simulated_archetype`) and `framework_executed` flags to DCP-2.0 conformance receipts to distinguish native micro-kernel executions from simulated baseline archetypes.

---

## [0.7.0] - 2026-10-07

### ⚡ The SQLite of Durable Execution (Micro-Kernel Architecture)
- **Radical Micro-Kernel Consolidation**: Amputated 70+ legacy coding-agent files, consolidating `letitloop` into an ultra-lean in-process persistence engine exporting `@durable`, `step`, `atomic_marker`, `supervise`, `LivenessSupervisor`, `State`, and `lock`.
- **Correctness & Corruption Resilience Patches**:
  - **Line-0 Torn Tail Recovery**: Patched LILWAL02 replay to safely truncate interrupted partial writes at line 0 without crashing.
  - **Zero Quadratic Write Amplification**: Decoupled full snapshot serialization from hot `step()` and `async_step()` loops (periodic snapshot every 50 steps or on clean close), reducing disk I/O overhead by up to 98% in long-running pipelines.
  - **Crash-Resilient 2-Phase `atomic_marker`**: Upgraded atomic step markers to a 2-phase commit lifecycle (`.pending.<pid>` $\to$ `.committed`) that cleans up uncommitted locks from dead PIDs.
  - **Fail-Closed State Verification**: Enforced fail-closed corruption handling across state snapshots and WAL journals.
- **Unified Lean CLI**: Redesigned command-line interface supporting `lil watch`, `lil inspect`, `lil demo`, `lil bench`, and `lil version`.
- **Zero-Daemon Ecosystem Integration**: Turnkey durable workflows and recipes for `browser-use`, `pydantic-ai`, and `dspy`.

---

## [0.6.0] - 2026-10-04

### 🌐 Ecosystem Cookbooks & Resilience Hardening
- **Browser Use Durable Digest Cookbook (#106)**: Added read-only release digest cookbook (`examples/cookbooks/browser_use_durable.py`) contributed by @MagMueller. Demonstrates `@durable_async` step checkpointing, explicit demo/live modes, and real process `kill -9` restart tests skipping finished web page extractions.
- **Fail-Closed Frame Integrity**: Enforced strict LILWAL02 CRC32 frame checksum verification on AutoGen adapters.
- **Telemetry & NTFS Resilience**: Added thread locking and exponential backoff retry loops to eliminate file-lock race conditions during rapid test runs on Windows NTFS.
- **Community Contributor Acknowledgements**: Integrated community contributions into release tracking and distribution.

---

## [0.5.2] - 2026-08-30

### 🔌 Multi-Framework Durability Adapter Suite
- **CrewAI (`CrewAIDurabilityHandler`)**: Task lifecycle callback integration (`on_task_start`, `on_task_end`, `on_tool_execute`, `on_task_error`) with automatic task skipping on resume.
- **Hugging Face Smolagents (`SmolagentsWALCallback`)**: Step callback integration (`CodeAgent`, `ToolCallingAgent`) capturing thoughts, tool calls, and observations into atomic WAL.
- **Microsoft AutoGen 0.4 / Magentic-One (`AutoGenStateSerializer`)**: State serialization, message history checkpointing, and tool execution persistence.
- **LangGraph (`LetItLoopCheckpointSaver`)**: Native `BaseCheckpointSaver` implementation powered by embedded SQLite in atomic WAL mode (`PRAGMA journal_mode=WAL`) with sub-millisecond superstep reads/writes.
- **Zero Mandatory Dependencies**: All 4 adapters utilize lazy optional loading and pure-Python duck-typed fallbacks when host packages are not installed.
- **Comprehensive Adapter Test Suite**: 16 dedicated unit tests in `tests/adapters/` verifying lifecycle hooks, memory restoration, and crash recovery.

---

## [0.5.1] - 2026-08-29

### 🍳 Cookbooks
- **LangGraph Financial Analyst**: Added a four-node yfinance + DeepSeek agent with durable WAL checkpoints, real post-memo SIGKILL recovery, independent external-call/token receipts, and scoped sub-millisecond fast-forward measurements.

### 🔄 Liveness & Auto-Supervision
- **Programmatic `@supervise` & `supervise()`**: Embedded parent-child process supervisor that catches `SIGKILL (137)`, OOM, and abnormal subprocess crashes, automatically relaunching until completion.
- **CLI Watcher (`lil watch`)**: Real-time terminal watcher with ASCII progress indicators, exponential backoff, and jitter (`lil watch script.py`).
- **Signal Classification Matrix**: User/system clean shutdowns (`SIGINT 130` / `SIGTERM 143`) halt immediately without resurrection; abnormal faults trigger restart policy.
- **Rapid-Failure Circuit Breaker**: Aborts execution on persistent deterministic syntax/logic bugs (<5s uptime x 3 attempts) to prevent runaway CPU spin.
- **Dual-OS Subprocess Tree Encapsulation**: Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` on Windows; `start_new_session=True` / `os.killpg()` on POSIX.

---

## [0.5.0] - 2026-08-27

### 🔒 Security & Correctness
- **Fail-Closed `step()` Enforcement**: `step()` called outside `@durable` now raises `RuntimeError` by default (opt-in bypass via `LETITLOOP_LENIENT=1`), eliminating silent data-loss vectors.
- **Action v2 Pure-JS LILWAL02 CRC32**: Implemented pure TS/Node LILWAL02 frame decoding with `zlib.crc32` verification and `python3` discovery.
- **MCP Security Hardening**: Jailed `emit_receipt` against `goal_id` path traversal escapes; added 300s TTL and 1000-entry capacity eviction to `_IDEMPOTENCY` cache.
- **WAL Schema Version Forward-Guard**: Rejects unverified v1 snapshot migrations and unsupported future schema versions.
- **WAL Deduplication**: Removed redundant `f"step_output_{step_id}"` key to reduce WAL I/O by ~40%.

### 🍳 Cookbooks & Developer Experience
- **Framework Cookbooks**: Added `examples/cookbooks/langgraph_financial_analyst.py` (yfinance + LangGraph + `@durable`) and `examples/cookbooks/dspy_durable_optimize.py` (DSPy + `@durable_async`).
- **Cookbook CI Tests**: Added `tests/test_cookbooks.py` with mock LLM runs ensuring zero bit-rot.
- **Benchmark Honesty**: Replaced adapter aliases in `agent-durability-bench` with honest baselines (`raw_python`, `json_checkpoint`, `sqlite_wal`).
- **README Rewrite**: Repositioned hero to 3-line `@durable` crash resilience; quarantined enterprise compliance behind `[compliance]` extra.

---

## [0.3.3] - 2026-08-26

### Added
- **LILWAL02 Checksummed WAL v2** (`orchestrator/state.py`): `\nLILWAL02:<len_hex>:<crc32_hex>:<payload>\n` with `zlib.crc32`, byte-accurate torn-tail `truncate(good_end)` + `fsync`, mid-file fail-closed; 8 tests `tests/test_wal_v2_checksums.py`
- **Native Async Durability** (`orchestrator/decorators.py`): `@durable_async` + `await async_step()` via `contextvars.ContextVar` + `asyncio.Lock`, fast-forward <1ms; 7 tests `tests/test_durable_async.py`
- **DCP-2.0 Conformance Moat** (`letitloop/conformance/`): harness 4 kill windows (PROMPT/EXEC/WRITE/VERIFY), real `subprocess.Popen` + `SIGKILL`/`taskkill`, adapters for `letitloop`/`langgraph`/`crewai`/`autogen`/`unmanaged`, scenarios DCP-001..004; CLI `lil bench --compare all` / `lil bench --scenario DCP-002` with JSON receipts (`T_resume`, `W_token`, `C_fail`)
- **500-Cycle Chaos Gate** (`scripts/chaos_fuzzer_v2.py`): 20 parallel sync+async workflows, random kills, 0 corrupted WALs / 0 state loss (80.18s)
- **Security Hardening**: sandbox `target_path` (`..` reject) + scenario whitelist `DCP-001..004` in `conformance/harness`
- **Extensive Plan**: `docs/plans/2026-08-26-vital-fixes-plus-real-bridges.md` (P0-P2 + A-D)

### Fixed
- **Silent failures surfaced**: WAL truncate and `DurableContext.close` no longer swallow `OSError`/`Exception`; `chaos_fuzzer` `shutil` import fixed
- **60s invariant**: `results/` + `.bench_wal/` now gitignored, scorecard moved to `docs/V033_SCORECARD.md`, `pytest --durations=10`
- **Bench Moat**: `conformance/harness/runner.py` PYTHONPATH fix (`parents[3]`), DCP-2.0 markdown export, `lil` entrypoint `pip install -e .`

### Changed
- `tests/test_hardening_batch3.py` now LILWAL02-aware; `1464 passed, 4 skipped` in `59.18s`

## [0.3.2] - 2026-08-23

### Fixed
- Clean conflict markers, verify 1449/1449 test suite pass (audit closeout)

## [0.1.0] - 2026-08-17

### 🚀 Added
- **Model Context Protocol (MCP) Server**: Full stdio JSON-RPC server with `letitloop-mcp` entry point exposing 8 tools for Google Antigravity, Claude Code, Cursor, and OpenCode.
- **Pluggable Worker Adapter Framework**: Native interfaces for Claude Code CLI (`claude`), Google Antigravity CLI (`agy`), Omniroute routing gateways, custom shell/Python scripts, and direct LLM calls.
- **Interactive Terminal Dashboard**: Zero-dependency live ASCII status matrix, DAG visualization, progress bars, and event telemetry (`lil dashboard`).
- **Turnkey Containerization**: Production multi-stage `Dockerfile` (non-root security profile) and `docker-compose.yml`.
- **Multi-Gateway Transport Layer**: Expanded `orchestrator.llm` to support Omniroute, OpenRouter, Groq, Ollama, DeepSeek, Google Gemini, Anthropic, and OpenAI.
- **Universal Benchmark Fallback**: Resilient `@pytest.fixture` fallback in `tests/test_benchmarks.py` enabling cross-platform benchmarking without mandatory external plugins.

### 🛡️ Fixed & Hardened
- **Windows Kernel Hang Resolution**: Replaced blocking POSIX `os.kill(pid, 0)` with non-blocking native Win32 `OpenProcess` checks in `orchestrator.lock` and `orchestrator.supervisor`.
- **Ruff Compliance**: Fixed 424 linting/formatting errors across 131 files with 100% clean check status.
- **POSIX Process Tree Kill**: Fixed nested quote escaping in `test_tree_kill_posix` using dedicated temporary runner scripts.
- **Zero-Leak Privacy Audit**: Verified zero PII, personal directory paths, or leaked private tokens in repository tracked files.
- **CI/CD Matrix**: 100% green test and build workflow on GitHub Actions across Python 3.11 and 3.12.
