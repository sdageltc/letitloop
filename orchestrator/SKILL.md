---
name: letitloop
description: Comprehensive Autonomous Macro-Task Control Loop & Verification Engine. Plans DAG dependency contracts, executes tasks via pluggable worker adapters (Claude Code, Antigravity CLI, Omniroute, Ollama, direct LLMs), enforces zero-trust deterministic acceptance checks, arbitrates multi-lens quality reviews, and reconciles cryptographic evidence ledgers. Fully provider-agnostic (0-subscription local Ollama, routing gateways, or commercial APIs).
version: 0.1.0
author: letitloop-maintainers
tags:
  - orchestration
  - autonomous-macro-loop
  - dag-planner
  - zero-trust-verification
  - quality-plane
  - multi-provider
  - local-ollama
  - mcp-tools
  - claude-code-skill
  - openai-codex
  - codex
---

# `letitloop` (LIL) — Universal Agent Skill Specification

The assistant decomposes complex, ambiguous goals into typed contract dependency graphs (DAGs), delegates task execution through sandboxed worker adapters, runs deterministic acceptance verification, and arbitrates multi-lens quality reviews with zero hallucinations and crash-resilient state recovery.

### ⚡ Autonomous Engine Setup & Launch Protocol
When this skill is triggered by a user request:
1. **Auto-Install Check**: Check if the `lil` CLI engine is installed. If not yet installed in the current environment, run:
   ```bash
   pip install letitloop
   ```
   *(Or from GitHub: `pip install git+https://github.com/sdageltc/letitloop.git`)*
2. **Execute Full Orchestrator**:
   ```bash
   lil propose "<natural language goal>" --run
   ```
3. **MCP Server Mode**: If connecting through Model Context Protocol, the agent starts and queries `letitloop-mcp` to run deterministic checks, inspect DAG states, and evaluate quality lenses directly.

---

## 🌟 Core System Capabilities

```
                                  ┌───────────────────────────────┐
                                  │     Natural Language Goal     │
                                  └───────────────┬───────────────┘
                                                  ▼
                                  ┌───────────────────────────────┐
                                  │       LLM DAG Planner         │
                                  │   (Decomposes into Contracts) │
                                  └───────────────┬───────────────┘
                                                  ▼
                                  ┌───────────────────────────────┐
                                  │   Contract Dependency Graph   │
                                  └───────────────┬───────────────┘
                                                  ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │                     Supervisor Loop                     │
                     │  1. Atomic File-Locking & State Journal (WAL)          │
                     │  2. Sandboxed Worker Dispatch (Claude Code, agy, Local) │
                     │  3. Deterministic Acceptance Verifier (AST / Tests)     │
                     │  4. Multi-Lens Quality Plane (Security / Correctness)   │
                     │  5. Dynamic Replanning & Bounded 3-Strike Escalation    │
                     └────────────────────────────┬────────────────────────────┘
                                                  ▼
                                  ┌───────────────────────────────┐
                                  │ Cryptographic Evidence Ledger │
                                  │ & Reconciled Workspace Files  │
                                  └───────────────────────────────┘
```

---

## 📊 Deterministic Architecture vs. Other Autonomous Agents

| Architectural Feature | **`letitloop` (LIL)** | **OpenHands** | **SWE-agent** | **AutoGPT / AgentGPT** | **MetaGPT / ChatDev** |
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

## 🔑 Universal Provider & Model Support (Zero Subscriptions Required)

`letitloop` is completely vendor-agnostic. It does not require any specific commercial subscription. Users can run on free local models, open-source gateways, or frontier commercial APIs:

### 1. Free / Local Zero-Cloud Execution (Ollama, LM Studio, vLLM)
No API keys or cloud subscriptions needed:
```bash
# Run local models via Ollama
export OLLAMA_BASE_URL="http://localhost:11434/v1"
export WORKER_MODEL="ollama:qwen2.5-coder:32b"
export QC_MODEL="ollama:deepseek-r1:14b"
export PLANNER_MODEL="ollama:qwen2.5-coder:32b"
```

### 2. Multi-Provider Routing Gateways (Omniroute, OpenRouter, Groq)
Use dynamic gateway routing across dozens of models:
```bash
# Omniroute Gateway (Local or Remote Proxy)
export OMNIROUTE_API_KEY="your-key"
export OMNIROUTE_BASE_URL="http://localhost:8000/v1"
export WORKER_MODEL="omniroute:auto"

# OpenRouter
export OPENROUTER_API_KEY="your-openrouter-key"
export WORKER_MODEL="openrouter:anthropic/claude-3.5-sonnet"

# Groq (Ultra-Fast LPU Inference)
export GROQ_API_KEY="your-groq-key"
export WORKER_MODEL="groq:llama-3.3-70b-versatile"
```

### 3. Frontier Commercial APIs (Gemini, Anthropic, OpenAI, DeepSeek)
```bash
# Google Gemini
export GEMINI_API_KEY="your-gemini-key"
export WORKER_MODEL="gemini:gemini-2.5-flash"
export QC_MODEL="gemini:gemini-2.5-pro"

# OpenAI
export OPENAI_API_KEY="your-openai-key"
export WORKER_MODEL="openai:gpt-4o-mini"
export QC_MODEL="openai:gpt-4o"

# Anthropic Claude
export ANTHROPIC_API_KEY="your-anthropic-key"
export WORKER_MODEL="anthropic:claude-3-5-sonnet-latest"
export QC_MODEL="anthropic:claude-3-7-sonnet-latest"

# DeepSeek
export DEEPSEEK_API_KEY="your-deepseek-key"
export WORKER_MODEL="deepseek:deepseek-chat"
export QC_MODEL="deepseek:deepseek-reasoner"
```

---

## 🤖 Pluggable Worker Adapters

The Supervisor executes task contracts through specialized worker adapters:

| Adapter Name | Mode Identifier | Description |
|---|---|---|
| **Claude Code Adapter** | `claude-code` | Invokes the Claude Code CLI (`claude -p "<prompt>"`) autonomously |
| **Antigravity CLI Adapter** | `antigravity-cli` | Invokes Google Antigravity CLI (`agy exec --prompt "<prompt>"`) |
| **OpenAI Codex Adapter** | `codex` | Invokes the OpenAI Codex CLI (`codex exec --prompt "<prompt>"`) autonomously |
| **Omniroute Adapter** | `omniroute` | Invokes local/remote multi-provider proxy gateways |
| **Script Worker Adapter** | `script` | Executes custom local bash/Python scripts with injected environment variables |
| **Direct API Worker** | `direct` | In-process zero-dependency stdlib HTTP transport |
| **Mock Worker** | `mock` | Deterministic simulation worker for CI, offline verification, and dry-runs |

---

## 🛠️ Complete MCP Tool Catalog (8 Native Tools)

When connecting via Model Context Protocol (`letitloop-mcp`), the assistant has direct access to the full orchestrator engine:

### 1. `create_goal`
Initializes a new macro-goal with isolated state directory.
* **Arguments**: `goal_id` (str), `title` (str), `description` (str)

### 2. `load_contract_file`
Loads and validates a contract JSON file against strict schema rules.
* **Arguments**: `contract_path` (str)

### 3. `run_contract_verification`
Executes deterministic acceptance checks against task outputs.
* **Arguments**: `contract_path` (str), `task_id` (optional str)

### 4. `run_quality_review`
Executes multi-lens quality plane review on produced artifacts.
* **Arguments**: `contract_path` (str), `lens` (optional str, e.g. `code_correctness`, `security`, `documentation`, `adversarial_architecture_audit`)

### 5. `execute_supervisor_plan`
Executes a multi-task contract DAG with automated retries, WAL state logging, and parallel execution.
* **Arguments**: `goal_id` (str), `contracts` (array of contract dicts), `parallel` (bool, default `False`)

### 6. `inspect_task_state`
Inspects state journal, attempt history, and evidence artifacts for a specific task.
* **Arguments**: `task_id` (str), `run_dir` (optional str)

### 7. `reconcile_workspace_state`
Audits file hashes against cryptographic evidence ledgers to detect out-of-band edits or missing outputs.
* **Arguments**: `goal_id` (str), `contracts` (array of contract dicts)

### 8. `get_system_health`
Returns runtime status, registered worker adapters, configured model transports, and memory health.
* **Arguments**: None

---

## 📋 Comprehensive Contract JSON Schema

Every step in an autonomous plan is defined by a strictly validated contract:

```json
{
  "task_id": "auth-jwt-01",
  "title": "Implement JWT validation middleware",
  "status": "drafted",
  "risk_tier": "qc_required",
  "workspace_scope": {
    "allow": ["src/auth/", "tests/test_auth.py"],
    "deny": [".env", "config/secrets.json"]
  },
  "objective": "Build token decode, expiration validation, and signature verification with unit tests.",
  "worker": {
    "model": "gemini:gemini-2.5-flash",
    "fallback_model": "openai:gpt-4o-mini",
    "max_attempts": 3,
    "adapter": "direct"
  },
  "inputs": [],
  "outputs": [
    {"path": "src/auth/jwt.py"},
    {"path": "tests/test_auth.py"}
  ],
  "acceptance_checks": [
    {
      "id": "syntax_check",
      "kind": "syntax",
      "path": "src/auth/jwt.py",
      "expected": "python"
    },
    {
      "id": "unit_tests",
      "kind": "command",
      "command": "pytest tests/test_auth.py -q",
      "expected": 0,
      "timeout_sec": 30
    },
    {
      "id": "doc_check",
      "kind": "render",
      "path": "src/auth/README.md",
      "expected": "markdown"
    },
    {
      "id": "scope_check",
      "kind": "undeclared_outputs"
    }
  ],
  "qc": {
    "required": true,
    "lens": "security"
  }
}
```

### Supported Acceptance Check Kinds:
- `syntax`: AST syntax validation for Python, TypeScript, JavaScript, Go, Rust.
- `command`: Subprocess command execution with exit code matching and process-tree killing on timeout.
- `file_exists`: Verifies output files exist and are non-empty.
- `min_size`: Asserts minimum byte size of generated artifact.
- `content_regex`: Matches required regular expressions in output text.
- `required_sections`: Asserts presence of required Markdown headers or JSON keys.
- `render`: Heuristic source structure validator for Markdown and HTML.
- `undeclared_outputs`: Detects any modified or created files outside the declared output manifest.

---

## ⚡ CLI Operational Playbook

For command-line and shell execution:

```bash
# 1. Propose & Preview a Contract DAG from natural language
lil propose "Build a rate-limiting middleware with Redis backend and unit tests" --run

# 2. Monitor execution in real-time via live ASCII Dashboard
lil dashboard

# 3. Reconcile workspace file hashes with evidence ledger
lil reconcile <goal_id>

# 4. Diagnose system configuration and provider health
lil doctor
```

---

## 🛡️ Fail-Closed Governance Invariants

1. **Zero Blind Edits**: All worker file mutations must reside within `workspace_scope.allow`.
2. **Deterministic Empirical Proof**: A task is never marked complete without evaluating acceptance check exit codes.
3. **Bounded 3-Strike Policy**: If a worker fails 3 consecutive attempts on a contract, the supervisor halts that branch, marks it `ESCALATED`, and emits an `impossibility.md` report.
4. **Secret Masking Firewall**: API keys, OAuth tokens, and SSH keys are automatically redacted from evidence ledgers and stdout logs.
