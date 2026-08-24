# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] - 2026-08-24

### Added
- **Docker Sandbox Worker Adapter**: `docker` adapter running contracts in isolated containers (`--network none`, cpu/memory limits, scope-driven read-only/read-write mounts, daemon-unreachable fail-fast) (#7).
- **Local LLM Tool-Calling Adapter**: `local-tool` adapter driving Ollama/vLLM through a multi-turn tool loop (`read_file`/`write_file`/`replace_lines`/`execute_command`) with workspace-scope enforcement, repair nudges, and full journaling (#10).
- **MCP Client Manager**: `MCPClientManager` with stdio + SSE transports, `.letitloop/mcp.json` config, namespaced tool discovery, and a `required_mcp_servers` contract field (#11).
- **Live Interactive TUI**: `lil dashboard --live` with ANSI status colors, DAG tree rendering, budget gauge, and q/tab keybindings - still zero-dependency (#12).
- **Community Recipe Book**: `recipes/` with four schema-validated walkthroughs (legacy refactor, FastAPI CRUD, offline Ollama loop, multi-agent QC) plus CI-enforced JSON validation (#14).
- **Ephemeral Worktree Sandboxing**: per-attempt git worktrees with merge-on-pass / prune-on-fail lifecycle, `--worktree` CLI flags, and zero host-tree pollution (#15).
- **Process-Tree Orphan Guard**: Windows Job Objects (pure ctypes) + POSIX process groups, `atexit`/signal sweeps, grandchild-proof tree kills wired into bounded subprocess runs and script adapters (#16).
- **DAG Cycle & Deadlock Detection**: Kahn-based validator with ASCII cycle traces, enforced at plan creation, dispatch, and resume (#17).
- **Webhooks + SSE Event Streaming**: thread-safe event bus, HMAC-SHA256-signed webhook dispatcher, `text/event-stream` server, `lil serve` command, and all 7 lifecycle events emitted from the supervisor (#18).
- **Risk-Tiered Provider Routing**: tier1/2/3 model ladder, 429/timeout within-tier rotation, budget-aware downgrade escalation (#19).
- **Adversarial Fault-Injection Suite**: scope-traversal fuzz matrix, zombie-process tree-kill proofs, journal-corruption recovery matrix, deterministic 3-strike escalation test (#20).
- **Prometheus/OTel Exporter**: exposition-format renderer (histogram + counters), token/three-strike metrics, optional `[telemetry]` dependency group, no-op-safe OTel bridge (#8).

### Changed
- **Modularized core**: `supervisor.py` (1,660 lines) decomposed into a package (core/recovery/reporting/cleanup with patch-forwarding facade); `quality_plane.py` decomposed into `qc/` (personas/parsing/aggregator/runner) with a new `QualityPlane` facade (#9).

### Fixed & Hardened
- `apply_checkpoint` now quarantines poisoned WAL journals so rehydrated snapshots become the consistent source of truth.
- Corrupt state snapshots (wrong schema / binary noise) raise typed `StateError` instead of raw `KeyError`/`UnicodeDecodeError`.
- Bounded subprocess runs close Job Object handles deterministically, reaping pipe-holding orphans on Windows.
- CLI `--worktree` flag no longer leaks the sandbox env var beyond the command scope.

### Testing
- Test suite grew from 1,221 to **1,403 passing** (+182), with `ruff check` and `ruff format --check` fully clean.

---

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
