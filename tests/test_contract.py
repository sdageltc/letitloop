"""Tests for contract validation."""

import copy
import json
import os
import tempfile
import pytest
from orchestrator.contract import validate_contract, load_contract, check_path_allowed, validate_contract_against_plan

pytestmark = pytest.mark.fast

VALID_CONTRACT = {
    "task_id": "test-001",
    "title": "Test contract",
    "status": "drafted",
    "risk_tier": "auto",
    "workspace_scope": {
        "allow": ["scratch/test/"],
        "deny": ["AGENTS.md"]
    },
    "objective": "Create a test file",
    "worker": {
        "model": "openai:gpt-4o-mini",
        "max_attempts": 3
    },
    "inputs": [],
    "outputs": [
        {"path": "scratch/test/output.txt"}
    ],
    "acceptance_checks": [
        {
            "id": "check1",
            "kind": "command",
            "command": "python --version",
            "expected": 0
        }
    ],
    "qc": {
        "required": False,
        "lens": "code_correctness"
    },
    "next_action": "preflight"
}


def test_valid_contract_passes():
    errors = validate_contract(VALID_CONTRACT, workspace_root="/tmp")
    assert errors == [], f"Expected no errors, got: {errors}"


def test_quality_plan_key_accepted():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["quality_plan"] = {"mode": "panel", "lens": "code_correctness"}
    errors = validate_contract(raw, workspace_root="/tmp")
    assert errors == [], f"Expected no errors, got: {errors}"


def test_quality_plan_wrong_type():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["quality_plan"] = ["panel"]
    errors = validate_contract(raw, workspace_root="/tmp")
    assert any("quality_plan" in e for e in errors), f"Expected quality_plan error, got: {errors}"


def test_unknown_top_level_key_still_rejected():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["bogus_key"] = 1
    errors = validate_contract(raw, workspace_root="/tmp")
    assert any("unknown top-level keys" in e for e in errors), f"Expected unknown key error, got: {errors}"


def test_contract_exposes_quality_plan():
    from orchestrator.contract import Contract
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["quality_plan"] = {"mode": "panel"}
    contract = Contract(raw)
    assert contract.quality_plan == {"mode": "panel"}


def test_contract_quality_plan_none_by_default():
    from orchestrator.contract import Contract
    contract = Contract(copy.deepcopy(VALID_CONTRACT))
    assert contract.quality_plan is None


def test_missing_required_field():
    for field in ["task_id", "title", "status", "risk_tier", "objective", "worker", "outputs", "acceptance_checks", "qc"]:
        raw = copy.deepcopy(VALID_CONTRACT)
        del raw[field]
        errors = validate_contract(raw, workspace_root="/tmp")
        assert any(field in e for e in errors), f"Expected error for missing {field}, got: {errors}"


def test_wrong_type_task_id():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["task_id"] = 123
    errors = validate_contract(raw, workspace_root="/tmp")
    assert any("task_id" in e for e in errors)


def test_invalid_status():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["status"] = "invalid_status"
    errors = validate_contract(raw)
    assert any("status" in e for e in errors)


def test_invalid_risk_tier():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["risk_tier"] = "invalid"
    errors = validate_contract(raw)
    assert any("risk_tier" in e for e in errors)


def test_valid_risk_tiers():
    for tier in ("auto", "qc_required", "human_required"):
        raw = copy.deepcopy(VALID_CONTRACT)
        raw["risk_tier"] = tier
        errors = validate_contract(raw, workspace_root="/tmp")
        assert errors == [], f"Expected no errors for risk_tier={tier}, got: {errors}"


def test_invalid_qc_lens():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["qc"] = {"required": True, "lens": "invalid_lens"}
    errors = validate_contract(raw)
    assert any("lens" in e for e in errors)


def test_valid_qc_lenses():
    for lens in ("code_correctness", "plan_correctness", "config_safety"):
        raw = copy.deepcopy(VALID_CONTRACT)
        raw["qc"] = {"required": False, "lens": lens}
        errors = validate_contract(raw, workspace_root="/tmp")
        assert errors == [], f"Expected no errors for lens={lens}, got: {errors}"


def test_missing_acceptance_check_id():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["acceptance_checks"] = [{"kind": "command", "command": "echo hi", "expected": 0}]
    errors = validate_contract(raw)
    assert any("id" in e for e in errors)


def test_invalid_check_kind():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["acceptance_checks"] = [{"id": "x", "kind": "invalid_kind"}]
    errors = validate_contract(raw)
    assert any("kind" in e for e in errors)


def test_valid_check_kinds():
    for kind in ("command", "file_exists", "json_schema", "content_exact", "content_regex"):
        raw = copy.deepcopy(VALID_CONTRACT)
        check = {"id": "test", "kind": kind}
        if kind in ("command", "content_exact", "content_regex"):
            check["expected"] = "something"
        if kind == "command":
            check["command"] = "echo hi"
        raw["acceptance_checks"] = [check]
        errors = validate_contract(raw, workspace_root="/tmp")
        assert errors == [], f"Expected no errors for kind={kind}, got: {errors}"


def test_output_without_path_fails():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["outputs"] = [{"not_path": "foo"}]
    errors = validate_contract(raw)
    assert any("path" in e for e in errors)


def test_unknown_top_level_keys():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["extra_key"] = "should fail"
    errors = validate_contract(raw)
    assert any("extra_key" in e for e in errors)


def test_non_strict_unknown_ok():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["extra_key"] = "should be ignored"
    errors = validate_contract(raw, strict_unknown=False)
    assert errors == []


def test_worker_max_attempts_non_positive():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["worker"]["max_attempts"] = 0
    errors = validate_contract(raw)
    assert any("max_attempts" in e for e in errors)


def test_output_outside_allow_list():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["outputs"] = [{"path": "forbidden/output.txt"}]
    errors = validate_contract(raw, workspace_root="/tmp")
    assert any("output path" in e for e in errors)


def test_output_in_deny_list():
    raw = copy.deepcopy(VALID_CONTRACT)
    raw["outputs"] = [{"path": "AGENTS.md"}]
    errors = validate_contract(raw, workspace_root="/tmp")
    assert any("deny-list" in e for e in errors)


class TestCheckPathAllowed:
    def test_allowed_path_ok(self):
        ok, err = check_path_allowed("scratch/test/foo.txt",
                                     ["scratch/test/"],
                                     [],
                                     "/workspace")
        assert ok
        assert err is None

    def test_path_outside_allow(self):
        ok, err = check_path_allowed("other/foo.txt",
                                     ["scratch/test/"],
                                     [],
                                     "/workspace")
        assert not ok
        assert "not in allow-list" in err

    def test_path_in_deny(self):
        ok, err = check_path_allowed("scratch/test/bar.txt",
                                     ["scratch/test/"],
                                     ["scratch/test/bar.txt"],
                                     "/workspace")
        assert not ok
        assert "deny-list" in err


class TestLoadContract:
    def test_valid_json_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(VALID_CONTRACT, f)
            fname = f.name
        try:
            contract, errors = load_contract(fname, workspace_root="/tmp")
            assert errors == []
            assert contract is not None
            assert contract.task_id == "test-001"
        finally:
            os.unlink(fname)

    def test_invalid_json_syntax(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json}")
            fname = f.name
        try:
            contract, errors = load_contract(fname)
            assert len(errors) > 0
            assert contract is None
        finally:
            os.unlink(fname)

    def test_nonexistent_file(self):
        contract, errors = load_contract("/nonexistent/path.json")
        assert len(errors) > 0
        assert contract is None


class TestValidateContractAgainstPlan:
    """Tests for validate_contract_against_plan — safety field downgrade detection."""

    def make_contract(self, qc_required=True, allow=None, deny=None, checks=2, hard_failures=None, min_score=0.5):
        return {
            "qc": {"required": qc_required},
            "workspace_scope": {
                "allow": allow or ["src/"],
                "deny": deny or ["secrets/"],
            },
            "acceptance_checks": [{"id": f"c{i}"} for i in range(checks)],
            "quality_spec": {
                "hard_failures": hard_failures or [],
                "minimum_score": min_score,
            },
        }

    def test_qc_required_true_survives_generation(self):
        plan = self.make_contract(qc_required=True)
        gen = self.make_contract(qc_required=True)
        errors = validate_contract_against_plan(plan, gen)
        assert errors == [], f"expected no errors, got: {errors}"

    def test_qc_required_downgrade_blocked(self):
        plan = self.make_contract(qc_required=True)
        gen = self.make_contract(qc_required=False)
        errors = validate_contract_against_plan(plan, gen)
        assert any("qc.required" in e for e in errors), f"expected qc.required error, got: {errors}"

    def test_qc_required_upgrade_allowed(self):
        plan = self.make_contract(qc_required=False)
        gen = self.make_contract(qc_required=True)
        errors = validate_contract_against_plan(plan, gen)
        assert errors == [], f"expected no errors, got: {errors}"

    def test_scope_downgrade_blocked(self):
        plan = self.make_contract(allow=["src/", "tests/", "docs/"])
        gen = self.make_contract(allow=["src/"])
        errors = validate_contract_against_plan(plan, gen)
        assert any("workspace_scope.allow" in e for e in errors), f"expected scope error, got: {errors}"

    def test_checks_downgrade_warning(self):
        plan = self.make_contract(checks=4)
        gen = self.make_contract(checks=2)
        errors = validate_contract_against_plan(plan, gen)
        assert any("WARNING" in e and "acceptance_checks" in e for e in errors), f"expected warning, got: {errors}"
