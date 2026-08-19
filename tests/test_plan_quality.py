"""Tests for plan quality validation."""

from orchestrator.goal import Plan
from orchestrator.plan_quality import (
    PlanQualityWarning,
    check_plan_quality,
    format_warnings,
    plan_is_safe,
)


def test_empty_plan_no_warnings():
    plan = Plan(goal_id="test", contracts=[])
    warnings = check_plan_quality(plan)
    assert len(warnings) == 0


def test_good_contract_passes():
    contracts = [
        {
            "task_id": "good-task",
            "depends_on": [],
            "contract": {
                "task_id": "good-task",
                "title": "Test",
                "status": "DRAFTED",
                "risk_tier": "auto",
                "workspace_scope": {"allow": ["scratch/"], "deny": []},
                "objective": "Create test output file",
                "worker": {"model": "fake", "max_attempts": 1},
                "inputs": [],
                "outputs": [{"path": "scratch/test.txt"}],
                "acceptance_checks": [
                    {"id": "c1", "kind": "file_exists", "path": "scratch/test.txt", "expected": True}
                ],
                "qc": {"required": False, "lens": "code_correctness"},
            },
        }
    ]
    plan = Plan(goal_id="test", contracts=contracts)
    warnings = check_plan_quality(plan)
    assert plan_is_safe(warnings)


def test_missing_outputs_is_error():
    contracts = [
        {
            "task_id": "no-out",
            "depends_on": [],
            "contract": {
                "task_id": "no-out",
                "title": "Test",
                "status": "DRAFTED",
                "risk_tier": "auto",
                "workspace_scope": {"allow": ["scratch/"], "deny": []},
                "objective": "test",
                "worker": {"model": "fake", "max_attempts": 1},
                "inputs": [],
                "outputs": [],
                "acceptance_checks": [],
                "qc": {"required": False, "lens": "code_correctness"},
            },
        }
    ]
    plan = Plan(goal_id="test", contracts=contracts)
    warnings = check_plan_quality(plan)
    assert not plan_is_safe(warnings)
    assert any("no outputs" in w.message for w in warnings if w.severity == "error")


def test_duplicate_task_ids():
    contracts = [
        {"task_id": "dup", "depends_on": [], "contract": {"task_id": "dup", "outputs": [{"path": "a.txt"}]}},
        {"task_id": "dup", "depends_on": [], "contract": {"task_id": "dup", "outputs": [{"path": "b.txt"}]}},
    ]
    plan = Plan(goal_id="test", contracts=contracts)
    warnings = check_plan_quality(plan)
    assert not plan_is_safe(warnings)
    assert any("duplicate" in w.message for w in warnings)


def test_missing_dependency():
    contracts = [
        {
            "task_id": "task-a",
            "depends_on": ["task-b"],
            "contract": {"task_id": "task-a", "outputs": [{"path": "a.txt"}]},
        },
    ]
    plan = Plan(goal_id="test", contracts=contracts)
    warnings = check_plan_quality(plan)
    assert not plan_is_safe(warnings)
    assert any("dependency" in w.message and "task-b" in w.message for w in warnings)


def test_permissive_check_warned():
    contracts = [
        {
            "task_id": "perm",
            "depends_on": [],
            "contract": {
                "task_id": "perm",
                "outputs": [{"path": "scratch/x.txt"}],
                "acceptance_checks": [{"id": "c1", "kind": "content_regex", "path": "scratch/x.txt", "expected": ".*"}],
                "workspace_scope": {"allow": ["scratch/"], "deny": []},
            },
        }
    ]
    plan = Plan(goal_id="test", contracts=contracts)
    warnings = check_plan_quality(plan)
    assert any("permissive" in w.message.lower() for w in warnings)


def test_output_outside_scope():
    contracts = [
        {
            "task_id": "bad-scope",
            "depends_on": [],
            "contract": {
                "task_id": "bad-scope",
                "outputs": [{"path": "forbidden/out.txt"}],
                "workspace_scope": {"allow": ["scratch/"], "deny": []},
            },
        }
    ]
    plan = Plan(goal_id="test", contracts=contracts)
    warnings = check_plan_quality(plan)
    assert not plan_is_safe(warnings)


def test_format_warnings_empty():
    assert "no issues" in format_warnings([])


def test_format_warnings_nonempty():
    ws = [PlanQualityWarning("test warning", severity="error", task_id="t1")]
    out = format_warnings(ws)
    assert "[ERROR]" in out
    assert "t1" in out
    assert "test warning" in out


class TestPlanQualityExtended:
    """Phase 4: Extended plan-quality validation rules."""

    def _make_contract(self, overrides=None):
        base = {
            "task_id": "ext-test",
            "depends_on": [],
            "contract": {
                "task_id": "ext-test",
                "title": "Extended quality test",
                "status": "DRAFTED",
                "risk_tier": "auto",
                "workspace_scope": {"allow": ["scratch/"], "deny": []},
                "objective": "Test extended checks",
                "worker": {"model": "fake", "max_attempts": 1},
                "inputs": [],
                "outputs": [{"path": "scratch/out.txt"}],
                "acceptance_checks": [{"id": "c1", "kind": "file_exists", "path": "scratch/out.txt"}],
                "qc": {"required": False, "lens": "code_correctness"},
            },
        }
        if overrides:
            base["contract"].update(overrides)
        return base

    def test_empty_checks_on_non_scratch_is_error(self):
        c = self._make_contract({"outputs": [{"path": "src/main.py"}], "acceptance_checks": []})
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert not plan_is_safe(warnings)
        assert any("no acceptance checks" in w.message for w in warnings)

    def test_check_path_mismatch_is_warning(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "content_regex", "path": "scratch/wrong.txt", "expected": ".+"},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert any("does not match any output" in w.message for w in warnings)

    def test_min_size_zero_is_error(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "min_size", "path": "scratch/out.txt", "expected": 0},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert not plan_is_safe(warnings)
        assert any("min_size expected=0" in w.message for w in warnings)

    def test_min_size_negative_is_error(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "min_size", "path": "scratch/out.txt", "expected": -5},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert not plan_is_safe(warnings)

    def test_empty_command_is_error(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "command"},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert any("empty command" in w.message for w in warnings)

    def test_syntax_unknown_extension_warns(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "syntax", "path": "scratch/out.txt"},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert any("unrecognized extension" in w.message for w in warnings)

    def test_syntax_known_extension_ok(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "syntax", "path": "scratch/out.py"},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        syntax_warning = [w for w in warnings if "syntax" in w.message]
        assert len(syntax_warning) == 0

    def test_required_sections_empty_is_warning(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "required_sections", "path": "scratch/out.txt", "expected": []},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert any("empty section list" in w.message for w in warnings)

    def test_render_unsupported_format_warns(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "render", "path": "scratch/out.txt", "expected": "pdf"},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert any("unsupported format" in w.message for w in warnings)

    def test_render_markdown_ok(self):
        c = self._make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "render", "path": "scratch/out.md", "expected": "markdown"},
                ]
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        render_warning = [w for w in warnings if "render" in w.message]
        assert len(render_warning) == 0

    def test_quality_spec_minimum_score_out_of_range_is_error(self):
        c = self._make_contract({"quality_spec": {"minimum_score": 42}})
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert not plan_is_safe(warnings)
        assert any("outside valid range" in w.message for w in warnings)

    def test_quality_dimensions_zero_weight_warns(self):
        c = self._make_contract(
            {
                "quality_spec": {
                    "quality_dimensions": {"correctness": 0},
                }
            }
        )
        plan = Plan(goal_id="test", contracts=[c])
        warnings = check_plan_quality(plan)
        assert any("weights sum to zero" in w.message for w in warnings)
