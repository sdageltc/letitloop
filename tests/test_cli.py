"""End-to-end CLI integration tests for orchestrator.cli.

Each test writes self-contained temp contracts, runs CLI via subprocess,
and checks stdout/stderr and state.json outcomes.
"""

import json
import os
import subprocess
import sys

import pytest

from orchestrator.llm import configured_providers

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI_MODULE = "orchestrator.cli"
CONTRACT_DIR = os.path.join(PROJECT_ROOT, "scratch", "test_cli_contracts")
MODEL = "openai:gpt-4o-mini"

REQUIRES_PROVIDER = pytest.mark.skipif(
    not configured_providers(),
    reason="no LLM provider configured — set OPENAI_API_KEY / GEMINI_API_KEY / "
    "ANTHROPIC_API_KEY / DEEPSEEK_API_KEY or LLM_API_KEY+LLM_BASE_URL",
)

BASE_CONTRACT = {
    "title": "CLI test contract",
    "status": "drafted",
    "risk_tier": "auto",
    "workspace_scope": {
        "allow": ["scratch/test_cli/"],
        "deny": ["AGENTS.md", "memory/", ".opencode/"],
    },
    "worker": {"model": MODEL, "max_attempts": 3},
    "inputs": [],
    "outputs": [{"path": "scratch/test_cli/output.txt"}],
    "acceptance_checks": [
        {"id": "output_exists", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
    ],
    "qc": {"required": False, "lens": "code_correctness"},
    "next_action": "preflight",
}


def _write_contract(contract_dict, name="contract.json"):
    os.makedirs(CONTRACT_DIR, exist_ok=True)
    path = os.path.join(CONTRACT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contract_dict, f, indent=2)
    rel_path = os.path.relpath(path, PROJECT_ROOT)
    return rel_path


def _state_path(run_dir, task_id):
    return os.path.join(run_dir, task_id, "state.json")


def _read_state(run_dir, task_id):
    sp = _state_path(run_dir, task_id)
    if not os.path.isfile(sp):
        return None
    with open(sp, encoding="utf-8") as f:
        return json.load(f)


def _run_cli(run_dir, *args, timeout=30, expect_fail=False):
    cmd = [sys.executable, "-m", CLI_MODULE, "--run-dir", str(run_dir)] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=timeout,
    )
    if not expect_fail and result.returncode != 0:
        print(f"  STDERR: {result.stderr}", file=sys.stderr)
        print(f"  STDOUT: {result.stdout}", file=sys.stderr)
        result.check_returncode()
    return result


def _make_output_file(task_id):
    out_dir = os.path.join(PROJECT_ROOT, "scratch", "test_cli")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "output.txt")
    with open(path, "w") as f:
        f.write("ok")
    return path


def _remove_output_file(task_id):
    path = os.path.join(PROJECT_ROOT, "scratch", "test_cli", "output.txt")
    if os.path.isfile(path):
        os.remove(path)


class TestCliIntegration:
    """All tests share a workspace via tmp_path for --run-dir."""

    @pytest.mark.fast
    @pytest.mark.integration
    @pytest.mark.proof
    @REQUIRES_PROVIDER
    def test_happy_path(self, tmp_path):
        tid = "cli-happy"
        c = dict(BASE_CONTRACT, task_id=tid, objective="Write scratch/test_cli/output.txt with content ok")
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)
        _run_cli(tmp_path, "preflight", tid)

        # work — actual opencode invocation; generous timeout
        _run_cli(tmp_path, "work", tid, timeout=180)

        # manually create the expected output for verification
        _make_output_file(tid)

        _run_cli(tmp_path, "verify", tid)

        state = _read_state(tmp_path, tid)
        assert state is not None, "state.json missing"
        assert state["status"] == "COMPLETE", f"Expected COMPLETE, got {state['status']}"

    @pytest.mark.fast
    @pytest.mark.integration
    @pytest.mark.proof
    def test_repair_loop(self, tmp_path, monkeypatch):
        # Deterministic repair-loop proof: FAKE_WORKER=RETRY fails the first
        # attempt (no output written), then writes FAKE_WORKER_OUTPUT after
        # retry — no real API key required, fully reproducible.
        monkeypatch.setenv("FAKE_WORKER", "RETRY")
        tid = "cli-repair"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="Write scratch/test_cli/output.txt containing exactly '42'",
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "c1", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
                {
                    "id": "c2",
                    "kind": "content_exact",
                    "path": "scratch/test_cli/output.txt",
                    "expected": "FAKE_WORKER_OUTPUT",
                },
            ],
        )
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)
        _run_cli(tmp_path, "preflight", tid)
        _run_cli(tmp_path, "work", tid, timeout=180)

        # corrupt output: write wrong content
        out_dir = os.path.join(PROJECT_ROOT, "scratch", "test_cli")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "output.txt"), "w") as f:
            f.write("wrong")

        r = _run_cli(tmp_path, "verify", tid, expect_fail=True)
        assert r.returncode != 0 or "FAIL" in r.stdout or "VERIFICATION_FAILED" in r.stdout

        state = _read_state(tmp_path, tid)
        assert state["status"] in ("VERIFICATION_FAILED", "RETRY_PENDING")

        # retry with changed approach
        _run_cli(tmp_path, "retry", tid, "--approach", "write the exact number 42")

        _run_cli(tmp_path, "work", tid, timeout=180)
        _run_cli(tmp_path, "verify", tid)

        state = _read_state(tmp_path, tid)
        assert state["status"] == "COMPLETE", f"Expected COMPLETE, got {state['status']}"

    @pytest.mark.fast
    @pytest.mark.integration
    @pytest.mark.proof
    @REQUIRES_PROVIDER
    def test_escalation(self, tmp_path):
        tid = "cli-escalation"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="write output.txt containing 'IMPOSSIBLE'",
            worker={"model": MODEL, "max_attempts": 1},
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "c1", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
                {
                    "id": "c2",
                    "kind": "content_exact",
                    "path": "scratch/test_cli/output.txt",
                    "expected": "THIS IS IMPOSSIBLE BY DESIGN",
                },
            ],
        )
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)
        _run_cli(tmp_path, "preflight", tid)
        _run_cli(tmp_path, "work", tid, timeout=180)

        # write wrong content so verify fails
        out_dir = os.path.join(PROJECT_ROOT, "scratch", "test_cli")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "output.txt"), "w") as f:
            f.write("not-expected")

        r = _run_cli(tmp_path, "verify", tid, expect_fail=True)
        assert "FAIL" in r.stdout or r.returncode != 0

        _run_cli(tmp_path, "retry", tid, "--approach", "will fail", expect_fail=True)

        state = _read_state(tmp_path, tid)
        assert state["status"] == "ESCALATED", f"Expected ESCALATED, got {state['status']}"

        # impossibility log should exist
        imp_path = os.path.join(PROJECT_ROOT, "scratch", "impossibility_theorems.log")
        assert os.path.isfile(imp_path), "impossibility log missing"

    @pytest.mark.fast
    @pytest.mark.integration
    @pytest.mark.proof
    @REQUIRES_PROVIDER
    def test_qc_required(self, tmp_path):
        tid = "cli-qc"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="write output.txt containing 'ok'",
            qc={"required": True, "lens": "code_correctness"},
        )
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)
        _run_cli(tmp_path, "preflight", tid)
        _run_cli(tmp_path, "work", tid, timeout=180)
        _make_output_file(tid)
        _run_cli(tmp_path, "verify", tid)

        state = _read_state(tmp_path, tid)
        assert state["status"] == "VERIFIED", f"Expected VERIFIED, got {state['status']}"

        _run_cli(tmp_path, "qc", tid, "--passed", "--reason", "all good")

        state = _read_state(tmp_path, tid)
        assert state["status"] == "COMPLETE", f"Expected COMPLETE, got {state['status']}"

    @pytest.mark.fast
    @pytest.mark.integration
    def test_resume(self, tmp_path):
        tid = "cli-resume"
        c = dict(BASE_CONTRACT, task_id=tid, objective="resume test")
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)
        _run_cli(tmp_path, "preflight", tid)

        r = _run_cli(tmp_path, "resume", tid)
        assert "READY" in r.stdout or "ready" in r.stdout
        assert "orchestrator work" in r.stdout

    @pytest.mark.fast
    @pytest.mark.integration
    def test_work_from_wrong_state(self, tmp_path):
        tid = "cli-wrong-state"
        c = dict(BASE_CONTRACT, task_id=tid, objective="wrong state test")
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)

        # try work directly after create (still DRAFTED, no preflight)
        r = _run_cli(tmp_path, "work", tid, expect_fail=True)
        assert r.returncode != 0
        err = (r.stderr or "").lower()
        assert any(
            w in err for w in ("drafted", "ready", "retry_pending", "cannot transition", "illegal transition")
        ), f"stderr missing expected keywords: {r.stderr}"

    @pytest.mark.fast
    @pytest.mark.integration
    def test_unknown_task(self, tmp_path):
        r = _run_cli(tmp_path, "status", "nonexistent-task-12345", expect_fail=True)
        assert r.returncode != 0
        err = (r.stderr or "").lower()
        assert "no state found" in err or "not found" in err or "nonexistent" in err

    @pytest.mark.fast
    @pytest.mark.integration
    @REQUIRES_PROVIDER
    def test_illegal_retry(self, tmp_path):
        tid = "cli-illegal-retry"
        c = dict(BASE_CONTRACT, task_id=tid, objective="illegal retry test")
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)
        _run_cli(tmp_path, "preflight", tid)
        _run_cli(tmp_path, "work", tid, timeout=180)
        _make_output_file(tid)
        _run_cli(tmp_path, "verify", tid)

        state = _read_state(tmp_path, tid)
        assert state["status"] == "COMPLETE", f"Expected COMPLETE, got {state['status']}"

        r = _run_cli(tmp_path, "retry", tid, "--approach", "should fail", expect_fail=True)
        assert r.returncode != 0
        err = (r.stderr or "").lower()
        assert "not in retry state" in err or "complete" in err

    @pytest.mark.fast
    @pytest.mark.integration
    def test_doctor_on_active_task(self, tmp_path):
        tid = "cli-doctor-test"
        c = dict(BASE_CONTRACT, task_id=tid, objective="doctor test")
        rel = _write_contract(c, f"{tid}.json")

        _run_cli(tmp_path, "create", rel)
        _run_cli(tmp_path, "preflight", tid)

        r = _run_cli(tmp_path, "doctor", tid)
        assert r.returncode == 0
        assert "READY" in r.stdout or "ready" in r.stdout
        assert "Next" in r.stdout or "next" in r.stdout.lower()
        assert tid in r.stdout


class TestFakeWorker:
    """Fake worker tests — use FAKE_WORKER env var, no real opencode."""

    @pytest.mark.fast
    @pytest.mark.integration
    def test_fake_worker_happy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        tid = "fake-happy"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="Write scratch/test_cli/output.txt with content FAKE_WORKER_OUTPUT",
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "exists", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
                {
                    "id": "content",
                    "kind": "content_exact",
                    "path": "scratch/test_cli/output.txt",
                    "expected": "FAKE_WORKER_OUTPUT",
                },
            ],
        )
        rel = _write_contract(c, f"{tid}.json")
        _run_cli(tmp_path, "create", rel, timeout=10)
        _run_cli(tmp_path, "preflight", tid, timeout=10)
        _run_cli(tmp_path, "work", tid, timeout=10)
        _run_cli(tmp_path, "verify", tid, timeout=10)
        state = _read_state(tmp_path, tid)
        assert state is not None
        assert state["status"] == "COMPLETE", f"Got {state['status']}"

    @pytest.mark.fast
    @pytest.mark.integration
    def test_fake_worker_repair(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "RETRY")
        tid = "fake-repair"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="Write scratch/test_cli/output.txt with content FAKE_WORKER_OUTPUT",
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "exists", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
                {
                    "id": "content",
                    "kind": "content_exact",
                    "path": "scratch/test_cli/output.txt",
                    "expected": "FAKE_WORKER_OUTPUT",
                },
            ],
        )
        rel = _write_contract(c, f"{tid}.json")
        _run_cli(tmp_path, "create", rel, timeout=10)
        _run_cli(tmp_path, "preflight", tid, timeout=10)
        _run_cli(tmp_path, "work", tid, timeout=10)
        r = _run_cli(tmp_path, "verify", tid, timeout=10, expect_fail=True)
        assert r.returncode != 0 or "FAIL" in r.stdout
        state = _read_state(tmp_path, tid)
        assert state["status"] in ("VERIFICATION_FAILED", "RETRY_PENDING")
        _run_cli(tmp_path, "retry", tid, "--approach", "try harder", timeout=10)
        _run_cli(tmp_path, "work", tid, timeout=10)
        _run_cli(tmp_path, "verify", tid, timeout=10)
        state = _read_state(tmp_path, tid)
        assert state["status"] == "COMPLETE", f"Got {state['status']}"

    @pytest.mark.fast
    @pytest.mark.integration
    def test_fake_worker_escalation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "FAIL")
        tid = "fake-escalation"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="Write scratch/test_cli/output.txt with content FAKE_WORKER_OUTPUT",
            worker={"model": MODEL, "max_attempts": 1},
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "exists", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
                {
                    "id": "content",
                    "kind": "content_exact",
                    "path": "scratch/test_cli/output.txt",
                    "expected": "FAKE_WORKER_OUTPUT",
                },
            ],
        )
        rel = _write_contract(c, f"{tid}.json")
        _run_cli(tmp_path, "create", rel, timeout=10)
        _run_cli(tmp_path, "preflight", tid, timeout=10)
        _run_cli(tmp_path, "work", tid, timeout=10)
        _run_cli(tmp_path, "verify", tid, timeout=10, expect_fail=True)
        _run_cli(tmp_path, "retry", tid, "--approach", "will fail", timeout=10, expect_fail=True)
        state = _read_state(tmp_path, tid)
        assert state["status"] == "ESCALATED", f"Got {state['status']}"

    @pytest.mark.fast
    @pytest.mark.integration
    def test_unicode_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        tid = "fake-unicode"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="Write unicode output file",
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "exists", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
            ],
        )
        rel = _write_contract(c, f"{tid}.json")
        _run_cli(tmp_path, "create", rel, timeout=10)
        _run_cli(tmp_path, "preflight", tid, timeout=10)
        _run_cli(tmp_path, "work", tid, timeout=10)
        _run_cli(tmp_path, "verify", tid, timeout=10)
        state = _read_state(tmp_path, tid)
        assert state["status"] == "COMPLETE", f"Got {state['status']}"

    @pytest.mark.fast
    @pytest.mark.integration
    def test_progress_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        tid = "fake-progress-out"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="Write output file with FAKE_WORKER_OUTPUT",
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "exists", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
                {
                    "id": "content",
                    "kind": "content_exact",
                    "path": "scratch/test_cli/output.txt",
                    "expected": "FAKE_WORKER_OUTPUT",
                },
            ],
        )
        rel = _write_contract(c, f"{tid}.json")
        _run_cli(tmp_path, "create", rel, timeout=10)
        r2 = _run_cli(tmp_path, "preflight", tid, timeout=10)
        r3 = _run_cli(tmp_path, "work", tid, timeout=10)
        r4 = _run_cli(tmp_path, "verify", tid, timeout=10)

        assert "[orchestrator]" in (r2.stderr or ""), f"preflight stderr: {r2.stderr}"
        assert "[orchestrator]" in (r3.stderr or ""), f"work stderr: {r3.stderr}"
        assert "[worker]" in (r3.stderr or ""), f"work stderr missing [worker]: {r3.stderr}"
        assert "[orchestrator]" in (r4.stderr or ""), f"verify stderr: {r4.stderr}"
        assert "[verify]" in (r4.stderr or ""), f"verify stderr missing [verify]: {r4.stderr}"

    @pytest.mark.fast
    @pytest.mark.integration
    def test_timeout_restart_diagnosis(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        tid = "fake-timeout-diag"
        c = dict(
            BASE_CONTRACT,
            task_id=tid,
            objective="Write output file with FAKE_WORKER_OUTPUT",
            outputs=[{"path": "scratch/test_cli/output.txt"}],
            acceptance_checks=[
                {"id": "exists", "kind": "file_exists", "path": "scratch/test_cli/output.txt", "expected": True},
            ],
        )
        rel = _write_contract(c, f"{tid}.json")
        _run_cli(tmp_path, "create", rel, timeout=10)
        _run_cli(tmp_path, "preflight", tid, timeout=10)
        _run_cli(tmp_path, "work", tid, timeout=10)

        # Simulate a timed-out worker restart through the event API so the WAL
        # chain stays replayable (direct state.json edits are ignored by
        # load_state, which replays the WAL authoritatively).
        from orchestrator.state import load_state, save_state

        sp = _state_path(tmp_path, tid)
        state = load_state(sp)
        state.patch_worker_result(
            0,
            {
                **state.worker_results[0],
                "exit_code": -1,
                "elapsed_sec": 300.0,
            },
        )
        save_state(state, sp)

        r = _run_cli(tmp_path, "doctor", tid)
        stdout = r.stdout
        assert "Last worker exit" in stdout, f"missing Last worker exit: {stdout}"
        assert "-1" in stdout, f"missing exit code -1: {stdout}"
        assert "300" in stdout, f"missing elapsed 300: {stdout}"
        assert "In progress" in stdout, f"missing In progress: {stdout}"
        assert "Run dir" in stdout, f"missing Run dir: {stdout}"

    @pytest.mark.fast
    @pytest.mark.integration
    @pytest.mark.phase2
    def test_phase2_two_step_goal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        gid = "phase2-proof-two-step"

        # 1. Create goal
        _run_cli(
            tmp_path,
            "goal-create",
            "--goal-id",
            gid,
            "--title",
            "Two-step proof",
            "--description",
            "Step 1 creates a file, Step 2 validates it",
            "--constraints",
            '{"workspace_scope": {"allow": ["scratch/phase2/proof/"], "deny": []}}',
        )

        # 2. Plan goal
        _run_cli(tmp_path, "plan", gid)

        # 3. Supervise goal execution
        _run_cli(tmp_path, "supervise", gid)

        # 4. Assert both contracts completed
        res_cli = _run_cli(tmp_path, "goal-result", gid)
        stdout = res_cli.stdout
        assert "complete" in stdout.lower() or "completed" in stdout.lower()

        c1_state = _read_state(tmp_path, f"{gid}/{gid}-step-1")
        c2_state = _read_state(tmp_path, f"{gid}/{gid}-step-2")
        if not c1_state:
            c1_state = _read_state(tmp_path, f"{gid}-step-1")
        assert c1_state is not None and c1_state["status"] in ("COMPLETE", "complete")
        assert c2_state is not None and c2_state["status"] in ("COMPLETE", "complete")

    @pytest.mark.fast
    def test_cli_install_skill(self, tmp_path):
        res = _run_cli(tmp_path, "install-skill", "--target", "cursor", "--workspace", str(tmp_path))
        assert res.returncode == 0
        assert "Cursor IDE" in res.stdout
        dest_file = tmp_path / ".cursor" / "skills" / "letitloop" / "SKILL.md"
        assert dest_file.is_file()
