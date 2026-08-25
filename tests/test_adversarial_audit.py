"""Tests for adversarial architecture audit profile."""

import copy
import os

import pytest

from orchestrator.contract import requires_semantic_qc, validate_contract
from orchestrator.qc_review import QCVerdict
from orchestrator.verifier import run_checks

pytestmark = pytest.mark.fast


ADVERSARIAL_CONTRACT = {
    "task_id": "audit-001",
    "title": "Architecture Audit: test system",
    "status": "drafted",
    "risk_tier": "qc_required",
    "workspace_scope": {"allow": ["scratch/"], "deny": []},
    "objective": "Audit the test system architecture",
    "worker": {
        "model": "gemini:gemini-3.6-flash",
        "max_attempts": 2,
        "quality_profile": "adversarial_architecture_audit",
    },
    "inputs": [],
    "outputs": [{"path": "scratch/audit/report.md"}],
    "acceptance_checks": [
        {"id": "content", "kind": "content_regex", "path": "scratch/audit/report.md", "expected": ".+"},
        {"id": "contradictions", "kind": "contradiction_count", "path": "scratch/audit/report.md", "expected": 5},
        {"id": "edge_cases", "kind": "edge_case_count", "path": "scratch/audit/report.md", "expected": 20},
        {"id": "schemas", "kind": "schema_count", "path": "scratch/audit/report.md", "expected": 3},
    ],
    "quality_spec": {
        "required_sections": [
            "Executive Verdict",
            "Critical Contradictions",
            "Uncomfortable Truths",
            "Failure Modes & Edge Cases",
            "Concrete Schemas & Artifacts",
        ],
        "quality_dimensions": {"analytical_depth": 0.25, "contradiction_resolution": 0.15, "actionability": 0.20},
        "hard_failures": ["no_contradictions", "no_edge_cases"],
        "minimum_score": 0.85,
        "minimum_counts": {
            "contradictions": 5,
            "edge_cases": 20,
            "test_specs": 10,
            "schemas": 3,
            "radical_alternatives": 1,
        },
    },
    "qc": {"required": True, "lens": "architecture_audit"},
}


class TestContractValidation:
    def test_adversarial_audit_contract_valid(self):
        errors = validate_contract(ADVERSARIAL_CONTRACT, workspace_root="/tmp")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_minimum_counts_validates(self):
        raw = copy.deepcopy(ADVERSARIAL_CONTRACT)
        raw["quality_spec"]["minimum_counts"] = "not_a_dict"
        errors = validate_contract(raw)
        assert any("minimum_counts" in e for e in errors)

    def test_architecture_audit_lens_is_valid(self):
        raw = copy.deepcopy(ADVERSARIAL_CONTRACT)
        raw["qc"]["lens"] = "architecture_audit"
        errors = validate_contract(raw, workspace_root="/tmp")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_new_check_kinds_are_valid(self):
        for kind in ("contradiction_count", "edge_case_count", "schema_count"):
            raw = copy.deepcopy(ADVERSARIAL_CONTRACT)
            raw["acceptance_checks"] = [{"id": f"check-{kind}", "kind": kind, "path": "scratch/audit/report.md"}]
            errors = validate_contract(raw, workspace_root="/tmp")
            assert errors == [], f"Expected no errors for kind={kind}, got: {errors}"


class TestRequiresSemanticQc:
    def test_minimum_counts_triggers_qc(self):
        assert (
            requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [], {"minimum_counts": {"contradictions": 5}})
            is True
        )

    def test_architecture_audit_check_kinds_trigger_qc(self):
        for kind in ("contradiction_count", "edge_case_count", "schema_count"):
            assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": kind}]) is True


class TestVerifierCountChecks:
    def test_contradiction_count_passes(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("""
# Architecture Review

## Critical Contradictions

1. **Contradiction 1:** The system claims O(1) memory but uses sqlite-vec.
   This tension between the two design goals creates inconsistency.

2. **Contradiction 2:** The control loop claims no external deps but imports FastAPI.
   The conflict with the stated policy is clear.

3. Another contradiction between speed and safety.

An uncomfortable truth about the design.
""")
        results = run_checks(
            [
                {"id": "c1", "kind": "contradiction_count", "path": file_path, "expected": 3},
            ],
            str(tmp_path),
        )
        assert results[0].passed, f"Expected pass, got: {results[0].message}"

    def test_contradiction_count_fails(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# Summary\nNo contradictions here.\n")
        results = run_checks(
            [
                {"id": "c1", "kind": "contradiction_count", "path": file_path, "expected": 3},
            ],
            str(tmp_path),
        )
        assert not results[0].passed

    def test_edge_case_count_passes(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("""
## Failure Modes

1. **Edge case 1:** Network partition during contract generation.
   If the model call fails mid-generation, state corruption can occur.

2. **Edge case 2:** Race condition in evidence ledger writes.
   If two tasks complete at the same instant, the ledger may lose entries.

3. **Edge case 3:** Corrupt state.json file.
   If the state file is truncated during write, recovery must rebuild from journal.

4. Scenario 4: Disk full during scope snapshot.

5. Failure mode: Timeout during worker execution.

6. Scenario: Undeclared output with same name as declared output.

7. Edge case: Journal file grows unbounded during long-running tasks.

8. Corner case: Multiple force-complete overrides on same task.

9. If the QC model returns malformed JSON, the entire review is lost.

10. If the planner returns zero contracts, the supervisor hangs.

11. Scenario: goal_id collision between concurrent runs.

12. Edge case: _inject_upstream_evidence when evidence_store is empty.

13. If the lock file is stale from a crashed supervisor.

14. Failure: backup directory already exists but is a file.

15. Edge case: state file has valid JSON but missing required fields.

16. If the user cancels mid-QC, the QC verdict is missing.

17. Scenario: contract validation passes but generated contract has different risk_tier.

18. Edge case: scope snapshot pre-dates current workspace changes.

19. If the retry count overflows integer.

20. Corner case: metrics file is empty JSON object.

21. Another edge case for good measure.
""")
        results = run_checks(
            [
                {"id": "e1", "kind": "edge_case_count", "path": file_path, "expected": 5},
            ],
            str(tmp_path),
        )
        assert results[0].passed, f"Expected pass, got: {results[0].message}"

    def test_edge_case_count_fails(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# Summary\nOnly one edge case here.\n")
        results = run_checks(
            [
                {"id": "e1", "kind": "edge_case_count", "path": file_path, "expected": 5},
            ],
            str(tmp_path),
        )
        assert not results[0].passed

    def test_schema_count_passes(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("""
## JSON Schemas

```json
{"contract": {"task_id": "string", "risk_level": "enum"}}
```

```json
{"pipeline": {"status": "string", "dependencies": "array"}}
```

## Risk Matrix

| Tier | Auto-approve | Review Required |
|---|---|---|
| LOW | Yes | No |
| MEDIUM | No | Yes |
| HIGH | No | Yes |

## Failure Classification

| Type | Description |
|---|---|
| TIMEOUT | Worker exceeded timeout |
| CRASH | Unexpected exception |

## Deployment Checklist

```yaml
steps:
  - verify state file exists
  - check evidence ledger integrity
```
""")
        results = run_checks(
            [
                {"id": "s1", "kind": "schema_count", "path": file_path, "expected": 3},
            ],
            str(tmp_path),
        )
        assert results[0].passed, f"Expected pass, got: {results[0].message}"

    def test_schema_count_fails(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# No schemas here\n")
        results = run_checks(
            [
                {"id": "s1", "kind": "schema_count", "path": file_path, "expected": 3},
            ],
            str(tmp_path),
        )
        assert not results[0].passed


class TestQcVerdictDimensions:
    def test_dimension_scores_stored(self):
        v = QCVerdict(
            passed=True,
            reason="good audit",
            status="PASS",
            score=0.9,
            dimension_scores={
                "originality": 0.8,
                "contradiction_resolution": 0.9,
                "concrete_artifacts": 0.7,
                "edge_case_coverage": 0.85,
                "intellectual_courage": 0.9,
                "actionability": 0.8,
                "source_fidelity": 0.95,
            },
            dimension_reasoning={
                "originality": "Some original analysis beyond source",
                "contradiction_resolution": "Identified 6 contradictions",
            },
        )
        d = v.to_dict()
        assert d["dimension_scores"]["originality"] == 0.8
        assert d["dimension_reasoning"]["originality"] != ""

    def test_empty_dimensions_default(self):
        v = QCVerdict(passed=True, reason="test", status="PASS", score=0.9)
        d = v.to_dict()
        assert d["dimension_scores"] == {}
        assert d["dimension_reasoning"] == {}


class TestWorkerBriefAdversarial:
    def test_adversarial_brief_contains_mode_instructions(self):
        from orchestrator.contract import Contract
        from orchestrator.worker import _build_brief

        raw = copy.deepcopy(ADVERSARIAL_CONTRACT)
        contract = Contract(raw)
        brief = _build_brief(contract, previous_failures=None)
        assert "ADVERSARIAL ARCHITECTURE AUDIT MODE" in brief
        assert "SENIOR SYSTEMS ARCHITECT" in brief
        assert "Uncomfortable Truths" in brief
        assert "radically simpler alternative" in brief

    def test_non_adversarial_contract_no_extra_instructions(self):
        from orchestrator.contract import Contract
        from orchestrator.worker import _build_brief

        raw = {
            "task_id": "simple",
            "title": "Write a file",
            "status": "drafted",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "objective": "Write a simple file",
            "worker": {"model": "openai:gpt-4o-mini", "max_attempts": 1},
            "inputs": [],
            "outputs": [{"path": "scratch/simple.txt"}],
            "acceptance_checks": [
                {"id": "c1", "kind": "content_regex", "path": "scratch/simple.txt", "expected": ".+"}
            ],
            "qc": {"required": False, "lens": "code_correctness"},
        }
        contract = Contract(raw)
        brief = _build_brief(contract, previous_failures=None)
        assert "ADVERSARIAL ARCHITECTURE AUDIT MODE" not in brief
