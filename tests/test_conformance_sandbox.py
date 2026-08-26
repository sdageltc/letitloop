import pytest


def test_target_path_traversal_rejected(tmp_path):
    from letitloop.conformance.harness.schema import SyntheticStep, SyntheticTaskSpec
    from letitloop.conformance.harness.synthetic_engine import SyntheticTaskRunner

    spec = SyntheticTaskSpec(
        task_id="evil",
        steps=[
            SyntheticStep(
                step_id="s1",
                action_type="FILE_WRITE",
                target_path="../../etc/passwd",
                expected_content="x",
                simulated_token_cost=10,
            )
        ],
        kill_at_step_index=-1,
    )
    runner = SyntheticTaskRunner(spec, wal_dir=str(tmp_path))
    with pytest.raises(ValueError, match="sandbox"):
        runner.run_until_kill_or_complete()


def test_scenario_whitelist_rejects_unknown():
    from letitloop.conformance.harness.runner import _load_scenario_json

    with pytest.raises((FileNotFoundError, ValueError)):
        _load_scenario_json("DCP-999")


def test_scenario_path_traversal_rejected():
    from letitloop.conformance.harness.runner import _load_scenario_json

    with pytest.raises(ValueError, match="sandbox|traversal"):
        _load_scenario_json("../etc/passwd")


def test_valid_scenario_still_loads():
    from letitloop.conformance.harness.runner import _load_scenario_json

    data = _load_scenario_json("DCP-002")
    assert data["id"].startswith("DCP-002")
