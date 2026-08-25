"""Engine batch 4 — redaction firewall + dropped-counter visibility (TDD)."""

import os


class TestRedaction:
    def test_aws_secret_access_key_redacted(self):
        from orchestrator.redaction import redact

        text = "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        out = redact(text)
        assert "wJalrXUtnFEMI" not in out

    def test_aws_access_key_id_redacted(self):
        from orchestrator.redaction import redact

        out = redact("AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in out

    def test_existing_patterns_still_redacted(self):
        from orchestrator.redaction import redact

        assert "ghp_16CharactersXXXXXXXXXX" not in redact("ghp_16CharactersXXXXXXXXXX")
        assert "sk-abcdefghijklmnop1234" not in redact("sk-abcdefghijklmnop1234")
        assert "AKIAIOSFODNN7EXAMPLE" not in redact("key=AKIAIOSFODNN7EXAMPLE")

    def test_plain_text_untouched(self):
        from orchestrator.redaction import redact

        text = "def compute(x):\n    return x + 1  # nothing secret\n"
        assert redact(text) == text

    def test_qc_review_delegate_unchanged(self):
        from orchestrator.qc_review import _redact_secrets
        from orchestrator.redaction import redact

        assert _redact_secrets("ghp_16CharactersXXXXXXXXXX") == redact("ghp_16CharactersXXXXXXXXXX")

    def test_worker_output_log_is_redacted(self, tmp_path):
        """The worker journal (evidence) must never contain raw secrets."""
        from orchestrator.worker import _write_output_log

        log_path = os.path.join(str(tmp_path), "worker_output.log")
        _write_output_log(
            log_path,
            exit_code=0,
            elapsed=1.0,
            materialized=[],
            stdout="connected with aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n",
            stderr="",
        )
        body = open(log_path, encoding="utf-8").read()
        assert "wJalrXUtnFEMI" not in body


class TestDroppedCounterVisibility:
    def test_run_summary_reports_dropped_events(self, tmp_path, monkeypatch, capsys):
        """Saturation must be visible post-run, not silent (#40 follow-through)."""
        from orchestrator.events import EventBus
        from orchestrator.goal import Goal, Plan
        from orchestrator.supervisor import Supervisor

        noisy = EventBus(max_concurrent_deliveries=1)
        noisy.subscribe(lambda _e: time.sleep(0.05))
        for _ in range(10):
            noisy.publish("contract.working", goal_id="g")
        assert noisy.dropped_count > 0

        monkeypatch.setattr("orchestrator.supervisor.reporting.get_bus", lambda: noisy, raising=False)
        ws = str(tmp_path)
        g = Goal(goal_id="g-drop", title="t", description="d")
        plan = Plan(goal_id="g-drop", contracts=[])
        sup = Supervisor(g, plan, workspace_root=ws, run_dir=os.path.join(ws, "run"))
        sup._print_run_summary()
        captured = capsys.readouterr()
        out = (captured.out + captured.err).lower()
        assert "dropped" in out and str(noisy.dropped_count) in out


import time  # noqa: E402
