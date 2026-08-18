---
name: contract-authoring
description: Guide and validator for authoring deterministic contract JSON specifications, invariant test commands, workspace scope boundaries, and risk tiers in letitloop.
metadata:
  author: letitloop-maintainers
  version: "1.0.0"
compatibility: Cross-platform (JSON schema v1)
---

# Contract Authoring Specification for letitloop

Contracts are the atomic unit of execution in `letitloop`. Each contract represents a single, verifiable task with bounded scope, deterministic exit-code verifiers, and independent quality gates.

## Contract JSON Schema

```json
{
  "task_id": "unique_contract_id",
  "goal_id": "parent_goal_id",
  "title": "Clear concise contract title",
  "description": "Exhaustive task specification describing exact required changes",
  "risk_tier": "TIER_1_TRIVIAL | TIER_2_STANDARD | TIER_3_CRUCIBLE",
  "depends_on": [],
  "workspace_scope": {
    "allow": ["src/module_a/", "tests/test_module_a.py"],
    "deny": ["src/auth/", "config/", "state.json"]
  },
  "acceptance_criteria": [
    "Feature X works as specified",
    "All unit tests pass with zero regression"
  ],
  "verification": {
    "command": "python -m pytest tests/test_module_a.py -v",
    "timeout_sec": 60,
    "require_clean_git": false
  },
  "worker": {
    "model": "gemini-2.5-flash",
    "adapter": "default",
    "max_attempts": 3,
    "timeout_sec": 300
  }
}
```

## Best Practices for Deterministic Verification

1. **Deterministic Test Commands**:
   - Always provide a physical verification command (`verification.command`) that returns exit code `0` on success and non-zero on failure.
   - Avoid interactive commands (`input()`, raw terminal pagers).
2. **Strict Workspace Scopes**:
   - Keep `workspace_scope.allow` as minimal as possible to prevent workers from mutating unrelated files.
   - Always add sensitive files (`.env`, secrets, core infrastructure) to `workspace_scope.deny`.
3. **Bounded Risk Tiers**:
   - `TIER_1_TRIVIAL`: Single-line edits, doc formatting, minor comments (fast LLM routing).
   - `TIER_2_STANDARD`: Feature coding, bugfixes, unit tests.
   - `TIER_3_CRUCIBLE`: Architecture changes, auth, database schemas (triggers multi-agent QC audit).
