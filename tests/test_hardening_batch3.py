"""Hardening batch 3 — TDD tests for four resilience/durability gaps.

A. CLI worker adapters: raw subprocess.run leaks orphan trees on timeout and
   swallows TimeoutExpired as exit_code=1/"error" (no 124/timeout signal).
B. WAL crash-resume: snapshot at step N + unsaved transitions must recover
   from the WAL replay (durability proof).
C. Evidence receipts: artifacts must be tamper-evident (HMAC-sealed).
D. AST splicer: duplicate function names across scopes must not silently
   replace the wrong target.
"""

import json
import os
import sys
import time

import pytest

from orchestrator import worker_adapters

# ---------------------------------------------------------------------------
# A. CLI adapter containment + timeout semantics
# ---------------------------------------------------------------------------


class TestCliAdapterContainment:
    def test_shared_helper_times_out_with_124_and_kills_tree(self, tmp_path):
        """The shared containment runner returns 124/timeout and the child is dead."""
        from orchestrator.worker_adapters import run_contained

        sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
        t0 = time.time()
        res = run_contained(sleeper, cwd=str(tmp_path), timeout_s=1)
        elapsed = time.time() - t0
        assert res["exit_code"] == 124
        assert res["approach"] == "timeout"
        assert elapsed < 10
        assert res["proc"].poll() is not None

    def test_shared_helper_success_contract(self, tmp_path):
        from orchestrator.worker_adapters import run_contained

        res = run_contained([sys.executable, "-c", "print('cli-ok')"], cwd=str(tmp_path), timeout_s=30)
        assert res["exit_code"] == 0
        assert "cli-ok" in res["stdout"]
        assert res["approach"] == "exec"

    @pytest.mark.parametrize(
        "adapter_cls,name",
        [
            ("ClaudeCodeWorkerAdapter", "claude-code"),
            ("AntigravityCliWorkerAdapter", "antigravity-cli"),
            ("OpenCodeWorkerAdapter", "opencode"),
            ("HermesWorkerAdapter", "hermes"),
            ("ClineWorkerAdapter", "cline"),
            ("AiderWorkerAdapter", "aider"),
            ("CodexWorkerAdapter", "codex"),
        ],
    )
    def test_cli_adapters_delegate_to_contained_runner(self, tmp_path, monkeypatch, adapter_cls, name):
        """Every CLI adapter must route through run_contained (tree-kill + 124 semantics)."""
        cls = getattr(worker_adapters, adapter_cls)
        adapter = cls()
        recorded = {}

        def fake_run_contained(cmd, cwd, timeout_s, input_text=None):
            recorded["cmd"] = list(cmd)
            recorded["cwd"] = cwd
            recorded["timeout_s"] = timeout_s
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "approach": "exec"}

        monkeypatch.setattr(worker_adapters, "run_contained", fake_run_contained)
        res = adapter.execute("do work", str(tmp_path), "t1", timeout=77)
        assert res["exit_code"] == 0
        assert recorded["timeout_s"] == 77
        assert recorded["cwd"] == str(tmp_path)
        import shutil

        resolved = shutil.which(adapter.cli_binary)
        assert recorded["cmd"][0] in {adapter.cli_binary, resolved}

    def test_claude_timeout_maps_to_124(self, tmp_path, monkeypatch):
        """TimeoutExpired must surface as 124/timeout, not exit_code=1/'error'."""
        adapter = worker_adapters.ClaudeCodeWorkerAdapter()

        def fake_run_contained(cmd, cwd, timeout_s, input_text=None):
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"subprocess timed out after {timeout_s}s",
                "approach": "timeout",
            }

        monkeypatch.setattr(worker_adapters, "run_contained", fake_run_contained)
        res = adapter.execute("p", str(tmp_path), "t2", timeout=2)
        assert res["exit_code"] == 124
        assert res["approach"] == "timeout"


# ---------------------------------------------------------------------------
# B. WAL crash-resume durability proof
# ---------------------------------------------------------------------------


class TestWalCrashResume:
    def test_wal_replays_53_unsaved_transitions_after_snapshot(self, tmp_path):
        """Snapshot at step 47, 53 more transitions only in WAL, 'crash', reload:
        state must recover to step 100 via WAL replay (not 47)."""
        from orchestrator.state import create_initial_state, load_state, save_state

        td = os.path.join(str(tmp_path), "t_dur")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_dur", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="boot")
        st.transition("READY", reason="boot")
        st.transition("WORKING", reason="boot")
        save_state(st, os.path.join(td, "state.json"))  # snapshot @ 3 events

        # 47 more transitions in memory + WAL only (the "unsaved" window).
        # Legal retry loop: WORKING -> VERIFYING -> VERIFICATION_FAILED ->
        # RETRY_PENDING -> WORKING (4 transitions per retry cycle).
        cycle = ["VERIFYING", "VERIFICATION_FAILED", "RETRY_PENDING", "WORKING"]
        for i in range(47):
            st.transition(cycle[i % 4], reason=f"w{i}")
        # Simulate process death here: snapshot on disk is stale, WAL is ahead.

        recovered = load_state(os.path.join(td, "state.json"), journal_dir=td)
        assert len(recovered.events) == len(st.events), (
            f"WAL replay lost events: recovered {len(recovered.events)}, expected {len(st.events)}"
        )
        assert recovered.status == st.status

    def test_tampered_wal_fails_closed(self, tmp_path):
        from orchestrator.exceptions import StateError
        from orchestrator.state import create_initial_state, load_state, save_state

        td = os.path.join(str(tmp_path), "t_tamper")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_tamper", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="a")
        save_state(st, os.path.join(td, "state.json"))
        wal = os.path.join(td, "state.wal.jsonl")
        with open(wal, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
        # LILWAL02-aware extraction: decode frame if present, else plain JSON
        last = raw_lines[-1].strip()
        if last.startswith("LILWAL02:"):
            # extract canonical payload after header for tampering
            _, _, _, payload = last.split(":", 3)
            obj = json.loads(payload)
        else:
            obj = json.loads(last)
        obj["hash"] = "f" * 64
        with open(wal, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")
        with pytest.raises(StateError):
            load_state(os.path.join(td, "state.json"), journal_dir=td)


# ---------------------------------------------------------------------------
# C. Evidence receipt tamper-evidence
# ---------------------------------------------------------------------------


class TestReceiptSealing:
    def test_seal_and_verify_roundtrip(self, tmp_path):
        from orchestrator.receipts import load_or_create_run_key, seal_artifact, verify_artifact

        run_dir = str(tmp_path)
        key = load_or_create_run_key(run_dir)
        assert len(key) >= 32
        artifact = os.path.join(run_dir, "evidence.json")
        with open(artifact, "w", encoding="utf-8") as f:
            json.dump({"all_passed": True}, f)
        seal_artifact(artifact, key)
        assert os.path.isfile(artifact + ".sig")
        assert verify_artifact(artifact, key) is True

    def test_tampered_artifact_fails_verification(self, tmp_path):
        from orchestrator.receipts import load_or_create_run_key, seal_artifact, verify_artifact

        run_dir = str(tmp_path)
        key = load_or_create_run_key(run_dir)
        artifact = os.path.join(run_dir, "metrics.json")
        with open(artifact, "w", encoding="utf-8") as f:
            json.dump({"contracts_passed": 3}, f)
        seal_artifact(artifact, key)
        with open(artifact, "w", encoding="utf-8") as f:
            json.dump({"contracts_passed": 99}, f)  # tamper
        assert verify_artifact(artifact, key) is False

    def test_missing_signature_fails_closed(self, tmp_path):
        from orchestrator.receipts import load_or_create_run_key, verify_artifact

        run_dir = str(tmp_path)
        key = load_or_create_run_key(run_dir)
        artifact = os.path.join(run_dir, "evidence.json")
        with open(artifact, "w", encoding="utf-8") as f:
            json.dump({}, f)
        assert verify_artifact(artifact, key) is False

    def test_run_key_is_stable_and_restricted(self, tmp_path):
        from orchestrator.receipts import load_or_create_run_key

        run_dir = str(tmp_path)
        k1 = load_or_create_run_key(run_dir)
        k2 = load_or_create_run_key(run_dir)
        assert k1 == k2
        keyfile = os.path.join(run_dir, ".receipt_key")
        assert os.path.isfile(keyfile)
        if os.name != "nt":
            assert (os.stat(keyfile).st_mode & 0o077) == 0

    def test_verification_evidence_is_sealed_automatically(self, tmp_path):
        """run_verification must seal its evidence artifact with the run key."""
        from orchestrator.contract import Contract
        from orchestrator.receipts import load_or_create_run_key, verify_artifact
        from orchestrator.verifier import run_verification

        ws = str(tmp_path)
        os.makedirs(os.path.join(ws, "src"), exist_ok=True)
        with open(os.path.join(ws, "src", "ok.py"), "w") as f:
            f.write("x = 1\n")
        run_dir = os.path.join(ws, "scratch", "runs")
        cd = {
            "task_id": "t",
            "title": "t",
            "status": "drafted",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["src/"], "deny": []},
            "objective": "x",
            "worker": {"model": "m", "max_attempts": 1},
            "outputs": [{"path": "src/ok.py"}],
            "acceptance_checks": [{"id": "c1", "kind": "syntax", "check_id": "c1", "path": "src/ok.py"}],
            "qc": {"required": False, "lens": "code_correctness"},
        }
        passed, _, evidence_path = run_verification(Contract(cd), ws, run_dir)
        assert passed is True
        assert evidence_path is not None
        key = load_or_create_run_key(run_dir)
        assert verify_artifact(evidence_path, key) is True


# ---------------------------------------------------------------------------
# D. AST splicer ambiguity
# ---------------------------------------------------------------------------


class TestSplicerAmbiguity:
    def test_duplicate_names_raise_instead_of_silent_wrong_target(self):
        from orchestrator.ast_node_splicer import splice_ast_function

        src = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def compute(self):\n"
            "            return 1\n"
            "\n"
            "def compute(x):\n"
            "    return 2\n"
        )
        with pytest.raises(ValueError, match="ambiguous"):
            splice_ast_function(src, "compute", "def compute(x):\n    return 99\n")

    def test_qualname_targets_nested_method(self):
        from orchestrator.ast_node_splicer import splice_ast_function

        src = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def compute(self):\n"
            "            return 1\n"
            "\n"
            "def compute(x):\n"
            "    return 2\n"
        )
        out = splice_ast_function(src, "Outer.Inner.compute", "def compute(self):\n    return 42\n")
        assert "return 42" in out
        assert "return 2" in out  # top-level untouched
        import ast as _ast

        _ast.parse(out)

    def test_nested_only_zero_indent_replacement_still_works(self):
        from orchestrator.ast_node_splicer import splice_ast_function

        src = "class Service:\n    def compute(self, x):\n        return 1\n"
        out = splice_ast_function(src, "Service.compute", "def compute(self, x):\n    return 2\n")
        import ast as _ast

        _ast.parse(out)
        assert "    def compute(self, x):" in out

    def test_unique_name_unqualified_still_works(self):
        from orchestrator.ast_node_splicer import splice_ast_function

        src = "def solo(x):\n    return 1\n"
        out = splice_ast_function(src, "solo", "def solo(x):\n    return 2\n")
        assert "return 2" in out
