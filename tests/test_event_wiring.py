"""Issue #8 part 2 — supervisor event emission wiring + `lil serve` SSE command."""

import argparse
import json
import os
import sys
import threading
import time
import urllib.request

import pytest

from orchestrator.events import get_bus
from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal
from orchestrator.metrics import MetricsCollector
from orchestrator.supervisor import Supervisor

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


class BusRecorder:
    """Synchronous recorder subscribed to every envelope on the global bus."""

    def __init__(self):
        self.events = []
        self._cond = threading.Condition()
        self._unsub = None

    def start(self):
        bus = get_bus()
        bus.clear()
        self._unsub = bus.subscribe(self._on_event)

    def stop(self):
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        get_bus().clear()

    def _on_event(self, envelope):
        with self._cond:
            self.events.append(envelope)
            self._cond.notify_all()

    def wait_for(self, predicate, timeout=30.0):
        deadline = time.time() + timeout
        with self._cond:
            while not predicate(self.events):
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True


@pytest.fixture
def recorder():
    rec = BusRecorder()
    rec.start()
    yield rec
    rec.stop()


def _names(events):
    return [e["event"] for e in events]


def _for_goal(events, goal_id):
    return [e for e in events if e.get("goal_id") == goal_id]


def _in_order(names, sequence):
    it = iter(names)
    return all(any(n == wanted for n in it) for wanted in sequence)


def _make_supervisor(tmp_path, goal_id, description, **kw):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id=goal_id, title=goal_id, description=description)
    plan = generate_contracts(goal, workspace_root=ws_dir)
    return Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir, **kw)


@pytest.mark.fast
def test_metrics_record_contract_status_and_roundtrip(tmp_path):
    mc = MetricsCollector(goal_id="m1")
    assert mc.contracts_by_status == {}
    mc.record_contract_status("COMPLETE")
    mc.record_contract_status("COMPLETE")
    mc.record_contract_status("ESCALATED")
    mc.record_three_strike_escalation()

    snap = mc.snapshot()
    assert snap.contracts_by_status == {"COMPLETE": 2, "ESCALATED": 1}
    d = mc.to_dict()
    assert d["contracts_by_status"] == {"COMPLETE": 2, "ESCALATED": 1}

    path = str(tmp_path / "metrics.json")
    mc.save(path)
    loaded = MetricsCollector.load(path)
    assert loaded.contracts_by_status == {"COMPLETE": 2, "ESCALATED": 1}
    assert loaded.counters["three_strike_escalations"] == 1


@pytest.mark.fast
def test_metrics_old_file_without_contracts_by_status(tmp_path):
    old = {"goal_id": "legacy", "phases": [], "attempt_counts": {}, "counters": {}}
    path = tmp_path / "old_metrics.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    loaded = MetricsCollector.load(str(path))
    assert loaded.contracts_by_status == {}


@pytest.mark.fast
def test_goal_lifecycle_event_sequence(tmp_path, recorder):
    sup = _make_supervisor(tmp_path, "evt-ok", "Step 1 creates a file, Step 2 validates it")

    res = sup.execute_plan()

    assert all(status in ("COMPLETE", "complete") for status in res.values())
    assert recorder.wait_for(
        lambda evs: bool(_for_goal(evs, "evt-ok")) and _names(_for_goal(evs, "evt-ok"))[-1] == "goal.completed"
    )
    time.sleep(0.3)

    events = _for_goal(recorder.events, "evt-ok")
    names = _names(events)
    assert names[0] == "goal.started"
    assert names[-1] == "goal.completed"
    assert _in_order(names, ["goal.started", "contract.working", "contract.verified", "goal.completed"])
    assert "contract.failed" not in names

    step_ids = {e["task_id"] for e in events if e["event"] == "contract.working"}
    assert len(step_ids) == 2

    completed_env = events[-1]["data"]
    assert completed_env["completed"] == 2
    assert completed_env["failed"] == 0

    assert sup.metrics_coll.contracts_by_status.get("COMPLETE") == 2


@pytest.mark.fast
def test_failing_contract_emits_failed_not_qc_passed(tmp_path, monkeypatch, recorder):
    monkeypatch.setenv("FAKE_WORKER", "FAIL")
    sup = _make_supervisor(tmp_path, "evt-fail", "Step 1 creates a file")
    task_id = sup.plan.contracts[0]["task_id"]

    res = sup.execute_plan()

    assert res[task_id] == "ESCALATED"
    assert recorder.wait_for(
        lambda evs: any(
            e["event"] == "impossibility.generated" and e.get("task_id") == task_id for e in _for_goal(evs, "evt-fail")
        )
    )
    assert recorder.wait_for(
        lambda evs: bool(_for_goal(evs, "evt-fail")) and _names(_for_goal(evs, "evt-fail"))[-1] == "goal.completed"
    )
    time.sleep(0.3)

    events = _for_goal(recorder.events, "evt-fail")
    names = _names(events)
    assert "contract.failed" in names
    failed_envs = [e for e in events if e["event"] == "contract.failed"]
    assert failed_envs[-1]["task_id"] == task_id
    assert failed_envs[-1]["data"].get("reason")
    assert "contract.qc_passed" not in names
    assert "impossibility.generated" in names
    assert _in_order(names, ["goal.started", "contract.failed", "goal.completed"])

    assert sup.goal.status in ("FAILED", "failed")
    assert sup.metrics_coll.contracts_by_status.get("ESCALATED", 0) >= 1
    completed_env = events[-1]["data"]
    assert completed_env["completed"] == 0
    assert completed_env["failed"] == 1


class StubSSEServer:
    instances = []

    def __init__(self, host="127.0.0.1", port=8080, bus=None):
        self.host = host
        self.port = port
        self.bus = bus
        self.bound_port = port
        self.started = False
        self.shutdown_called = False
        StubSSEServer.instances.append(self)

    def start(self):
        self.started = True

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def serve_harness(monkeypatch):
    from orchestrator import cli as cli_mod

    StubSSEServer.instances = []
    monkeypatch.setattr("orchestrator.sse_server.SSEServer", StubSSEServer)
    attached = []
    detached = []

    def fake_attach(bus, dispatcher):
        attached.append(dispatcher)
        return lambda: detached.append(True)

    monkeypatch.setattr("orchestrator.webhooks.attach_webhooks", fake_attach)

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", boom)
    return {
        "cli": cli_mod,
        "attached": attached,
        "detached": detached,
        "servers": StubSSEServer.instances,
    }


@pytest.mark.fast
def test_serve_cli_flags_and_clean_shutdown(serve_harness, tmp_path, capsys, monkeypatch):
    wh_path = tmp_path / "webhooks.json"
    wh_path.write_text(
        json.dumps([{"url": "http://127.0.0.1:9/hook", "events": ["goal.started"]}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lil",
            "--run-dir",
            str(tmp_path / "runs"),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--webhooks-json",
            str(wh_path),
        ],
    )

    # cli.main() reassigns module-global DEFAULT_RUN_DIR from --run-dir; save and
    # restore it so later in-process CLI tests are not poisoned by our tmp dir.
    saved_run_dir = serve_harness["cli"].DEFAULT_RUN_DIR
    try:
        serve_harness["cli"].main()
    finally:
        serve_harness["cli"].DEFAULT_RUN_DIR = saved_run_dir

    assert len(serve_harness["servers"]) == 1
    server = serve_harness["servers"][0]
    assert server.host == "127.0.0.1"
    assert server.port == 0
    assert server.bus is get_bus()
    assert server.started and server.shutdown_called
    assert len(serve_harness["attached"]) == 1
    assert len(serve_harness["attached"][0].webhooks) == 1
    assert serve_harness["detached"] == [True]
    out = capsys.readouterr().out
    assert "/events" in out
    assert "[serve] shutdown complete" in out


@pytest.mark.fast
def test_serve_env_webhook_loader(serve_harness, tmp_path, monkeypatch):
    wh_path = tmp_path / "env_hooks.json"
    wh_path.write_text(json.dumps([{"url": "http://127.0.0.1:9/env"}]), encoding="utf-8")
    monkeypatch.setenv("LETITLOOP_WEBHOOKS_JSON", str(wh_path))

    serve_harness["cli"].cmd_serve(argparse.Namespace(host="localhost", port=1, webhooks_json=""))

    assert len(serve_harness["attached"]) == 1
    assert serve_harness["servers"][0].shutdown_called


@pytest.mark.fast
def test_serve_without_webhooks_skips_attach(serve_harness, monkeypatch):
    monkeypatch.delenv("LETITLOOP_WEBHOOKS_JSON", raising=False)

    serve_harness["cli"].cmd_serve(argparse.Namespace(host="127.0.0.1", port=2, webhooks_json=""))

    assert serve_harness["attached"] == []
    assert len(serve_harness["servers"]) == 1
    assert serve_harness["servers"][0].shutdown_called


@pytest.mark.fast
def test_sse_ephemeral_port_smoke():
    from orchestrator.sse_server import SSEServer

    server = SSEServer(host="127.0.0.1", port=0, bus=get_bus())
    server.start()
    try:
        assert server.bound_port
        with urllib.request.urlopen(f"http://127.0.0.1:{server.bound_port}/health", timeout=5) as resp:
            assert resp.read() == b"ok"
    finally:
        server.shutdown()
