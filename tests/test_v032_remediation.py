"""v0.3.2 remediation regression tests — VULN-004/005, rollback API, verify_receipt."""

import json
import os
import threading


class TestThreadLocalDurableContext:
    """VULN-004: module-global _ACTIVE_CONTEXT cross-contaminated concurrent workflows."""

    def test_concurrent_workflows_no_cross_contamination(self, tmp_path):
        import orchestrator.decorators as dec
        from orchestrator.decorators import DurableContext, step

        results = {}

        def run_wf(n):
            ctx = DurableContext(f"g-tl-{n}", os.path.join(str(tmp_path), f"run{n}"))
            ctx.initialize()
            dec._set_active_context(ctx)
            try:
                results[n] = step("shared", lambda: {"n": n})["n"]
            finally:
                dec._set_active_context(None)
                ctx.close()

        t1 = threading.Thread(target=run_wf, args=(1,))
        t2 = threading.Thread(target=run_wf, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert results[1] == 1 and results[2] == 2


class TestOutsideContextWarning:
    """VULN-005: step() outside a durable context raises RuntimeError by default, warns under lenient mode."""

    def test_warns_and_executes(self, capsys, monkeypatch):
        import pytest
        from orchestrator.decorators import step

        # Fail-closed by default
        with pytest.raises(RuntimeError, match="called outside a @durable context"):
            step("ghost", lambda: "x")

        # Lenient mode opt-in
        monkeypatch.setenv("LETITLOOP_LENIENT", "1")
        out = step("ghost", lambda: "x")
        assert out == "x"
        captured = capsys.readouterr()
        combined = (captured.out + captured.err).lower()
        assert "non-durably" in combined or "outside" in combined


class TestStateRollback:
    """SPEC-MISMATCH-002: independent deepcopy semantics."""

    def test_rollback_is_independent_deepcopy(self, tmp_path):
        from orchestrator.state import create_initial_state

        st = create_initial_state("t-rb", journal_dir=str(tmp_path))
        st.transition("PREFLIGHT_RUNNING", reason="a")
        rb = st.rollback()
        rb["data"]["poison"] = True
        rb["status"] = "MUTATED"
        rb["events"].append({"fake": 1})
        assert "poison" not in st.data
        assert st.status == "PREFLIGHT_RUNNING"
        assert not any(e.get("fake") for e in st.events)

    def test_rollback_reflects_live_state_at_call_time(self, tmp_path):
        from orchestrator.state import create_initial_state

        st = create_initial_state("t-rb2", journal_dir=str(tmp_path))
        st.transition("PREFLIGHT_RUNNING", reason="a")
        rb1 = st.rollback()
        st.transition("READY", reason="b")
        rb2 = st.rollback()
        assert rb1["status"] == "PREFLIGHT_RUNNING"
        assert rb2["status"] == "READY"


class TestVerifyReceipt:
    """Phase 3.1 API: verify_receipt fails closed on tamper/missing."""

    def test_clean_tampered_missing(self, tmp_path):
        from orchestrator.receipts import load_or_create_run_key, seal_artifact
        from orchestrator.verifier import verify_receipt

        run_dir = str(tmp_path)
        art = os.path.join(run_dir, "evidence.json")
        json.dump({"all_passed": True}, open(art, "w"))
        key = load_or_create_run_key(run_dir)
        seal_artifact(art, key)
        assert verify_receipt(art) is True  # key auto-loaded from artifact dir
        with open(art, "w") as f:
            json.dump({"all_passed": False}, f)
        assert verify_receipt(art) is False  # tamper detected
        assert verify_receipt(os.path.join(run_dir, "missing.json")) is False

    def test_explicit_key(self, tmp_path):
        from orchestrator.receipts import seal_artifact
        from orchestrator.verifier import verify_receipt

        run_dir = str(tmp_path)
        art = os.path.join(run_dir, "a.json")
        json.dump({"v": 1}, open(art, "w"))
        seal_artifact(art, "explicit-key-123")
        assert verify_receipt(art, "explicit-key-123") is True
        assert verify_receipt(art, "wrong-key") is False
