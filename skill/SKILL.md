---
name: letitloop
description: Autonomous Macro-Task Orchestration & Verification Control Loop. Decomposes natural language objectives into dependency contract DAGs, executes tasks via pluggable worker adapters, enforces zero-trust verification checks, and arbitrates multi-lens quality reviews.
version: 0.1.0
author: letitloop-maintainers
tags:
  - orchestration
  - autonomous-loop
  - dag-planner
  - deterministic-verification
  - quality-plane
  - mcp-tools
---

# `letitloop` — Autonomous Macro-Task Orchestrator Skill

When this skill is activated, you operate as the **Orchestrator Architect**. You leverage `letitloop` (via CLI `lil` or native MCP tools) to plan, execute, verify, and reconcile complex multi-step engineering tasks with zero hallucinations and fail-closed state management.

---

## 🎯 When to Activate This Skill

Use `letitloop` whenever a task requires:
1. **Multi-Step / Multi-File Implementations**: Complex features spanning multiple modules, database models, or API endpoints.
2. **Deterministic Acceptance Testing**: Tasks requiring explicit proof (exit codes == 0, AST syntax validity, regex pattern matches, zero test regressions).
3. **Multi-Lens Quality Assurance**: High-stakes code changes requiring adversarial audit (*security*, *code correctness*, *test coverage*, *documentation fidelity*).
4. **Autonomous Crash-Resilient Loops**: Long-running overnight tasks requiring automated 3-strike retries, WAL state recovery, and deadlock-free execution.

---

## 🧠 Architectural Roles

```
┌─────────────────────────────────────────────────────────────┐
│                    Lead Agent (Architect)                   │
│   - Receives User Goal & Refines Intent                     │
│   - Generates or Approves Contract Dependency Graph (DAG)   │
│   - Evaluates QC Multi-Lens Findings & Overrule Audits      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                   letitloop Runtime Engine                  │
│   - Atomic Heartbeat Lock & Write-Ahead State Journal       │
│   - Sandboxed Worker Dispatch (Claude Code, agy, Omniroute) │
│   - Deterministic Acceptance Verifiers (Syntax, Cmd, Regex) │
│   - Cryptographic Evidence Ledger & Workspace Reconciler    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Preferred Execution Paths

### Path A: Native MCP Server Tools (Recommended for Claude Desktop, Antigravity & Cursor)
If the `letitloop` MCP server is registered:
1. `create_goal(goal_id="feature-auth", title="JWT Authentication System", description="...")`
2. `execute_supervisor_plan(goal_id="feature-auth", contracts=[...], parallel=True)`
3. `run_contract_verification(contract_path="contracts/auth.json")`
4. `reconcile_workspace_state(goal_id="feature-auth", contracts=[...])`

### Path B: CLI Execution (Terminal / Shell Workers)
If invoking via terminal:
```bash
# 1. Propose & Preview Contract DAG
lil propose "Implement rate-limiting middleware with Redis backend and unit tests"

# 2. Execute Plan with Supervisor Loop
lil run <goal_id>

# 3. Monitor Real-Time State Dashboard
lil dashboard

# 4. Deterministic Workspace Reconciliation
lil reconcile <goal_id>
```

---

## 📋 Contract Schema Specification

When drafting individual task contracts for the DAG:

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
  "objective": "Build token decode, expiration check, and signature verification.",
  "worker": {
    "model": "gemini:gemini-3.7-flash",
    "max_attempts": 3
  },
  "inputs": [],
  "outputs": [
    {"path": "src/auth/jwt.py"},
    {"path": "tests/test_auth.py"}
  ],
  "acceptance_checks": [
    {"id": "syntax_jwt", "kind": "syntax", "path": "src/auth/jwt.py", "expected": "python"},
    {"id": "test_pass", "kind": "command", "command": "pytest tests/test_auth.py -q", "expected": 0}
  ],
  "qc": {
    "required": true,
    "lens": "security"
  }
}
```

---

## 🔒 Safety & Governance Invariants

1. **Zero Blind Edits**: Always declare exact file outputs in `workspace_scope.allow`.
2. **Empirical Proof Before Completion**: A contract is only marked `COMPLETE` when all `acceptance_checks` yield exit code 0.
3. **Strict Bounded Retries**: If a worker fails 3 consecutive attempts on the same task contract, the supervisor escalates to `ESCALATED` and generates an Impossibility Report. Never loop infinitely.
4. **Secret Masking**: Environment variables containing private keys or credentials are automatically redacted by the verifier ledger.
