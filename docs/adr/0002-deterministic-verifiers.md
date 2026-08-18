# ADR-0002: Deterministic AST, Regex & Exit-Code Verification Gates

**Date**: 2026-08-16  
**Status**: `accepted`  
**Deciders**: Lead Architecture Team (`sdageltc`)  

---

## Context

LLM-as-a-judge patterns suffer from severe non-determinism, sycophancy, and hallucinated evaluations (e.g. LLMs claiming unit tests passed when syntax errors prevent execution). Relying purely on semantic model prompts to verify code output creates false confidence and broken builds.

---

## Decision

All contract verifications in `letitloop` are guarded by **deterministic verifiers** before reaching the semantic Quality Plane:
1. **AST Parsing (`ast.parse`)**: Python outputs are statically checked for syntax trees, function signatures, and class declarations without running untrusted code.
2. **Deterministic Regex & Substring Search**: Output files are verified against exact contract regex patterns.
3. **Execution Exit Code Gates**: Shell command and test suite executions require explicit exit code 0 (`$LASTEXITCODE == 0`) and non-empty output assertions.
4. **Filesystem Scope Snapshotting**: Scope enforcement detects undeclared files created outside the contract workspace boundary.

---

## Alternatives Considered

### Alternative 1: Pure LLM Self-Evaluation
- **Pros**: Easy to implement; handles arbitrary loose descriptions.
- **Cons**: High hallucination rate, high latency, susceptible to prompt injection.
- **Why Rejected**: Fails reliability criteria for unattended macro-tasks.

---

## Consequences

### Positive
- Zero false positives on syntax errors and missing classes.
- Fast execution (<10ms for static verifications vs 3-5s for LLM calls).

### Negative & Trade-offs
- Contract authors must specify unambiguous acceptance criteria (e.g. `content_regex`, `test_command`).
