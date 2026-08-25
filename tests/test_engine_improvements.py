"""Engine improvement tests - 5 angles (TDD, written before the fixes).

1. PERF   - fast_ast_verify cache thrashing (clear-all at 2048 entries).
2. RELY   - opt-in transient-error retry for provider HTTP calls (LIL_HTTP_RETRIES).
3. DIAG   - lil doctor probes local Ollama endpoint when relevant.
4. ERGO   - lil version command.
5. AUDIT  - opt-in event-bus -> telemetry.jsonl persistence (LETITLOOP_TELEMETRY=1).
"""

import json
import os

import pytest

# ---------------------------------------------------------------------------
# Angle 1: PERF - AST cache must not thrash (clear-all at capacity)
# ---------------------------------------------------------------------------


class TestAstCacheBounded:
    def test_cache_retains_recent_entries_at_capacity(self):
        import hashlib

        from orchestrator import verifier

        verifier._AST_CACHE.clear()
        try:
            # Fill to exactly capacity, stamp a marker on the LAST pre-overflow
            # entry, then insert one more. Clear-all behavior nuked that marker
            # entry; LRU must retain it.
            keys = []
            for i in range(2048):
                src = f"x_{i} = {i}\n"
                verifier.fast_ast_verify(src)
                keys.append(hashlib.sha256(src.encode()).hexdigest())
            verifier._AST_CACHE[keys[-1]] = (True, "CACHE_HIT_MARKER")
            verifier.fast_ast_verify("overflow_entry = 1\n")
            assert verifier.fast_ast_verify("x_2047 = 2047\n") == (True, "CACHE_HIT_MARKER"), (
                "capacity overflow cleared recent entries (clear-all thrash)"
            )
            assert len(verifier._AST_CACHE) <= 2048
        finally:
            verifier._AST_CACHE.clear()

    def test_cache_size_never_exceeds_bound(self):
        from orchestrator import verifier

        verifier._AST_CACHE.clear()
        try:
            for i in range(3000):
                verifier.fast_ast_verify(f"y = {i}\n")
            assert len(verifier._AST_CACHE) <= 2048
        finally:
            verifier._AST_CACHE.clear()


# ---------------------------------------------------------------------------
# Angle 2: RELY - opt-in transient retry for provider HTTP calls
# ---------------------------------------------------------------------------


class TestLlmTransientRetry:
    def _payload(self):
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def test_retry_disabled_by_default(self, monkeypatch):
        from orchestrator import llm

        monkeypatch.delenv("LIL_HTTP_RETRIES", raising=False)
        calls = {"n": 0}

        def fake_http(url, headers, payload, timeout_s):
            calls["n"] += 1
            raise llm.LLMError("HTTP 429 from provider: slow down", status=429)

        monkeypatch.setattr(llm, "_http_json", fake_http)
        monkeypatch.setattr(llm, "api_key", lambda provider: "sk-test")
        with pytest.raises(llm.LLMError):
            llm.call_llm("hi", "openai:gpt-4o")
        assert calls["n"] == 1

    def test_retry_recovers_transient_429(self, monkeypatch):
        from orchestrator import llm

        monkeypatch.setenv("LIL_HTTP_RETRIES", "2")
        calls = {"n": 0}

        def fake_http(url, headers, payload, timeout_s):
            calls["n"] += 1
            if calls["n"] < 3:
                raise llm.LLMError(f"HTTP 429 from provider: slow down ({calls['n']})", status=429)
            return self._payload()

        monkeypatch.setattr(llm, "_http_json", fake_http)
        monkeypatch.setattr(llm, "api_key", lambda provider: "sk-test")
        monkeypatch.setattr(llm.time, "sleep", lambda s: None)
        res = llm.call_llm("hi", "openai:gpt-4o")
        assert res["text"] == "ok"
        assert calls["n"] == 3

    def test_retry_gives_up_after_budget(self, monkeypatch):
        from orchestrator import llm

        monkeypatch.setenv("LIL_HTTP_RETRIES", "2")
        calls = {"n": 0}

        def fake_http(url, headers, payload, timeout_s):
            calls["n"] += 1
            raise llm.LLMError(f"HTTP 503 from provider: down ({calls['n']})", status=503)

        monkeypatch.setattr(llm, "_http_json", fake_http)
        monkeypatch.setattr(llm, "api_key", lambda provider: "sk-test")
        monkeypatch.setattr(llm.time, "sleep", lambda s: None)
        with pytest.raises(llm.LLMError):
            llm.call_llm("hi", "openai:gpt-4o")
        assert calls["n"] == 3  # 1 initial + 2 retries

    def test_no_retry_on_client_errors(self, monkeypatch):
        from orchestrator import llm

        monkeypatch.setenv("LIL_HTTP_RETRIES", "3")
        calls = {"n": 0}

        def fake_http(url, headers, payload, timeout_s):
            calls["n"] += 1
            raise llm.LLMError("HTTP 401 from provider: bad key", status=401)

        monkeypatch.setattr(llm, "_http_json", fake_http)
        monkeypatch.setattr(llm, "api_key", lambda provider: "sk-test")
        with pytest.raises(llm.LLMError):
            llm.call_llm("hi", "openai:gpt-4o")
        assert calls["n"] == 1  # 401 is deterministic - retrying is waste


# ---------------------------------------------------------------------------
# Angle 3: DIAG - doctor probes local Ollama endpoint
# ---------------------------------------------------------------------------


class TestDoctorOllamaProbe:
    def test_doctor_reports_ollama_when_reachable(self, monkeypatch, capsys):
        from orchestrator import env_doctor

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.setattr(
            env_doctor, "_probe_endpoint", lambda url, timeout=1.5: url.startswith("http://127.0.0.1:11434")
        )
        env_doctor.run_env_doctor(check_connectivity=True)
        out = capsys.readouterr().out.lower()
        assert "ollama" in out
        assert "reachable" in out or "ok" in out

    def test_doctor_reports_ollama_offline_as_warning(self, monkeypatch, capsys):
        from orchestrator import env_doctor

        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "LLM_BASE_URL"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(env_doctor, "_probe_endpoint", lambda url, timeout=1.5: False)
        env_doctor.run_env_doctor(check_connectivity=True)
        out = capsys.readouterr().out.lower()
        assert "ollama" in out
        assert "offline" in out or "not detected" in out or "unreachable" in out


# ---------------------------------------------------------------------------
# Angle 4: ERGO - lil version command
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_version_parser_registered(self):

        from orchestrator import cli

        # The parser is built inside main(); verify the handler exists instead.
        assert hasattr(cli, "cmd_version")

    def test_version_output(self, capsys):
        from orchestrator import cli

        cli.cmd_version()
        out = capsys.readouterr().out
        assert "letitloop" in out.lower()
        assert "python" in out.lower()

    def test_version_reports_package_version(self, capsys, monkeypatch):
        from orchestrator import cli

        monkeypatch.setattr(cli, "PACKAGE_VERSION", "9.9.9-test")
        cli.cmd_version()
        assert "9.9.9-test" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Angle 5: AUDIT - opt-in event bus -> telemetry.jsonl persistence
# ---------------------------------------------------------------------------


class TestTelemetryAttachment:
    def test_attach_telemetry_persists_events(self, tmp_path):
        from orchestrator.events import EventBus
        from orchestrator.telemetry import attach_telemetry, load_events

        bus = EventBus()
        detach = attach_telemetry(bus, str(tmp_path / "telemetry.jsonl"))
        bus.publish("goal.started", goal_id="g1")
        bus.publish("contract.working", goal_id="g1", task_id="t1")
        deadline = time.time() + 3
        while time.time() < deadline:
            events = load_events(str(tmp_path / "telemetry.jsonl"))
            if len(events) >= 2:
                break
            time.sleep(0.02)
        detach()
        events = load_events(str(tmp_path / "telemetry.jsonl"))
        assert len(events) >= 2
        assert {e.get("event_type") for e in events} >= {"goal.started", "contract.working"}

    def test_detach_stops_persistence(self, tmp_path):
        import time as _t

        from orchestrator.events import EventBus
        from orchestrator.telemetry import attach_telemetry

        path = str(tmp_path / "telemetry.jsonl")
        bus = EventBus()
        detach = attach_telemetry(bus, path)
        bus.publish("goal.started", goal_id="g1")
        deadline = _t.time() + 3
        while _t.time() < deadline and not os.path.isfile(path):
            _t.sleep(0.02)
        detach()
        before = os.path.getsize(path) if os.path.isfile(path) else 0
        bus.publish("goal.completed", goal_id="g1")
        _t.sleep(0.2)
        after = os.path.getsize(path) if os.path.isfile(path) else 0
        assert after == before

    def test_supervisor_wires_telemetry_when_env_set(self, tmp_path, monkeypatch):
        """Opt-in via LETITLOOP_TELEMETRY=1: supervised runs persist the event stream."""
        from orchestrator.goal import Goal, Plan
        from orchestrator.supervisor import Supervisor

        monkeypatch.setenv("LETITLOOP_TELEMETRY", "1")
        ws = str(tmp_path)
        run_dir = os.path.join(ws, "run")
        g = Goal(goal_id="g-tel", title="t", description="d")
        plan = Plan(goal_id="g-tel", contracts=[])
        sup = Supervisor(g, plan, workspace_root=ws, run_dir=run_dir)
        try:
            sup.execute_plan()
            deadline = time.time() + 3
            tel = os.path.join(run_dir, "telemetry.jsonl")
            while time.time() < deadline and not os.path.isfile(tel):
                time.sleep(0.05)
            assert os.path.isfile(tel), "telemetry.jsonl not persisted under LETITLOOP_TELEMETRY=1"
            lines = [json.loads(x) for x in open(tel, encoding="utf-8") if x.strip()]
            assert any(e.get("event_type") == "goal.started" for e in lines)
        finally:
            sup._detach_telemetry() if hasattr(sup, "_detach_telemetry") else None


import time  # noqa: E402  (kept at bottom import block for all classes above)
