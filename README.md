<div align="center">

# LetItLoop (LIL) 🛡️

**Deterministic Verification Harness and Crash-Safe Execution Gate for AI Coding Agents**

[![CI](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml/badge.svg)](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml)
[![Core Engine](https://img.shields.io/badge/Core%20Engine-v0.2.0-green.svg)](https://github.com/sdageltc/letitloop)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**[Durability Benchmark](https://github.com/sdageltc/agent-durability-bench)** • **[PR Verification Action](https://github.com/sdageltc/letitloop-action)** • **[Engine Core](https://github.com/sdageltc/letitloop)**

</div>

---

## Overview

Most AI coding workflows suffer from two fundamental engineering failure modes:
1. **Whole-File Rewrite Pathology**: When an agent attempts a 5-line bug fix, it re-generates the entire 400-line file, wiping out comments, mutating unrelated function signatures, and inflating token costs.
2. **State Amnesia on Process Crash**: If a long-running execution terminates mid-loop (`kill -9`, spot eviction, rate-limit timeout), the agent loses all progress and starts from Step 0.

`letitloop` is a lightweight, zero-trust verification engine and supervisor loop designed to wrap coding agents with mechanical safety gates.

---

## The Three Mechanical Pillars

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LETITLOOP CORE ARCHITECTURE                     │
├─────────────────────────┬────────────────────────┬─────────────────────┤
│ 1. AST Node Splicer     │ 2. WAL State Machine   │ 3. Verification Gate│
├─────────────────────────┼────────────────────────┼─────────────────────┤
│ Method-level splicing.  │ Atomic SQLite/JSONL    │ 5-Pillar acceptance │
│ Preserves comments,     │ state journal. Resumes │ checks (AST syntax, │
│ decorators, whitespace. │ in 2ms after crash.    │ tests, scope fence).│
└─────────────────────────┴────────────────────────┴─────────────────────┘
```

### 1. AST Node Splicer (`orchestrator/ast_node_splicer.py`)
Rather than relying on blind string replacement or destructive `ast.unparse()` code regeneration, `letitloop` parses Python AST structures and splices replacement function nodes directly into the source span. Comments, docstrings, and decorator sets remain intact.

### 2. Write-Ahead Log (WAL) Supervisor (`orchestrator/micro_epoch.py`)
Every phase transition, model prompt, tool execution receipt, and file mutation is committed to an atomic Write-Ahead Log (`state.wal.jsonl`). If the process crashes at Step 8, re-launching `letitloop` reads the WAL journal and resumes Step 8 immediately without repeating completed work.

### 3. Deterministic 5-Pillar Verification Firewall (`orchestrator/fast_sandbox.py`)
Before any code change touches the host disk:
1. **AST Syntax Validation**: Verifies code parses cleanly.
2. **Signature Invariant Check**: Proves function arguments, defaults, and decorators match interface contracts.
3. **Scope Boundary Fence**: Blocks unapproved edits outside the declared file boundary.
4. **In-Memory Sandbox Overlay**: Runs unit tests against modified code in memory using `sys.modules` pre-injection.
5. **Subprocess Exit Code Assertion**: Verifies that tests exit cleanly with code `0`.

---

## Quickstart

### Installation

```bash
# Clone repository
git clone https://github.com/sdageltc/letitloop.git
cd letitloop

# Install package and CLI
pip install -e .
```

### Basic CLI Usage

```bash
# 1. Propose an execution contract DAG from an objective
lil propose "Add rate limiter middleware to FastAPI app"

# 2. Run execution loop with deterministic verification gates
lil run --strict

# 3. View WAL journal state and active checkpoints
lil status
```

---

## Ecosystem Repositories

- **[agent-durability-bench](https://github.com/sdageltc/agent-durability-bench)**: The open crash-resilience benchmark (DCP-1.0) for measuring agent recovery fidelity.
- **[letitloop-action](https://github.com/sdageltc/letitloop-action)**: Drop-in GitHub Action attaching proof receipts to Pull Requests.

---

## License

MIT License. Copyright (c) 2026 Oguzhan Kayan.
