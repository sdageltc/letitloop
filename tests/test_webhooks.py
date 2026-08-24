"""Tests for webhooks, event bus, and SSE streaming."""

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrator.events import EVENT_TYPES, EventBus
from orchestrator.sse_server import SSEServer
from orchestrator.webhooks import (
    WebhookConfig,
    WebhookDispatcher,
    attach_webhooks,
    load_webhook_configs,
    load_webhook_configs_from_env,
    sign_payload,
)


@pytest.mark.fast
def test_sign_payload_known_vectors():
    assert sign_payload(b"Hi There", "\x0b" * 20) == "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
    assert (
        sign_payload(b"what do ya want for nothing?", "Jefe")
        == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
    )


@pytest.mark.fast
def test_event_bus_async_delivery_envelope_shape():
    bus = EventBus()
    got = []
    done = threading.Event()

    def cb(envelope):
        got.append(envelope)
        done.set()

    bus.subscribe(cb)
    envelope = bus.publish("goal.started", goal_id="g1", task_id="t1", model="m1")
    assert envelope["event"] == "goal.started"
    assert envelope["goal_id"] == "g1"
    assert envelope["task_id"] == "t1"
    assert envelope["data"] == {"model": "m1"}
    assert "T" in envelope["timestamp"]
    assert done.wait(2.0)
    assert got[0] == envelope


@pytest.mark.fast
def test_event_bus_subscriber_exception_isolation():
    bus = EventBus()
    got = []
    done = threading.Event()

    def bad(envelope):
        raise ValueError("boom")

    def good(envelope):
        got.append(envelope)
        done.set()

    bus.subscribe(bad)
    bus.subscribe(good)
    bus.publish("contract.working")
    assert done.wait(2.0)


@pytest.mark.fast
def test_event_bus_unsubscribe():
    bus = EventBus()
    calls = []

    def cb(envelope):
        calls.append(envelope)

    unsub = bus.subscribe(cb)
    bus.publish("contract.failed")
    unsub()
    bus.publish("contract.failed")
    time.sleep(0.3)
    assert len(calls) == 1
    bus.clear()


@pytest.mark.fast
def test_event_bus_type_filtering():
    bus = EventBus()
    matched = []
    all_seen = []
    done = threading.Event()

    bus.subscribe(matched.append, event_type="goal.completed")
    bus.subscribe(all_seen.append)

    bus.publish("contract.working")

    def cb_wait(envelope):
        done.set()

    probe_unsub = bus.subscribe(cb_wait, event_type="goal.completed")
    bus.publish("goal.completed")
    assert done.wait(2.0)
    assert len(all_seen) == 2
    types = [e["event"] for e in matched]
    assert types == ["goal.completed"]
    probe_unsub()


@pytest.mark.integration
def test_dispatcher_end_to_end_signed_delivery():
    recorded = {}
    hit = threading.Event()

    class Recorder(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            recorded["body"] = body
            recorded["headers"] = {k.lower(): v for k, v in self.headers.items()}
            hit.set()
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        dispatcher = WebhookDispatcher([WebhookConfig(url=url, secret="s3cr3t", timeout=5.0)])
        bus = EventBus()
        detach = attach_webhooks(bus, dispatcher)
        envelope = bus.publish("goal.started", goal_id="g9", task_id="t9")
        assert hit.wait(3.0)
        body = recorded["body"]
        assert json.loads(body.decode("utf-8")) == envelope
        assert recorded["headers"]["x-letitloop-signature"] == "sha256=" + sign_payload(body, "s3cr3t")
        assert recorded["headers"]["x-letitloop-event"] == "goal.started"
        detach()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.integration
def test_dispatcher_respects_event_filter():
    recorded = []
    hit = threading.Event()

    class Recorder(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            recorded.append(self.path)
            hit.set()
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        dispatcher = WebhookDispatcher([WebhookConfig(url=url, events=["goal.completed"], timeout=5.0)])
        dispatcher.dispatch("contract.working", {"event": "contract.working"})
        assert not hit.wait(0.5)
        dispatcher.dispatch("goal.completed", {"event": "goal.completed"})
        assert hit.wait(3.0)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.integration
def test_dispatcher_unreachable_url_does_not_raise():
    dispatcher = WebhookDispatcher([WebhookConfig(url="http://127.0.0.1:9/hook", timeout=1.0)])
    dispatcher.dispatch("goal.started", {"event": "goal.started"})


@pytest.mark.integration
def test_sse_server_streams_frames():
    bus = EventBus()
    server = SSEServer(host="127.0.0.1", port=0, bus=bus)
    server.start()
    assert isinstance(server.bound_port, int) and server.bound_port > 0
    received = {}
    done = threading.Event()

    def reader():
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{server.bound_port}/events", timeout=5.0)
            lines = []
            while True:
                line = resp.readline()
                if not line or line == b"\n":
                    break
                lines.append(line)
            received["lines"] = lines
        except Exception as exc:
            received["error"] = exc
        finally:
            done.set()

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.time() + 3.0
    while server.client_count < 1 and time.time() < deadline:
        time.sleep(0.02)
    assert server.client_count == 1
    envelope = bus.publish("goal.started", goal_id="gs1", task_id="ts1")
    assert done.wait(3.0)
    assert "error" not in received
    text = b"".join(received["lines"]).decode("utf-8")
    assert text.startswith("event: goal.started\ndata: ")
    payload = json.loads(text.split("\ndata: ", 1)[1].split("\n")[0])
    assert payload["event"] == envelope["event"]
    assert payload["goal_id"] == "gs1"

    health_ok = {}

    def health_check():
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{server.bound_port}/health", timeout=5.0)
            health_ok["status"] = resp.status
            health_ok["body"] = resp.read()
        except Exception as exc:
            health_ok["error"] = exc

    ht = threading.Thread(target=health_check, daemon=True)
    ht.start()
    ht.join(5.0)
    assert health_ok.get("status") == 200
    assert health_ok.get("body") == b"ok"
    server.shutdown()
    bus.clear()


@pytest.mark.fast
def test_load_webhook_configs_happy_and_invalid(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text(
        json.dumps(
            [
                {
                    "url": "http://example.com/a",
                    "secret": "k",
                    "events": ["goal.completed"],
                    "timeout": 2.5,
                },
                {"url": "http://example.com/b"},
                {"secret": "no-url"},
                "not-a-dict",
            ]
        ),
        encoding="utf-8",
    )
    configs = load_webhook_configs(str(path))
    assert len(configs) == 2
    assert configs[0].url == "http://example.com/a"
    assert configs[0].secret == "k"
    assert configs[0].events == ["goal.completed"]
    assert configs[0].timeout == 2.5
    assert configs[1].events is None
    assert configs[1].timeout == 5.0


@pytest.mark.fast
def test_load_webhook_configs_missing_and_malformed(tmp_path):
    assert load_webhook_configs(str(tmp_path / "missing.json")) == []
    malformed = tmp_path / "bad.json"
    malformed.write_text("{not json", encoding="utf-8")
    assert load_webhook_configs(str(malformed)) == []


@pytest.mark.fast
def test_load_webhook_configs_from_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LETITLOOP_WEBHOOKS_JSON", raising=False)
    assert load_webhook_configs_from_env() == []
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps([{"url": "http://example.com/x"}]), encoding="utf-8")
    monkeypatch.setenv("LETITLOOP_WEBHOOKS_JSON", str(path))
    configs = load_webhook_configs_from_env()
    assert len(configs) == 1
    assert configs[0].url == "http://example.com/x"


@pytest.mark.fast
def test_event_types_tuple_matches_spec():
    assert EVENT_TYPES == (
        "goal.started",
        "contract.working",
        "contract.verified",
        "contract.qc_passed",
        "contract.failed",
        "impossibility.generated",
        "goal.completed",
    )
