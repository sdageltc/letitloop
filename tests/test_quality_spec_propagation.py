"""Verify quality_spec propagates through every link in the chain."""

import json
import os

from orchestrator.contract import Contract, load_contract, validate_contract
from orchestrator.prompts import build_critic_prompt, build_implementer_prompt, summarize_acceptance
from orchestrator.qc_review import _build_qc_prompt

DISTINCTIVE_SPEC = {
    "required_sections": ["Security Notes", "Usage"],
    "quality_dimensions": {"correctness": 0.7, "completeness": 0.3},
    "hard_failures": ["contains placeholder TODO", "missing error handling"],
    "minimum_score": 0.85,
}


def _make_minimal_contract_raw(task_id="prop-test-1", quality_spec=None):
    return {
        "task_id": task_id,
        "title": "Propagation Test",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/test-prop/"], "deny": []},
        "objective": "Test quality_spec propagation",
        "worker": {"model": "openai:gpt-4o-mini", "max_attempts": 1},
        "inputs": [],
        "outputs": [{"path": "scratch/test-prop/output.txt"}],
        "acceptance_checks": [
            {"id": "check-1", "kind": "file_exists", "path": "scratch/test-prop/output.txt"},
        ],
        "qc": {"required": True, "lens": "code_correctness"},
        "quality_spec": quality_spec or {},
    }


class TestContractStorage:
    def test_quality_spec_stored_in_contract(self):
        raw = _make_minimal_contract_raw(quality_spec=DISTINCTIVE_SPEC)
        errors = validate_contract(raw)
        assert not errors, f"validation errors: {errors}"
        contract = Contract(raw)
        assert contract.quality_spec == DISTINCTIVE_SPEC

    def test_quality_spec_defaults_to_empty_dict(self):
        raw = _make_minimal_contract_raw()
        del raw["quality_spec"]
        errors = validate_contract(raw)
        assert not errors
        contract = Contract(raw)
        assert contract.quality_spec == {}

    def test_quality_spec_roundtrip(self, tmp_path):
        raw = _make_minimal_contract_raw(quality_spec=DISTINCTIVE_SPEC)
        path = os.path.join(str(tmp_path), "contract.json")
        with open(path, "w") as f:
            json.dump(raw, f)
        loaded, errors = load_contract(path)
        assert not errors
        assert loaded.quality_spec == DISTINCTIVE_SPEC

    def test_malformed_quality_spec_rejected(self):
        bad_specs = [
            "not a dict",
            42,
            {"required_sections": "not a list"},
            {"quality_dimensions": "not a dict"},
            {"hard_failures": "not a list"},
            {"minimum_score": -1},
            {"minimum_score": 1.5},
        ]
        for spec in bad_specs:
            raw = _make_minimal_contract_raw(quality_spec=spec)
            errors = validate_contract(raw)
            assert errors, f"expected validation errors for {spec!r}"


class TestImplementerPrompt:
    def test_implementer_prompt_contains_quality_spec_values(self):
        prompt = build_implementer_prompt(
            title="Test",
            objective="Test propagation",
            output_paths=["scratch/test-prop/output.txt"],
            acceptance_summary=summarize_acceptance(
                [
                    {"kind": "content_regex", "path": "out.txt", "expected": ".+"},
                ]
            ),
            quality_spec=DISTINCTIVE_SPEC,
        )
        assert "Security Notes" in prompt
        assert "Usage" in prompt
        assert "correctness" in prompt
        assert "contains placeholder TODO" in prompt
        assert "0.85" in prompt

    def test_implementer_prompt_omitted_when_empty(self):
        prompt = build_implementer_prompt(
            title="Test",
            objective="Test propagation",
            output_paths=["scratch/test-prop/output.txt"],
            acceptance_summary="[content_regex] out.txt expected=.+",
        )
        assert "QUALITY SPECIFICATION" not in prompt


class TestCriticPrompt:
    def test_critic_prompt_contains_quality_spec_values(self):
        prompt = build_critic_prompt(
            title="Test",
            objective="Test propagation",
            output_paths=["scratch/test-prop/output.txt"],
            acceptance_summary=summarize_acceptance(
                [
                    {"kind": "content_regex", "path": "out.txt", "expected": ".+"},
                ]
            ),
            artifact_summaries=[{"path": "scratch/test-prop/output.txt", "content": "hello world"}],
            quality_spec=DISTINCTIVE_SPEC,
        )
        assert "Security Notes" in prompt
        assert "Usage" in prompt
        assert "correctness" in prompt
        assert "contains placeholder TODO" in prompt
        assert "0.85" in prompt

    def test_critic_prompt_omitted_when_empty(self):
        prompt = build_critic_prompt(
            title="Test",
            objective="Test propagation",
            output_paths=["scratch/test-prop/output.txt"],
            acceptance_summary="[content_regex] out.txt expected=.+",
            artifact_summaries=[{"path": "out.txt", "content": "hello"}],
        )
        assert "QUALITY SPECIFICATION" not in prompt


class TestQcPrompt:
    def test_qc_prompt_contains_quality_spec_values(self):
        class FakeContract:
            task_id = "test"
            objective = "test"
            acceptance_checks = [{"kind": "file_exists", "path": "out.txt"}]
            quality_spec = DISTINCTIVE_SPEC

        prompt = _build_qc_prompt(
            FakeContract(),
            output_paths=["scratch/test-prop/output.txt"],
            verification_results=[{"check_id": "v1", "passed": True, "message": "ok"}],
            quality_spec=DISTINCTIVE_SPEC,
        )
        assert "Security Notes" in prompt
        assert "Usage" in prompt
        assert "correctness" in prompt
        assert "contains placeholder TODO" in prompt
        assert "0.85" in prompt

    def test_qc_prompt_omitted_when_empty(self):
        class FakeContract:
            task_id = "test"
            objective = "test"
            acceptance_checks = [{"kind": "file_exists", "path": "out.txt"}]

        prompt = _build_qc_prompt(
            FakeContract(),
            output_paths=["scratch/test-prop/output.txt"],
            verification_results=[{"check_id": "v1", "passed": True, "message": "ok"}],
        )
        assert "QUALITY SPECIFICATION" not in prompt
