# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
