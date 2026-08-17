# let it loop (LIL)

**let it loop (LIL)** — Autonomous Macro-Task Orchestration & Verification Control Loop. A durable, production-grade agent framework featuring automated DAG contract planning, crash-resilient supervisor execution, deterministic multi-phase verification, multi-lens quality reviews, and native Model Context Protocol (MCP) support.

[![CI](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml/badge.svg)](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Supported](https://img.shields.io/badge/MCP-Supported-purple.svg)](docs/MCP_GUIDE.md)

---

## 🌟 Key Capabilities

- 🧠 **Autonomous DAG Planning**: Decomposes natural language objectives into cryptographically scoped, strongly-typed JSON contract dependency graphs with cycle detection.
- 🔄 **Fault-Tolerant Supervisor Loop**: State journal with WAL (Write-Ahead Logging), crash recovery, Win32/POSIX atomic file-locking, and bounded 3-strike retries with strategy mutation.
- 🛡️ **Zero-Trust Verification Engine**: 8 distinct deterministic acceptance check kinds (AST syntax parsers, command exit-code assertions, regex matchers, file validators, size bounds, and undeclared output detectors).
- ⚖️ **Multi-Lens Quality Plane**: Multi-perspective evaluation with 5 specialized lenses (*Code Correctness*, *Security Hardening*, *Documentation Fidelity*, *Test Completeness*, *Adversarial Architecture Audit*) and formal arbitration.
- 🔌 **Native Model Context Protocol (MCP) Server**: 8 stdio JSON-RPC tools connecting directly with **Claude Code**, **Cursor**, **Google Antigravity**, **Hermes Agent**, **OpenCode**, **Cline**, and **Windsurf**.
- 🤖 **9 Pluggable Worker Adapters**: Native execution interfaces for Claude Code, Google Antigravity (`agy`), OpenCode, Hermes Agent, Cline, Aider, Omniroute gateways, local scripts, and direct LLMs.
- 🆓 **Zero-Subscription Independence**: Seamlessly run 100% locally via Ollama/vLLM, multi-model gateways (Omniroute, OpenRouter, Groq), or commercial frontier APIs.
- 📊 **Interactive Terminal Dashboard**: Zero-dependency live ASCII DAG status matrix, execution progress bars, and event telemetry (`lil dashboard`).
- 📦 **Turnkey Containerization**: Production multi-stage Docker build and Docker Compose orchestration.

---

## 📊 How `letitloop` Compares to Other Autonomous Agent Systems

Unlike conversational agent loops that rely on open-ended text streaming and optimistic assumptions, `letitloop` operates like an **Operating System process scheduler**: every task requires a cryptographic contract, empirical acceptance proof, and bounded retry governance.

| Architectural Feature | **`letitloop` (LIL)** | **OpenHands** | **SWE-agent** | **AutoGPT / AgentGPT** | **MetaGPT / ChatDev** |
|---|:---:|:---:|:---:|:---:|:---:|
| **Orchestration Model** | **Typed DAG Contracts** | Container Terminal Chat | Single-Task Benchmark Agent | Open-Ended While-Loop | Multi-Role Chat Simulation |
| **Deterministic Verifier** | **8 Machine-Verified Checks** (AST, Cmd Exit Codes, Regex, Render, Scope) | Eyeball / Agent Judgement | Unit Test Execution Only | None (LLM Self-Assessment) | Role-Play Text Review |
| **Crash Recovery & Resume** | **Write-Ahead Log (WAL) Journal** | Manual Session Replay | No (Ephemeral Run) | None (Lost State) | None |
| **Retry & Failure Policy** | **Bounded 3-Strike with Strategy Mutation & Impossibility Proof** | Infinite Loop / Timeout | Fixed Retries / Prompt Dump | Infinite Hallucination Loop | Reprompting Loop |
| **Sandbox Scope Enforcement** | **Strict `allow`/`deny` & Undeclared Output Detection** | Docker Container Isolation | Bash Environment Isolation | None (Unrestricted Host) | None |
| **Quality Plane & Lenses** | **5 Specialized Lenses + Senior Arbitration & QC Overrule** | Single Review Step | None | None | Simulated Peer Chat |
| **AI Ecosystem & Skill Support** | **Universal Skill & MCP across 7 Platforms** (Claude Code, Antigravity, Hermes, Cursor, OpenCode, Cline, Windsurf) | Standalone Web UI / Docker | Standalone CLI | Standalone CLI / Web | Standalone Framework |
| **Zero-Subscription Local Use** | **Native Ollama, vLLM, LM Studio & Omniroute Support** | Local LLMs supported via LiteLLM | Local LLMs supported | Local LLMs (Ollama) | Local LLMs supported |

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
