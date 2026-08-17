# let it loop (LIL)

**let it loop (LIL)** — Autonomous Task Orchestration System. A durable, production-grade macro-task control loop with automated DAG planning, execution, deterministic verification, multi-lens quality review, and Model Context Protocol (MCP) support.

[![CI](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml/badge.svg)](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Ready](https://img.shields.io/badge/MCP-Supported-purple.svg)](docs/MCP_GUIDE.md)

---

## 🌟 Key Highlights

- 🧠 **Autonomous DAG Planning**: Decomposes natural language goals into cryptographically scoped, typed contract dependency graphs.
- 🔄 **Fault-Tolerant Supervisor Loop**: State journal with WAL (Write-Ahead Logging), crash recovery, atomic heartbeat file-locking, and bounded 3-strike retries.
- 🛡️ **Zero-Trust Verification Engine**: Deterministic acceptance checks (AST syntax checks, regex matchers, unit tests, and command exit code assertions).
- ⚖️ **Multi-Lens Quality Plane**: Multi-agent evaluation with specialized lenses (*Code Correctness*, *Security Hardening*, *Documentation Clarity*, *Test Completeness*).
- 🔌 **Native Model Context Protocol (MCP) Server**: Connect directly with Claude Desktop, Cursor, Antigravity, and OpenCode assistants via standard stdio JSON-RPC.
- 📊 **Interactive Terminal Dashboard**: Live ASCII DAG status matrix, execution progress bars, and event telemetry.
- 📦 **Containerized & Turnkey**: Complete multi-stage Docker build and Docker Compose orchestration.

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/sdageltc/letitloop.git
cd letitloop

# Install package and dependencies
pip install -e .

# Test CLI
lil --help
```

---

### 2. Model Context Protocol (MCP) Server

`letitloop` includes a built-in MCP server for seamless integration with AI coding assistants.

#### Claude Desktop Setup
Add to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"],
      "cwd": "/path/to/your/workspace"
    }
  }
}
```

See [docs/MCP_GUIDE.md](docs/MCP_GUIDE.md) for full Cursor and IDE setup guides.

---

### 3. CLI Usage

#### Initialize and Execute a Macro-Goal
```bash
# Propose a plan from a natural language prompt
lil propose "Build a user authentication module with JWT validation and unit tests" --run

# View real-time terminal status dashboard
lil dashboard

# Run deterministic reconciliation audit across workspace files
lil reconcile <goal_id>
```

---

## 🏗️ Architecture Overview

```
                          ┌───────────────────────────┐
                          │   Natural Language Goal   │
                          └─────────────┬─────────────┘
                                        ▼
                          ┌───────────────────────────┐
                          │     LLM DAG Planner       │
                          └─────────────┬─────────────┘
                                        ▼
                          ┌───────────────────────────┐
                          │ Contract Dependency Graph │
                          └─────────────┬─────────────┘
                                        ▼
                       ┌─────────────────────────────────┐
                       │       Supervisor Loop           │
                       │  - Preflight Scoping            │
                       │  - Pluggable Worker Execution   │
                       │  - Deterministic Verification   │
                       │  - Multi-Lens QC Evaluation     │
                       └─────────────┬───────────────────┘
                                     ▼
                    ┌─────────────────────────────────┐
                    │ Cryptographic Evidence Ledger   │
                    │ & Reconciled Workspace Output   │
                    └─────────────────────────────────┘
```

---

## 🧪 Running Test Suites

```bash
# Run all unit tests (fast logic & state machine)
python -m pytest tests -q --ignore=tests/test_integration.py --ignore=tests/test_benchmarks.py

# Run end-to-end integration tests
python -m pytest tests/test_integration.py -v

# Run performance benchmarks (requires pytest-benchmark)
python -m pytest tests/test_benchmarks.py
```

---

## 🔒 Security & Privacy

`letitloop` operates under strict security and data protection standards:
- **Redaction Firewall**: Automatic masking of PATs, OAuth keys, AWS credentials, GCP tokens, and PEM private keys.
- **Sandbox Scoping**: Deny-by-default file paths and containment enforcement.
- **Safe Subprocess Spawning**: Sandboxed temporary prompt isolation with explicit 0o600 permissions.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more details.
