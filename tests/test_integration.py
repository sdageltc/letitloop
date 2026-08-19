"""Integration tests for orchestrator end-to-end flows.

These tests simulate the full control loop without invoking opencode.
"""

import os
import shutil
import tempfile

import pytest

from orchestrator.contract import Contract
from orchestrator.handoff import build_handoff
from orchestrator.preflight import run_preflight
from orchestrator.state import (
    IllegalTransitionError,
    create_initial_state,
    load_state,
    save_state,
)
from orchestrator.verifier import run_verification

pytestmark = pytest.mark.fast

FIXTURE_CONTRACT = {
    "task_id": "integration-test-hello",
    "title": "Integration test: create hello_fixture.py",
    "status": "drafted",
    "risk_tier": "auto",
    "workspace_scope": {"allow": ["scratch/test/"], "deny": ["AGENTS.md", "memory/", ".opencode/"]},
    "objective": "Create scratch/test/hello_fixture.py with a greet function",
    "worker": {"model": "openai:gpt-4o-mini", "max_attempts": 3},
    "inputs": [],
    "outputs": [{"path": "scratch/test/hello_fixture.py"}],
    "acceptance_checks": [
        {"id": "file_exists", "kind": "file_exists", "path": "scratch/test/hello_fixture.py", "expected": "nonempty"},
        {
            "id": "greet_function",
            "kind": "content_regex",
            "path": "scratch/test/hello_fixture.py",
            "expected": r"def greet",
        },
        {
            "id": "pytest_pass",
            "kind": "command",
            "command": "python -m pytest scratch/test/hello_fixture.py -v",
            "expected": 0,
        },
    ],
    "qc": {"required": False, "lens": "code_correctness"},
}


@pytest.fixture
def isolated_workspace():
    td = tempfile.mkdtemp()
    os.makedirs(os.path.join(td, "scratch", "test"), exist_ok=True)
    os.makedirs(os.path.join(td, "scratch", "orchestrator_runs"), exist_ok=True)
    yield td
    shutil.rmtree(td)


def test_simulated_happy_path(isolated_workspace):
    """Simulate a full happy path: create -> preflight -> work -> verify -> complete.

    We write the fixture output manually to simulate the worker.
    """
    ws = isolated_workspace
    contract = Contract(FIXTURE_CONTRACT)
    run_dir = os.path.join(ws, "scratch", "orchestrator_runs", "integration-test-hello")
    state = create_initial_state("integration-test-hello")
    state.data["contract_path"] = "mock"

    # PREFLIGHT
    passed, results, evidence = run_preflight(contract, ws, run_dir)
    state.status = "PREFLIGHT_RUNNING"
    state.add_evidence("preflight", evidence or "")
    state.status = "READY"

    # WORK (simulated - write the file manually)
    output_dir = os.path.join(ws, "scratch", "test")
    os.makedirs(output_dir, exist_ok=True)
    fixture_path = os.path.join(output_dir, "hello_fixture.py")
    with open(fixture_path, "w") as f:
        f.write(
            "def greet(name):\n    return f'Hello, {name}!'\n\n\ndef test_greet():\n    assert greet('World') == 'Hello, World!'\n"
        )

    state.status = "WORKING"
    state.add_worker_result({"exit_code": 0, "stdout": "ok", "stderr": "", "elapsed_sec": 0.5})
    state.status = "VERIFYING"

    # VERIFY
    all_passed, results, evidence = run_verification(contract, ws, run_dir)
    state.add_evidence("verification", evidence or "")
    assert all_passed, f"Verification failed: {[r for r in results if not r.passed]}"
    state.status = "COMPLETE"

    # Handoff
    handoff = build_handoff(state, run_dir)
    assert handoff["status"] == "COMPLETE"
    assert handoff["outcome_classification"] == "lesson_candidate"


def test_verifier_rejects_bad_output(isolated_workspace):
    """Worker produces wrong content; verifier must reject."""
    ws = isolated_workspace
    contract = Contract(FIXTURE_CONTRACT)
    run_dir = os.path.join(ws, "scratch", "orchestrator_runs", "integration-test-reject")

    output_dir = os.path.join(ws, "scratch", "test")
    os.makedirs(output_dir, exist_ok=True)
    fixture_path = os.path.join(output_dir, "hello_fixture.py")
    with open(fixture_path, "w") as f:
        f.write("# wrong content - no greet function\n")

    all_passed, results, _ = run_verification(contract, ws, run_dir)
    assert not all_passed, "Should reject missing greet function"

    greet_results = [r for r in results if not r.passed and "greet" in r.message]
    assert len(greet_results) > 0


def test_three_strike_impossibility(isolated_workspace):
    """Simulate three failed attempts, then ESCALATED, no fourth retry."""
    Contract(FIXTURE_CONTRACT)
    state = create_initial_state("integration-test-3strike")

    for attempt in range(1, 4):
        state.attempt = attempt
        state.status = "WORKING"
        state.add_worker_result(
            {"exit_code": 1, "stdout": "", "stderr": f"attempt {attempt} failed", "elapsed_sec": 0.1}
        )
        state.record_approach(f"approach {attempt}")
        state.status = "VERIFICATION_FAILED"
        state.status = "RETRY_PENDING"
        if attempt < 3:
            state.increment_attempt()

    if state.attempt >= 3:
        state.transition("ESCALATED", reason="impossibility: max retries exhausted")

    assert state.status == "ESCALATED"
    assert state.is_terminal()

    handoff = build_handoff(state, run_dir=None)
    assert handoff["outcome_classification"] == "skill_candidate"
    assert "impossibility" in handoff["blocker"].lower() or "escalated" in handoff["blocker"].lower()


def test_resume_from_state(isolated_workspace):
    """Save state to disk, reload, confirm legal next action."""
    ws = isolated_workspace
    state = create_initial_state("resume-test")
    state.status = "READY"
    state_path = os.path.join(ws, "state.json")
    save_state(state, state_path)

    loaded = load_state(state_path)
    assert loaded.task_id == "resume-test"
    assert loaded.status == "READY"
    assert "WORKING" in loaded.legal_transitions()

    handoff = build_handoff(loaded, run_dir=None)
    assert "WORKING" in handoff["next_legal_actions"]


def test_contract_scope_enforcement(isolated_workspace):
    """Contract with output outside allow-list fails preflight."""
    ws = isolated_workspace
    bad_contract_dict = dict(FIXTURE_CONTRACT)
    bad_contract_dict["outputs"] = [{"path": "AGENTS.md"}]
    contract = Contract(bad_contract_dict)

    passed, results, _ = run_preflight(contract, ws, os.path.join(ws, "runs"))
    assert not passed
    allow_fails = [r for r in results if not r["passed"] and r["kind"] == "output_allow_list"]
    assert len(allow_fails) > 0


def test_illegal_state_transition_across_loop(isolated_workspace):
    """Ensure no shortcut transitions are possible."""
    s = create_initial_state("illegal")
    with pytest.raises(IllegalTransitionError):
        s.transition("COMPLETE")
    with pytest.raises(IllegalTransitionError):
        s.transition("WORKING")
    with pytest.raises(IllegalTransitionError):
        s.transition("BLOCKED")

    s.status = "READY"
    with pytest.raises(IllegalTransitionError):
        s.transition("COMPLETE")


def test_worker_with_retry_and_changed_approach(isolated_workspace):
    """Worker result records approach changes across attempts."""
    Contract(FIXTURE_CONTRACT)
    state = create_initial_state("approach-test")
    state.status = "RETRY_PENDING"

    state.record_approach("first: write a simple script")
    state.increment_attempt()
    state.status = "WORKING"
    state.add_worker_result({"exit_code": 1, "stderr": "fail", "elapsed_sec": 0.1})
    state.status = "VERIFICATION_FAILED"
    state.status = "RETRY_PENDING"

    state.record_approach("second: use pytest directly")
    state.increment_attempt()
    assert len(state.changed_approaches) == 2
    assert state.attempt == 3
