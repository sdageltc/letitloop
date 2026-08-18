# ADR-0004: Format-Aware Acceptance Check & Markdown Injection

**Date**: 2026-08-18  
**Status**: `accepted`  
**Deciders**: Lead Architecture Team (`sdageltc`)  

---

## Context

The Quality Plane and supervisor automatically inject structural acceptance checks (such as `required_sections` from the quality specification) to ensure thorough deliverables. However, when applied indiscriminately to all outputs, code files (`.py`, `.ts`, `.rs`, `.go`) fail verification because source code files do not contain markdown heading headers (`# Architecture`, `# Overview`).

---

## Decision

The supervisor's auto-check injection in `orchestrator/supervisor.py` is made strictly **format-aware**:
1. `required_sections` checks are only injected for documentation, markdown, and plain-text outputs (`.md`, `.markdown`, `.txt`, `.rst`).
2. Code files (`.py`, `.ts`, `.js`, `.json`, `.yaml`, etc.) bypass heading checks and are verified exclusively through AST syntax parsing, type contracts, deterministic regex assertions, and test execution exit codes.

---

## Consequences

### Positive
- Code generation contracts succeed without false verification rejections.
- Markdown reports maintain strict section requirements without polluting code files.
