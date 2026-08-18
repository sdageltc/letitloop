<p align="center">
  <img src="assets/logo.png" alt="let it loop (LIL)" width="340" style="border-radius: 20px;">
</p>

# let it loop (LIL)

**let it loop (LIL)** is an autonomous macro-task orchestration and verification control loop for AI coding agents. It provides a durable, production-grade execution backbone featuring automated DAG contract planning, crash-resilient supervisor execution (Write-Ahead Logging), deterministic multi-phase verification, multi-lens quality reviews, and universal Model Context Protocol (MCP) support.

[![CI](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml/badge.svg)](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Supported](https://img.shields.io/badge/MCP-Supported-purple.svg)](docs/MCP_GUIDE.md)

```bash
# 1. Propose & Decompose into Strongly-Typed DAG Contracts
$ lil propose "Build zero-downtime distributed rate limiter with Redis backend"

[lil] Decomposing objective into contract DAG (3 nodes, 0 cycles)...
[lil] Generated 3 strongly-typed execution contracts:
  ├─ [01_rate_limiter_core]  Scope: src/limiter.py (allowed: src/limiter.py)
  ├─ [02_redis_storage_wire] Scope: src/storage.py (allowed: src/storage.py) [depends on: 01]
  └─ [03_integration_tests]  Scope: tests/test_limiter.py (allowed: tests/*) [depends on: 01, 02]

# 2. Execute with Write-Ahead Logging & Deterministic Zero-Trust Verifiers
$ lil run --doctor --strict

[doctor] Checking Python 3.12, Git, Pytest, AST parsers... [PASS]
[supervisor] Active worker adapter: claude-code (auto-fallback: ollama/qwen2.5-coder)
[wal] Journal initialized at .letitloop/runs/run_20260818_2145/state.wal.jsonl

▶ Executing Contract 01/03: 01_rate_limiter_core
  ├─ [worker] claude-code generating token-bucket implementation... done (4.2s)
  ├─ [verifier] AST Syntax Validation ................................ [PASS]
  ├─ [verifier] File Existence (src/limiter.py) ...................... [PASS]
  └─ [verifier] Scope Fence (0 undeclared files mutated) ............. [PASS]

▶ Executing Contract 02/03: 02_redis_storage_wire
  ├─ [worker] claude-code wiring async Redis pipeline... done (3.8s)
  ├─ [verifier] Command Check (`pytest tests/test_storage.py`) ...... [PASS] (exit: 0)
  └─ [verifier] Regex Check (`class RedisTokenBucket`) ............... [PASS]

▶ Executing Contract 03/03: 03_integration_tests
  ├─ [worker] claude-code generating 40 adversarial concurrency tests... done (6.1s)
  ├─ [verifier] Command Check (`pytest tests/ -v`) .................. [PASS] (40 passed in 1.1s)
  └─ [quality-plane] 5-Lens Review (Correctness, Security, Docs, Tests, Arch) ... [PASS (5/5)]

================================================================================
✨ MACRO-TASK COMPLETE: 3/3 Contracts Verified | 0 Retries | 100% Deterministic
================================================================================
```

---

## Key Capabilities

- **Autonomous DAG Planning**: Decomposes natural language objectives into cryptographically scoped, strongly-typed JSON contract dependency graphs with cycle detection.
- **Fault-Tolerant Supervisor Loop**: State journal with WAL (Write-Ahead Logging), crash recovery, Win32/POSIX atomic file-locking, and bounded 3-strike retries with strategy mutation.
- **Zero-Trust Verification Engine**: 8 distinct deterministic acceptance check kinds (AST syntax parsers, command exit-code assertions, regex matchers, file validators, size bounds, and undeclared output detectors).
- **Multi-Lens Quality Plane**: Multi-perspective evaluation with 5 specialized lenses (*Code Correctness*, *Security Hardening*, *Documentation Fidelity*, *Test Completeness*, *Adversarial Architecture Audit*) and formal arbitration.
- **Native Model Context Protocol (MCP) Server**: 8 stdio JSON-RPC tools connecting directly with Claude Code, OpenAI Codex, Cursor, Google Antigravity, Hermes Agent, OpenCode, Cline, and Windsurf.
- **10 Pluggable Worker Adapters**: Native execution interfaces for Claude Code, OpenAI Codex, Google Antigravity (`agy`), OpenCode, Hermes Agent, Cline, Aider, Omniroute gateways, local scripts, and direct LLMs.
- **Zero-Subscription Independence**: Seamlessly run 100% locally via Ollama/vLLM, multi-model gateways (Omniroute, OpenRouter, Groq), or commercial frontier APIs.
- **Interactive Terminal Dashboard**: Zero-dependency live ASCII DAG status matrix, execution progress bars, and event telemetry (`lil dashboard`).
- **Turnkey Containerization**: Production multi-stage Docker build and Docker Compose orchestration.

---

## How letitloop Compares to Other Autonomous Agent Systems

Unlike conversational agent loops that rely on open-ended text streaming and optimistic assumptions, `letitloop` operates like an **Operating System process scheduler**: every task requires a cryptographic contract, empirical acceptance proof, and bounded retry governance.

| Architectural Feature | **letitloop (LIL)** | **OpenHands** | **SWE-agent** | **AutoGPT / AgentGPT** | **MetaGPT / ChatDev** |
|---|:---:|:---:|:---:|:---:|:---:|
| **Orchestration Model** | **Typed DAG Contracts** | Container Terminal Chat | Single-Task Benchmark Agent | Open-Ended While-Loop | Multi-Role Chat Simulation |
| **Deterministic Verifier** | **8 Machine-Verified Checks** (AST, Cmd Exit Codes, Regex, Render, Scope) | Eyeball / Agent Judgement | Unit Test Execution Only | None (LLM Self-Assessment) | Role-Play Text Review |
| **Crash Recovery & Resume** | **Write-Ahead Log (WAL) Journal** | Manual Session Replay | No (Ephemeral Run) | None (Lost State) | None |
| **Retry & Failure Policy** | **Bounded 3-Strike with Strategy Mutation & Impossibility Proof** | Infinite Loop / Timeout | Fixed Retries / Prompt Dump | Infinite Hallucination Loop | Reprompting Loop |
| **Sandbox Scope Enforcement** | **Strict `allow`/`deny` & Undeclared Output Detection** | Docker Container Isolation | Bash Environment Isolation | None (Unrestricted Host) | None |
| **Quality Plane & Lenses** | **5 Specialized Lenses + Senior Arbitration & QC Overrule** | Single Review Step | None | None | Simulated Peer Chat |
| **AI Ecosystem & Skill Support** | **Universal Skill & MCP across 8 Platforms** (Claude Code, Antigravity, OpenAI Codex, Hermes, Cursor, OpenCode, Cline, Windsurf) | Standalone Web UI / Docker | Standalone CLI | Standalone CLI / Web | Standalone Framework |
| **Zero-Subscription Local Use** | **Native Ollama, vLLM, LM Studio & Omniroute Support** | Local LLMs supported via LiteLLM | Local LLMs supported | Local LLMs (Ollama) | Local LLMs supported |

---

## Installation & Getting Started

### Option A: Zero-Install AI Agent Skill (1-Second Setup)
Enhance your existing AI coding agent without installing Python or cloning this repository:

```bash
# Universal AI agent skill package manager
npx skills add sdageltc/letitloop
```
*Or copy [`SKILL.md`](https://raw.githubusercontent.com/sdageltc/letitloop/main/skill/SKILL.md) directly into your agent's skills directory.*

---

### Option B: Full Python Engine & CLI (`lil`)
For autonomous execution loops with machine-verified proofs, AST syntax checks, and crash resilience:

```bash
# Install via pip
pip install letitloop

# (Or directly from GitHub)
pip install git+https://github.com/sdageltc/letitloop.git

# 1-Click Skill Installation across all detected AI agents
lil install-skill --all
```

---

### Skill-Only Protocol vs. Full Python Engine

| Capability | Zero-Install Skill (`SKILL.md`) | Full Engine (`pip install letitloop`) |
|---|:---:|:---:|
| **Installation Requirement** | Zero (Pure Markdown Prompt) | Python 3.11+ Runtime |
| **Orchestration Lifecycle** | In-Chat Self-Governed DAG | Machine Supervisor Daemon |
| **Retry Discipline** | 3-Strike Behavioral Protocol | 3-Strike State Machine with WAL |
| **AST Syntax Parsers** | Prompt-Instructed | Native Machine-Verified (AST) |
| **Exit-Code Test Proofs** | Agent-Reported | Subprocess Exit Code (`exit_code == 0`) |
| **Crash Resilient State** | Ephemeral Chat Session | Atomic Write-Ahead Log (`state.wal.jsonl`) |
| **Terminal Dashboard** | None | Live ASCII Matrix (`lil dashboard`) |
| **MCP Server Integration** | None | 8-Platform stdio JSON-RPC Server |

---

## Quick Start

### 1. Model & Provider Configuration

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

## Architecture & Control Loop

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

## Supported Worker Adapters & Gateways

| Worker Adapter | Identifier | Description |
|---|---|---|
| **Google Antigravity CLI** | `antigravity-cli` | Invokes the official `agy` subagent tool safely |
| **Claude Code CLI** | `claude-code` | Autonomous task execution via the Claude Code CLI |
| **OpenAI Codex CLI** | `codex` | Autonomous task execution via the OpenAI Codex CLI |
| **OpenCode CLI** | `opencode` | Autonomous execution via OpenCode agent CLI |
| **Hermes Agent CLI** | `hermes` | Autonomous execution via Nous Research Hermes agent CLI |
| **Cline CLI** | `cline` | Headless execution via Cline autonomous coding runner |
| **Aider Pair Programmer** | `aider` | Pair programming execution via Aider CLI |
| **Omniroute Gateway** | `omniroute` | Multi-model fallback routing through local/remote gateways |
| **Script Worker** | `script` | Executes local shell/Python automation scripts with env isolation |
| **Direct LLM APIs** | `direct` | In-process calls to Gemini, OpenAI, Anthropic, DeepSeek, or Ollama |
| **Mock Worker** | `mock` | Deterministic simulation worker for CI and offline integration tests |

---

## Quick Safe Demo (Zero Cloud Keys)

You can run a complete, deterministic macro-task loop completely offline without any API keys using the built-in `mock` worker:

```bash
# 1. Propose a plan
lil propose "Build a mathematical utility module" --worker mock

# 2. Inspect and approve the generated Contract DAG
lil approve <goal_id>

# 3. Execute under supervisor oversight
lil run-approved <goal_id>

# 4. View execution ledger and metrics
lil status <goal_id>
```

---

## Fast Developer Loop & Testing

`letitloop` includes a high-performance in-process test runner designed to bypass slow pytest plugin autoloads during development:

```bash
# 1. Fast in-process test runner (1,122 tests in ~75s)
python fast_test_runner.py

# 2. Run targeted unit test suite
pytest tests/test_supervisor.py -v

# 3. Run hostile security & fuzzing suites
pytest tests/test_wal_corruption_recovery.py tests/test_verifier_ast_fuzz.py tests/test_worker_escaping.py -v

# 4. Run full integration test suite
pytest tests/test_integration.py -v
```

---

## Operational Environment & Storage

By default, task execution state, WAL journals, and checkpoints are stored in `scratch/orchestrator_runs` (which is excluded from Git via `.gitignore`).

To store runs in an external directory (e.g. for CI isolation or persistent daemon usage), set the `LIL_RUN_DIR` environment variable:

```bash
export LIL_RUN_DIR=~/.letitloop/runs
```

---

## Security & Sandboxing Architecture

`letitloop` operates under a zero-trust execution model:
- **Redaction Firewall**: Automatic masking of PATs, OAuth keys, AWS credentials, GCP tokens, and PEM private keys.
- **Environment Scrubbing**: Sensitive parent environment variables are stripped prior to worker execution.
- **Scope Checking**: Userland filesystem snapshot diffing (`scope.py`) enforcing directory bounds and declared output paths.
- **Sandboxing Recommendation**: For untrusted or autonomous workloads, running `letitloop` within a container runtime (Docker/Podman/Firecracker) with network isolation is strongly recommended.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more details.
