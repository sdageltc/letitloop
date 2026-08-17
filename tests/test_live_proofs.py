"""Live proof end-to-end integration tests for orchestrator lifecycle."""

import os

from orchestrator.audit import query_audit
from orchestrator.checkpoint import (
    list_checkpoints,
    recover_from_checkpoint,
    save_checkpoint,
)
from orchestrator.goal import Goal, Plan
from orchestrator.limits import ResourceLimits
from orchestrator.metrics import MetricsCollector
from orchestrator.safety import format_safety_report, run_safety_checks
from orchestrator.state import State, load_state, save_state
from orchestrator.supervisor import Supervisor
from orchestrator.telemetry import load_events, record_event
from orchestrator.templates import list_templates


def _make_contract(task_id, depends_on=None, output_path=None, max_attempts=1):
    return {
        "task_id": task_id,
        "depends_on": depends_on or [],
        "status": "DRAFTED",
        "contract": {
            "task_id": task_id,
            "title": f"Task {task_id}",
            "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "objective": "live proof end-to-end test",
            "worker": {"model": "test", "max_attempts": max_attempts},
            "inputs": [],
            "outputs": [{"path": output_path or f"scratch/{task_id}_out.txt"}],
            "acceptance_checks": [
                {
                    "id": f"{task_id}-chk",
                    "kind": "file_exists",
                    "path": output_path or f"scratch/{task_id}_out.txt",
                    "expected": True,
                }
            ],
            "qc": {"required": False, "lens": "code_correctness"},
        },
    }


# LIFECYCLE GROUP


def test_live_full_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_full", title="Full Lifecycle Goal", description="3-step end-to-end plan")
    c1 = _make_contract("t1")
    c2 = _make_contract("t2", depends_on=["t1"])
    c3 = _make_contract("t3", depends_on=["t2"])
    plan = Plan(goal_id=goal.goal_id, contracts=[c1, c2, c3])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    res = supervisor.execute_plan()
    assert len(res) == 3
    for tid, status in res.items():
        assert status in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")


def test_live_causal_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_causal", title="Causal Dependency Goal", description="Task B depends on Task A")
    cA = _make_contract("task_A")
    cB = _make_contract("task_B", depends_on=["task_A"])
    plan = Plan(goal_id=goal.goal_id, contracts=[cA, cB])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    assert supervisor.graph.get_ready_tasks() == ["task_A"]
    assert supervisor.graph.get_blocked_tasks() == ["task_B"]

    res = supervisor.execute_plan()
    assert res["task_A"] in ("COMPLETE", "complete")
    assert res["task_B"] in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")


def test_live_evidence_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_evidence", title="Evidence Chain Goal", description="Outputs flow via evidence store")
    out_a = "scratch/task_a_out.txt"
    cA = _make_contract("task_A", output_path=out_a)
    cB = _make_contract("task_B", depends_on=["task_A"])
    plan = Plan(goal_id=goal.goal_id, contracts=[cA, cB])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    supervisor.execute_plan()
    assert "task_A" in supervisor.evidence_store
    a_outputs = supervisor.evidence_store["task_A"]
    assert len(a_outputs) > 0
    assert any("task_a_out.txt" in os.path.basename(p) for p in a_outputs)


# OPERATOR GROUP


def test_live_pause_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_pause", title="Pause Goal", description="Execute plan then pause")
    c1 = _make_contract("t1")
    c2 = _make_contract("t2", depends_on=["t1"])
    plan = Plan(goal_id=goal.goal_id, contracts=[c1, c2])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    for tid in ["t1", "t2"]:
        st_path = supervisor._state_path(tid)
        os.makedirs(os.path.dirname(st_path), exist_ok=True)
        save_state(State(task_id=tid, status="READY"), st_path)

    res = supervisor.pause_plan("operator paused execution")
    assert res.get("t1") == "PAUSED"
    assert res.get("t2") == "PAUSED"
    assert goal.status == "PAUSED"
    assert load_state(supervisor._state_path("t1")).status == "PAUSED"


def test_live_cancel_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_cancel", title="Cancel Goal", description="Execute plan then cancel")
    c1 = _make_contract("t1")
    plan = Plan(goal_id=goal.goal_id, contracts=[c1])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    st_path = supervisor._state_path("t1")
    os.makedirs(os.path.dirname(st_path), exist_ok=True)
    save_state(State(task_id="t1", status="READY"), st_path)

    res = supervisor.cancel_plan("operator cancelled execution")
    assert res.get("t1") == "CANCELLED"
    assert goal.status == "CANCELLED"
    assert load_state(supervisor._state_path("t1")).status == "CANCELLED"


def test_live_inspect_after_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_inspect", title="Inspect Goal", description="Inspect task after execution")
    c1 = _make_contract("t1")
    plan = Plan(goal_id=goal.goal_id, contracts=[c1])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    supervisor.execute_plan()
    info = supervisor.inspect_task("t1")
    assert info["task_id"] == "t1"
    assert info["status"] in ("COMPLETE", "complete")
    assert isinstance(info["evidence_files"], dict)


# OBSERVABILITY GROUP


def test_live_audit_records_pause_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_audit", title="Audit Goal", description="Audit records for pause and cancel")
    c1 = _make_contract("t1")
    plan = Plan(goal_id=goal.goal_id, contracts=[c1])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    st_path = supervisor._state_path("t1")
    os.makedirs(os.path.dirname(st_path), exist_ok=True)
    save_state(State(task_id="t1", status="READY"), st_path)

    supervisor.pause_plan("operator audit pause")
    supervisor.cancel_plan("operator audit cancel")

    pause_entries = query_audit(run_dir, goal_id="g_audit", action_type="pause")
    cancel_entries = query_audit(run_dir, goal_id="g_audit", action_type="cancel")
    assert len(pause_entries) >= 1
    assert len(cancel_entries) >= 1


def test_live_metrics_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_metrics", title="Metrics Goal", description="Supervisor writes metrics.json")
    c1 = _make_contract("t1")
    plan = Plan(goal_id=goal.goal_id, contracts=[c1])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    supervisor.execute_plan()
    metrics_path = os.path.join(run_dir, "metrics.json")
    assert os.path.exists(metrics_path)
    loaded_mc = MetricsCollector.load(metrics_path)
    assert loaded_mc.goal_id == "g_metrics"
    assert len(loaded_mc.phases) > 0


def test_live_telemetry_records_events(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_telemetry", title="Telemetry Goal", description="Telemetry captures events")
    c1 = _make_contract("t1")
    plan = Plan(goal_id=goal.goal_id, contracts=[c1])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    record_event(run_dir, "execution_start", goal_id=goal.goal_id)
    supervisor.execute_plan()
    record_event(run_dir, "execution_complete", goal_id=goal.goal_id)

    events = load_events(run_dir)
    assert len(events) >= 2
    event_types = [e["event_type"] for e in events]
    assert "execution_start" in event_types
    assert "execution_complete" in event_types


# SAFETY GROUP


def test_live_safety_check_runs_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    c1 = _make_contract("t1")
    c2 = _make_contract("t2", depends_on=["t1"])
    plan = Plan(goal_id="g_safety_clean", contracts=[c1, c2])

    assert len(list_templates()) > 0
    report = run_safety_checks(plan, ws_dir)
    assert report.passed is True
    assert report.failed_checks == 0
    formatted = format_safety_report(report)
    assert "PASSED" in formatted


def test_live_safety_check_detects_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    c1 = _make_contract("t1", depends_on=["t2"])
    c2 = _make_contract("t2", depends_on=["t1"])
    plan = Plan(goal_id="g_safety_cycle", contracts=[c1, c2])

    report = run_safety_checks(plan, ws_dir)
    assert report.passed is False
    assert any(i.issue_type == "dependency_cycle" for i in report.issues)


def test_live_safety_uses_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    c1 = _make_contract("t1", max_attempts=5)
    c2 = _make_contract("t2", max_attempts=5)
    plan = Plan(goal_id="g_safety_limits", contracts=[c1, c2])
    limits = ResourceLimits(max_attempts_global=3)

    report = run_safety_checks(plan, ws_dir, limits=limits)
    assert any(i.issue_type == "resource_exceeded" for i in report.issues)
    assert any(i.severity == "warning" for i in report.issues)


# DURABILITY GROUP


def test_live_checkpoint_then_recover(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    run_dir = str(tmp_path / "runs")
    save_checkpoint(
        run_dir=run_dir,
        iteration=1,
        plan_contracts=[{"task_id": "t1"}],
        results={"t1": {"status": "COMPLETE"}},
        graph_statuses={"t1": "COMPLETE"},
        evidence_store={"t1": ["scratch/t1_out.txt"]},
        goal_status="IN_PROGRESS",
        total_contracts=1,
    )

    cps = list_checkpoints(run_dir)
    assert len(cps) == 1
    rec = recover_from_checkpoint(run_dir)
    assert rec["recovered"] is True
    assert rec["iteration"] == 1
    assert rec["results"] == {"t1": {"status": "COMPLETE"}}
    assert rec["goal_status"] == "IN_PROGRESS"


def test_live_dryrun_via_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g_dryrun", title="DryRun Goal", description="Dry run via supervisor")
    out_path = "scratch/dry_out.txt"
    contracts = [_make_contract("t1", output_path=out_path)]
    plan = Plan(goal_id=goal.goal_id, contracts=contracts)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir, dry_run=True)
    res = supervisor.execute_plan()
    assert res.get("t1") in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")

    full_out = os.path.join(ws_dir, out_path)
    assert os.path.exists(full_out)
    with open(full_out, "r", encoding="utf-8") as f:
        content = f.read()
        assert "SIMULATED" in content
