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

---

## 📝 Creating a New ADR

To propose a new architecture decision, copy [`template.md`](template.md) to `NNNN-short-title.md` and submit a Pull Request.