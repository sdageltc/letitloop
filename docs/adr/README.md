# Architecture Decision Records (ADRs)

This directory maintains the living Architecture Decision Records for `letitloop`.
Following the Michael Nygard format, every record captures the context, decision, rejected alternatives, and consequences of significant technical choices.

## Status Conventions
- `proposed`: Decision under review or exploration.
- `accepted`: Decision committed and actively enforced in the codebase.
- `superseded`: Replaced by a newer decision (always links the replacement).
- `deprecated`: No longer active or applicable.

---

## ADR Index

| ADR | Title | Status | Date | Core Focus |
|---|---|---|---|---|
| [**0001**](0001-write-ahead-logging.md) | Write-Ahead Logging (WAL) & Zero-State Recovery | `accepted` | 2026-08-15 | Atomic state machine, fault barrier, and crash resumption |
| [**0002**](0002-deterministic-verifiers.md) | Deterministic AST, Regex & Exit-Code Verification Gates | `accepted` | 2026-08-16 | Hard boundary checking without probabilistic hallucination |
| [**0003**](0003-headless-cli-adapters.md) | Zero-API-Key Headless Agent CLI Wrapper Failovers | `accepted` | 2026-08-18 | Native execution via `agy`, `claude`, `opencode`, and `hermes` |
| [**0004**](0004-format-aware-acceptance-checks.md) | Format-Aware Acceptance Check & Markdown Injection | `accepted` | 2026-08-18 | Selective heading verification for markdown vs source code |
