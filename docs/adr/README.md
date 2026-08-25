# Architecture Decision Records (ADRs)

This directory maintains the living Architecture Decision Records for `letitloop`.
Following the Michael Nygard format, every record captures the context, decision, rejected alternatives, and consequences of significant technical choices.

---

## 📜 Living ADR Index

| # | ADR Document | Status | Summary |
|---|---|:---:|---|
| **0001** | [Write-Ahead Logging Architecture](0001-write-ahead-logging.md) | `ACCEPTED` | 2ms append-only WAL checkpointing on atomic step boundaries for crash recovery. |
| **0002** | [Deterministic Verifiers](0002-deterministic-verifiers.md) | `ACCEPTED` | Fail-closed acceptance check kinds with zero reliance on probabilistic judge LLMs. |
| **0003** | [Headless CLI Adapters](0003-headless-cli-adapters.md) | `ACCEPTED` | Subprocess isolation protocols and process-tree lifecycle management. |
| **0004** | [Format-Aware Acceptance Checks](0004-format-aware-acceptance-checks.md) | `ACCEPTED` | Typed payload validators for JSON, regex, min size, and exit code receipts. |
| **0005** | [Reposition to Verification Harness](0005-reposition-to-verification-harness.md) | `ACCEPTED` | Pivot from general orchestrator to proof-carrying verification gate. |
| **0006** | [Vertical Beachhead: CVE Remediation](0006-vertical-beachhead-cve-remediation.md) | `ACCEPTED` | Security-first beachhead addressing EU Cyber Resilience Act (CRA) compliance. |
| **0007** | [Benchmark-First Credibility](0007-benchmark-first-credibility.md) | `ACCEPTED` | Open-source `agent-durability-bench` (DCP-1.0) for independent empirical authority. |
| **0008** | [Scope Freeze & Sunset Criteria](0008-scope-freeze-and-sunset-criteria.md) | `ACCEPTED` | 6-month pre-committed milestone gates and transparent sunset criteria. |

---

## 📝 Creating a New ADR

To propose a new architecture decision, copy [`template.md`](template.md) to `NNNN-short-title.md` and submit a Pull Request.