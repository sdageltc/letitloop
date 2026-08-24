# Ecosystem Positioning & Comparison Matrices

---

## 1. Durability Bench (DCP-1.0) vs. Industry Benchmarks

`agent-durability-bench` measures an orthogonal dimension that existing LLM evaluation benchmarks ignore: **crash resilience and step resumption fidelity**.

| Evaluation Dimension | **Durability Bench (DCP-1.0)** | **SWE-bench** | **GAIA** | **AgentBench** |
|---|:---:|:---:|:---:|:---:|
| **Primary Metric** | **Crash Survival & Resumption Fidelity** | Single-Shot Issue Resolution | Multimodal Tool Execution | Multi-Turn LLM Reasoning |
| **Failure Injection** | **Deterministic `SIGKILL`, OOM, Spot Eviction** | None (Runs to completion or timeout) | None | None |
| **State Corruption Check** | **AST Invariants & File Integrity Proofs** | Pytest Exit Code Only | Regex String Match | Environment Reward Score |
| **Cost to Run (100 Tasks)** | **$0.00 (Zero-API Synthetic Harness)** | ~$450.00 (Cloud LLM Tokens) | ~$120.00 (LLM API) | Variable |
| **Runtime Duration** | **< 15 Seconds** | 4–12 Hours | 1–3 Hours | 2–6 Hours |
| **External Dependencies** | **Zero (Pure Python `psutil` + `pytest`)** | Docker Daemon + Seed Repos | Custom Tool APIs | Docker Sandbox |

### Why Durability Matters
When an agent worker crashes at Step 8 of a 10-step task (spot instance eviction, memory limit, unhandled API exception):
- **Fragile Agents**: Re-execute Steps 1–7 from scratch, burning duplicate tokens and risking non-deterministic drift.
- **Durable Agents (DCP-1.0)**: Read state from a Write-Ahead Log, verify existing file checksums, and resume Step 8 in milliseconds.

---

## 2. LetItLoop-Action vs. Standard PR Tooling

`letitloop-action` brings proof-carrying code principles to CI for AI-generated pull requests.

| PR Verification Layer | **Standard CI (GitHub Actions)** | **Linters (Ruff / ESLint)** | **LetItLoop Action (`letitloop-action`)** |
|---|:---:|:---:|:---:|
| **Unit Test Execution** | ✅ Yes | ❌ No | ✅ Yes (Captures stdout & exit code) |
| **AST Signature Drift Guard** | ❌ No (Allows breaking signature changes if tests pass) | ⚠️ Partial (Syntax only) | ✅ **Guarantees zero parameter/decorator drift** |
| **Scope Boundary Lease** | ❌ No (Agent can modify any repo file) | ❌ No | ✅ **Enforces strict file allowlist per task** |
| **Proof Receipt Generation** | ❌ No | ❌ No | ✅ **Emits signed SHA-256 evidence bundle** |
| **Comment Integrity** | ❌ No (Whole-file unparse strips comments) | ❌ No | ✅ **Enforces AST node splicing without comment loss** |

---

## 3. Two-Tier Verification Capability

`letitloop-action` operates on two distinct tiers depending on repository language:

1. **Tier 1 (Universal / Any Language)**:
   - **Scope Boundary Enforcement**: Verifies that git changes are strictly confined to the issue's declared file boundary.
   - **Execution Receipt**: Records process exit codes, execution wall-clock time, and environment metadata in a cryptographic SHA-256 bundle.

2. **Tier 2 (Python 3.10+)**:
   - **AST Node Invariant Check**: Compares abstract syntax trees before and after changes to guarantee that method signatures, decorator sets (`@dataclass`, `@property`, `@override`), and async/sync semantics remain unbroken.
   - **Surgical Patch Verification**: Proves changes were applied via surgical node splicing rather than blind whole-file unparsing.
