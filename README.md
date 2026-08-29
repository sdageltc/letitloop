<p align="center">
  <img src="assets/logo.png" alt="let it loop (LIL)" width="320" style="border-radius: 20px;">
</p>

<div align="center">

# let it loop (LIL)

**Make any Python function or AI agent workflow crash-proof in 3 lines. Zero tokens wasted on SIGKILL.**

[![Official Website](https://img.shields.io/badge/Website-LetItLoop-0284c7?logo=googlechrome&logoColor=white)](https://sdageltc.github.io/letitloop/)
[![PyPI version](https://img.shields.io/pypi/v/letitloop.svg?color=blue)](https://pypi.org/project/letitloop/)
[![CI Matrix](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sdageltc/letitloop/actions/workflows/ci.yml)
[![GitHub Action v2](https://img.shields.io/badge/Action-LetItLoop_v2-blue?logo=github)](https://github.com/sdageltc/letitloop-action)
[![Benchmark](https://img.shields.io/badge/Benchmark-DCP--2.0-blue)](https://sdageltc.github.io/agent-durability-bench/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**[Official Website](https://sdageltc.github.io/letitloop/)** • **[DCP-2.0 Benchmark](https://sdageltc.github.io/agent-durability-bench/)** • **[GitHub Action v2](https://github.com/sdageltc/letitloop-action)** • **[PyPI Package](https://pypi.org/project/letitloop/)**

</div>

---

> **Temporal is great if you have a DevOps team to manage a cluster. LetItLoop is for developers who want crash-proof Python functions and AI agent pipelines in 3 lines of code without running a single daemon.**

---

## ⚡ Quickstart

```python
from letitloop import durable, step, atomic_marker


@durable(goal_id="customer_sync")
def sync_workflow():
    # If this process crashes or gets SIGKILLed midway,
    # completed steps are skipped on resume in <15ms. Zero duplicate tokens wasted.
    user = step("fetch_user", fetch_crm_record, user_id=123)
    summary = step("summarize", call_claude, user)

    # Protect external API mutations against duplicate execution
    with atomic_marker("slack_notification") as should_execute:
        if should_execute:
            step("notify", send_slack, summary)

    return summary


if __name__ == "__main__":
    sync_workflow()
```

```bash
pip install letitloop
```

> **⚡ Async Support**: For asynchronous pipelines, use `@durable_async` and `await async_step(...)` with full `asyncio.gather()` isolation.

---

## 🔄 Process Liveness: Auto-Supervision & CLI Watcher

LetItLoop bridges the gap between **State Durability** (saving steps to disk) and **Process Liveness** (auto-restarting on `SIGKILL 137` / OOM) with zero external daemons:

### 1. Terminal Watcher (`lil watch`)
Run any existing Python script under supervisor control with rapid-failure circuit breaking and clean Ctrl+C handling:

```bash
# Auto-respawns on SIGKILL (137), resuming from last WAL checkpoint in ~14ms
lil watch agent_pipeline.py --max-restarts 10 --backoff 1.0
```

### 2. In-Code Programmatic Supervisor (`@supervise`)
```python
from letitloop import durable, step, supervise


@supervise(max_restarts=5, backoff=1.0)
@durable(goal_id="equity_analyst")
def run_pipeline():
    user = step("fetch_data", fetch_financials)
    report = step("generate_report", analyze, user)
    return report


if __name__ == "__main__":
    run_pipeline()
```

---

## 💎 The 3 Architectural Moats

### 1. Zero-Daemon Local Durability (Zero Infrastructure)
No background Go servers, no Redis queues, and no PostgreSQL cluster configuration. LetItLoop embeds a single-file Write-Ahead Log (`LILWAL02`) that logs step outputs atomically. If your script dies from `SIGKILL (137)`, OOM, or spot eviction, running the script again instantly fast-forwards to the exact interrupted step in **~14ms**.

### 2. Source-Span AST Node Splicer (0% Comment Loss)
Temporal and existing orchestrators only manage task state. LetItLoop includes a surgical Python concrete syntax tree (CST) engine built specifically for self-coding AI agents:
- Replaces targeted functions and classes with surgical precision.
- **0% Comment Loss**: Guarantees module docstrings, inline comments, licensing headers, and class indentation are never stripped or hallucinated away by LLM whole-file rewrites.

### 3. Proof-Carrying CI Gate (`letitloop-action`)
LetItLoop generates signed HMAC-SHA256 receipts recording execution invariants and test outputs. Drop [`letitloop-action@v2`](https://github.com/sdageltc/letitloop-action) into GitHub Actions to block AI pull requests from hallucinating passing test outputs or altering protected function signatures.

---

## 📊 DCP-2.0 Agent Durability Conformance Benchmark

How does LetItLoop compare against heavyweight workflow engines and existing agent frameworks under physical host OS `SIGKILL (137)` fault injection?

Empirical results from the open [DCP-2.0 Durability Benchmark](https://sdageltc.github.io/agent-durability-bench/):

| Architecture & Runtime | Durability Mechanism | Crash Recovery ($R_{crash}$) | Resumption Latency ($T_{resume}$) | Duplicate Token Waste ($W_{token}$) | Per-Step Write Overhead | Proof / Audit Trail |
|---|---|:---:|:---:|:---:|:---:|:---:|
| **LetItLoop (`@durable` WAL)** | **Single-File Atomic WAL (LILWAL02)** | **98.6% PASS** | **14.2 ms** | **2.8%** *(interrupted step)* | **+3.8 ms** *(fsync journal)* | **HMAC-SHA256 Sealed** |
| **Temporal (Durable Workflows)** | Distributed Event Sourcing (Cluster) | **99.2% PASS** | 74.0 ms | 1.9% | +18.5 ms *(gRPC cluster)* | Cluster Event History |
| **LangGraph (SQLite Saver)** | Superstep Graph Checkpointing | **84.5% PARTIAL** | 38.4 ms | 16.8% *(node re-run)* | +1.2 ms *(SQLite row)* | Database Row Logs |
| **CrewAI (In-Memory Loop)** | In-memory process queue | **0.0% LOSS** | N/A *(Full restart)* | 100.0% *(Total wipe)* | 0.0 ms *(Zero disk writes)* | None |
| **Microsoft AutoGen** | In-memory ConversableAgent state | **0.0% LOSS** | N/A *(Full restart)* | 100.0% *(Total wipe)* | 0.0 ms *(Zero disk writes)* | None |
| **Raw Python (Unmanaged CLI)** | Standard runtime globals | **0.0% LOSS** | N/A *(Full restart)* | 100.0% *(Total wipe)* | 0.0 ms *(Zero disk writes)* | None |

> [!NOTE]
> **Methodological Disclosure & Architectural Trade-offs**:
> 1. **Why 100% durability is physically impossible**: If a non-maskable `SIGKILL` strikes while an uncommitted external network request is actively in flight, that single step must be re-executed upon resume, producing an empirical ~1.4%–2.8% token re-execution overhead.
> 2. **The I/O Overhead Trade-off**: LetItLoop trades **~3.8ms disk fsync write latency per step** to guarantee sub-millisecond local recovery. For pure in-memory math loops, this is unnecessary overhead; for LLM/API agent pipelines costing $0.10–$2.00 per step, paying 3.8ms disk I/O to guarantee zero lost progress is an overwhelming net win.

---

## 🔄 Durability vs. Liveness (Auto-Supervision)

- **Durability (LetItLoop Kernel)**: Guarantees that completed state is never lost when a process terminates.
- **Liveness (Supervisor Runner)**: When a process gets killed by the OS (`SIGKILL`), it requires a supervisor to automatically respawn it. LetItLoop provides built-in supervision:

```bash
# Supervise execution and auto-respawn process on unhandled SIGKILL/crash until completion
lil run --task auth-refactor --supervise --strict
```

---

## 🍳 Framework Recipes & Community Cookbooks

Explore runnable self-contained examples in [`examples/`](examples/):

| Framework | Recipe / Cookbook | Status | Description |
|---|---|:---:|---|
| **CrewAI** | [**Durable Tools Example**](examples/crewai_durable_tools.py) | ✅ Ready | Multi-agent tool execution with step-level resumption and zero duplicate side-effects |
| **LlamaIndex** | [**Durable Workflows Example**](examples/llamaindex_durable_workflow.py) | ✅ Ready | Event-driven `@step` pipeline with crash durability and sub-millisecond fast-forward |
| **OpenAI Swarm** | [**Durable Handoff Example**](examples/swarm_durable_handoff.py) | ✅ Ready | Multi-agent context handoff with WAL v2 serialization |
| **LangGraph** | [**Issue #82: Financial Analyst Agent**](https://github.com/sdageltc/letitloop/issues/82) | 🤝 Contributor | 4-step `yfinance` + StateGraph equity analysis surviving simulated SIGKILL |
| **DSPy** | [**Issue #83: Prompt Optimizer Pipeline**](https://github.com/sdageltc/letitloop/issues/83) | 🤝 Contributor | Async `BootstrapFewShot` / Teleprompter tuning with zero lost progress |
| **Playwright** | [**Issue #88: Web Scraping Agent**](https://github.com/sdageltc/letitloop/issues/88) | 🤝 Contributor | Multi-page browser scraper that checkpoints DOM items to skip scraped pages |
| **Pydantic AI** | [**Issue #89: Pydantic AI Integration**](https://github.com/sdageltc/letitloop/issues/89) | 🤝 Contributor | Type-safe agent with tool-calling checkpointing and zero token waste |

---

## 🛡️ GitHub Action CI Gate (v2)

Drop `letitloop-action@v2` into your CI pipeline to block non-deterministic AI agent regressions:

```yaml
name: LetItLoop Proof-Carrying CI Gate
on: [pull_request]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: sdageltc/letitloop-action@v2
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          strict-ast: 'true'
```

---

## 📜 Architecture Decision Records (ADRs)

Core design invariants are documented under [`docs/adr/`](docs/adr/):
- [**ADR-0001**](docs/adr/0001-write-ahead-logging.md): Write-Ahead Logging (WAL) & Zero-State Recovery
- [**ADR-0002**](docs/adr/0002-deterministic-verifiers.md): Deterministic AST & Exit-Code Verification Gates
- [**ADR-0003**](docs/adr/0003-headless-cli-adapters.md): Zero-API-Key Headless Agent CLI Failovers
- [**ADR-0004**](docs/adr/0004-format-aware-acceptance-checks.md): Format-Aware Acceptance Checks & Markdown Invariants

---

## License

Distributed under the MIT License. Copyright (c) 2026 sdageltc. See [LICENSE](LICENSE) for details.
