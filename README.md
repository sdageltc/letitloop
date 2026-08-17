# let it loop (LIL)

**let it loop (LIL)** — Autonomous Macro-Task Orchestration & Verification Control Loop. A durable, production-grade agent framework featuring automated DAG contract planning, crash-resilient supervisor execution, deterministic multi-phase verification, multi-lens quality reviews, and native Model Context Protocol (MCP) support.

[![CI](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml/badge.svg)](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Supported](https://img.shields.io/badge/MCP-Supported-purple.svg)](docs/MCP_GUIDE.md)

---

## 🌟 Key Capabilities

- 🧠 **Autonomous DAG Planning**: Decomposes natural language objectives into cryptographically scoped, strongly-typed JSON contract dependency graphs.
- 🔄 **Fault-Tolerant Supervisor Loop**: State journal with WAL (Write-Ahead Logging), crash recovery, atomic file-locking, and bounded 3-strike retries.
- 🛡️ **Zero-Trust Verification Engine**: Multi-tiered deterministic acceptance checks (AST syntax validators, regex matchers, unit test runners, and command exit code assertions).
- ⚖️ **Multi-Lens Quality Plane**: Multi-perspective evaluation with specialized lenses (*Code Correctness*, *Security Hardening*, *Documentation Fidelity*, *Test Coverage*).
- 🔌 **Native Model Context Protocol (MCP) Server**: Connect directly with **Google Antigravity**, **Claude Code**, **Cursor**, and **OpenCode** via standard stdio JSON-RPC.
- 🤖 **Pluggable Worker Adapters**: Native interfaces for Claude Code, Google Antigravity CLI (`agy`), Omniroute gateways, custom scripts, and direct LLM APIs.
- 📊 **Interactive Terminal Dashboard**: Zero-dependency live ASCII DAG status matrix, execution progress bars, and event telemetry (`lil dashboard`).
- 📦 **Turnkey Containerization**: Production multi-stage Docker build and Docker Compose orchestration.

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/sdageltc/letitloop.git
cd letitloop

# Install package in editable mode
pip install -e .

# Verify CLI installation
lil --help
```

---

### 2. Model & Provider Configuration

Configure your environment variables in `.env` (see [`.env.example`](.env.example)):

```bash
# Core API Keys
export GEMINI_API_KEY="your-gemini-key"
export OPENAI_API_KEY="your-openai-key"
export ANTHROPIC_API_KEY="your-anthropic-key"
export DEEPSEEK_API_KEY="your-deepseek-key"

# Model Routing Defaults
export WORKER_MODEL="gemini:gemini-3.6-flash"
export QC_MODEL="gemini:gemini-3.1-pro"
export PLANNER_MODEL="gemini:gemini-3.6-flash"

# Optional Gateways (Omniroute, OpenRouter, Groq, Ollama)
export OMNIROUTE_BASE_URL="http://localhost:8000/v1"
```

---

### 3. Model Context Protocol (MCP) Server

`letitloop` includes a built-in MCP server (`letitloop-mcp`) exposing 8 autonomous management tools for AI assistants.

#### Configuration for Google Antigravity & Cursor
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "letitloop-mcp",
      "env": {
        "WORKER_MODEL": "gemini:gemini-3.6-flash",
        "QC_MODEL": "gemini:gemini-3.1-pro"
      }
    }
  }
}
```

#### Configuration for Claude Code
```bash
claude mcp add letitloop -- python -m orchestrator.mcp_server
```
Or in `~/.claude.json`:
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"]
    }
  }
}
```
*For detailed integration instructions, see [docs/MCP_GUIDE.md](docs/MCP_GUIDE.md).*

---

### 4. CLI Usage

#### Propose and Run an Autonomous Macro-Goal
```bash
# Propose a contract DAG from a natural language prompt and execute it
lil propose "Build a user authentication module with JWT validation and unit tests" --run

# View real-time terminal dashboard
lil dashboard

# Run deterministic reconciliation audit across workspace files
lil reconcile <goal_id>
```

---

## 🏗️ Architecture & Control Loop

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
                       │  - Preflight & Sandbox Scoping  │
                       │  - Pluggable Worker Execution   │
                       │  - Deterministic Verification   │
                       │  - Multi-Lens QC Review         │
                       └─────────────┬───────────────────┘
                                     ▼
                    ┌─────────────────────────────────┐
                    │ Cryptographic Evidence Ledger   │
                    │ & Reconciled Workspace Outputs  │
                    └─────────────────────────────────┘
```

---

## 🔌 Supported Worker Adapters & Gateways

| Worker Adapter | Identifier | Description |
|---|---|---|
| **Google Antigravity CLI** | `antigravity-cli` | Invokes the official `agy` subagent tool safely |
| **Claude Code CLI** | `claude-code` | Autonomous task execution via the Claude Code CLI |
| **Omniroute Gateway** | `omniroute` | Multi-model fallback routing through local/remote gateways |
| **Script Worker** | `script` | Executes local shell/Python automation scripts with env isolation |
| **Direct LLM APIs** | `direct` | In-process calls to Gemini, OpenAI, Anthropic, DeepSeek, or Ollama |
| **Mock Worker** | `mock` | Deterministic simulation worker for CI and offline integration tests |

---

## 🧪 Testing & Verification

```bash
# Run all unit tests (362 tests across 65 modules)
python -m pytest tests -q --ignore=tests/test_integration.py --ignore=tests/test_benchmarks.py

# Run end-to-end integration tests
python -m pytest tests/test_integration.py -v

# Fast in-process verification runner
python fast_test_runner.py
```

---

## 🔒 Security & Privacy

`letitloop` is built with a zero-trust security architecture:
- **Redaction Firewall**: Automatic masking of PATs, OAuth keys, AWS credentials, GCP tokens, and PEM private keys.
- **Sandbox Scoping**: Deny-by-default path scoping preventing directory traversal or unauthorized file modifications.
- **Safe Subprocess Spawning**: Isolated execution environments with explicit permission boundaries.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more details.
