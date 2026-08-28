"""Live end-to-end smoke test against a configured real provider.

Runs one minimal scratch task through the full supervisor loop with a real
LLM call. Requires at least one configured provider (LLM_API_KEY +
LLM_BASE_URL, or any standard provider key in the environment).

Usage:
    pytest tests/test_live_smoke.py -v

Default: skipped unless a provider is configured.
"""

import json
import os
import sys

import pytest
from orchestrator.goal import Goal, Plan
from orchestrator.llm import configured_providers
from orchestrator.models import ModelRegistry
from orchestrator.state import load_state
from orchestrator.supervisor import Supervisor

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not configured_providers(),
        reason="no LLM provider configured — set OPENAI_API_KEY / GEMINI_API_KEY / "
        "ANTHROPIC_API_KEY / DEEPSEEK_API_KEY or LLM_API_KEY+LLM_BASE_URL",
    ),
]


TASK_ID = "live-smoke-1"
OUTPUT_PATH = "scratch/live-smoke/output.txt"


@pytest.fixture(autouse=True)
def ensure_no_fake_env(monkeypatch):
    monkeypatch.delenv("FAKE_WORKER", raising=False)
    monkeypatch.delenv("FAKE_QC", raising=False)


def _collect_evidence(task_dir, run_dir, goal_id):
    """Collect all evidence files for post-hoc inspection."""
    evidence_files = {}
    if os.path.isdir(task_dir):
        for fname in os.listdir(task_dir):
            fpath = os.path.join(task_dir, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        evidence_files[fname] = f.read()[:2000]
                except Exception:
                    evidence_files[fname] = "[read error]"
    summary = {
        "goal_id": goal_id,
        "task_id": TASK_ID,
        "output_path": OUTPUT_PATH,
        "evidence_files": evidence_files,
    }
    summary_path = os.path.join(run_dir, "live_smoke_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    return summary_path


def test_live_smoke_scratch_task(tmp_path, monkeypatch):
    """Minimal scratch task using the default worker model, single output,
    file_exists check."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    task_dir = os.path.join(run_dir, TASK_ID)

    model = ModelRegistry.default_worker()
    contract_dict = {
        "task_id": TASK_ID,
        "title": "Live Smoke Test",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/live-smoke/"], "deny": []},
        "objective": f"Write a Python print line to {OUTPUT_PATH}",
        "worker": {
            "model": model,
            "max_attempts": 1,
        },
        "inputs": [],
        "outputs": [{"path": OUTPUT_PATH}],
        "acceptance_checks": [
            {"id": "check-exists", "kind": "file_exists", "path": OUTPUT_PATH},
        ],
        "qc": {"required": False, "lens": "code_correctness"},
    }

    goal = Goal(goal_id="live-smoke-goal", title="Live Smoke", description="")
    plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": TASK_ID, "contract": contract_dict}])

    print(f"\n[LIVE SMOKE] starting — model={model}", file=sys.stderr)
    print(f"[LIVE SMOKE] timeout=60s worker, task_dir={task_dir}", file=sys.stderr)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()

    task_status = res.get(TASK_ID, "???")
    print(f"[LIVE SMOKE] result={task_status}", file=sys.stderr)

    # Assert output created
    output_path = os.path.join(ws_dir, OUTPUT_PATH)
    if task_status in ("COMPLETE", "complete"):
        assert os.path.isfile(output_path), f"output not created: {output_path}"
        with open(output_path, "r") as f:
            content = f.read()
        print(f"[LIVE SMOKE] output ({len(content)} bytes): {content[:200]}", file=sys.stderr)
    else:
        output_exists = os.path.isfile(output_path)
        print(f"[LIVE SMOKE] task not complete (status={task_status}), output_exists={output_exists}", file=sys.stderr)

    # Collect all evidence
    summary_path = _collect_evidence(task_dir, run_dir, goal.goal_id)
    print(f"[LIVE SMOKE] summary written to {summary_path}", file=sys.stderr)
    print(f"[LIVE SMOKE] goal status: {goal.status}", file=sys.stderr)
    print(f"[LIVE SMOKE] results: {res}", file=sys.stderr)

    # Read back state for the record
    state_file = os.path.join(task_dir, "state.json")
    if os.path.isfile(state_file):
        state = load_state(state_file)
        print(f"[LIVE SMOKE] final state: {state.status} attempt={state.attempt}", file=sys.stderr)
