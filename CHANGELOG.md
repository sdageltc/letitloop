# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-08-17

### 🚀 Added
- **Model Context Protocol (MCP) Server**: Full stdio JSON-RPC server with `letitloop-mcp` entry point exposing 8 tools for Google Antigravity, Claude Desktop, Cursor, and OpenCode.
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
