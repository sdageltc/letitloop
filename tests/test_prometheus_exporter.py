"""Tests for the Prometheus text-format exporter and MetricsCollector counter extensions."""

import importlib.util
import json

import pytest

import orchestrator.prometheus_exporter as pe
from orchestrator.metrics import MetricsCollector, PhaseRecord
from orchestrator.prometheus_exporter import render_prometheus, write_prometheus_file

pytestmark = pytest.mark.fast

HAS_OTEL = importlib.util.find_spec("opentelemetry") is not None

BUCKET_BOUNDS = ["0.1", "0.25", "0.5", "1", "2.5", "5", "10", "30", "60"]


def _sample_map(text):
    samples = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name_labels, _, value = line.rpartition(" ")
        samples.setdefault(name_labels, []).append(float(value))
    return samples


def _header_lines(text):
    return [line for line in text.splitlines() if line.startswith("#")]


def _populated_collector():
    mc = MetricsCollector("g1")
    mc.phases.append(PhaseRecord(phase="plan", task_id="t0", elapsed_sec=0.05))
    mc.phases.append(PhaseRecord(phase="verify", task_id="t1", elapsed_sec=0.3))
    mc.phases.append(PhaseRecord(phase="verify", task_id="t2", elapsed_sec=2.0))
    mc.record_attempt("t1")
    mc.record_three_strike_escalation()
    mc.record_token_usage(100, 40)
    return mc


def test_histogram_headers_buckets_sum_count():
    out = render_prometheus(_populated_collector())
    headers = "\n".join(_header_lines(out))
    assert "# HELP letitloop_contract_duration_seconds" in headers
    assert "# TYPE letitloop_contract_duration_seconds histogram" in headers
    samples = _sample_map(out)
    for bound in BUCKET_BOUNDS:
        key = f'letitloop_contract_duration_seconds_bucket{{le="{bound}",phase="verify"}}'
        assert key in samples, f"missing bucket {bound}"
    inf_key = 'letitloop_contract_duration_seconds_bucket{le="+Inf",phase="verify"}'
    assert samples[inf_key] == [2.0]
    assert samples['letitloop_contract_duration_seconds_bucket{le="0.25",phase="verify"}'] == [0.0]
    assert samples['letitloop_contract_duration_seconds_bucket{le="0.5",phase="verify"}'] == [1.0]
    assert samples['letitloop_contract_duration_seconds_bucket{le="2.5",phase="verify"}'] == [2.0]
    assert samples['letitloop_contract_duration_seconds_count{phase="verify"}'] == [2.0]
    assert samples['letitloop_contract_duration_seconds_sum{phase="verify"}'] == [pytest.approx(2.3)]
    assert samples['letitloop_contract_duration_seconds_count{phase="plan"}'] == [1.0]
    assert samples['letitloop_contract_duration_seconds_bucket{le="0.1",phase="plan"}'] == [1.0]


def test_contracts_total_per_status():
    mc = _populated_collector()
    mc.contracts_by_status = {"completed": 2, "failed": 1}
    out = render_prometheus(mc)
    headers = "\n".join(_header_lines(out))
    assert "# TYPE letitloop_contracts_total counter" in headers
    samples = _sample_map(out)
    assert samples['letitloop_contracts_total{status="completed"}'] == [2.0]
    assert samples['letitloop_contracts_total{status="failed"}'] == [1.0]


def test_contracts_total_omitted_without_data():
    out = render_prometheus(MetricsCollector("g-empty"))
    assert "letitloop_contracts_total" not in out


def test_budget_tokens_counters():
    out = render_prometheus(_populated_collector())
    headers = "\n".join(_header_lines(out))
    assert "# TYPE letitloop_budget_tokens_total counter" in headers
    samples = _sample_map(out)
    assert samples['letitloop_budget_tokens_total{type="prompt"}'] == [100.0]
    assert samples['letitloop_budget_tokens_total{type="completion"}'] == [40.0]


def test_three_strike_escalations_counter():
    out = render_prometheus(_populated_collector())
    headers = "\n".join(_header_lines(out))
    assert "# TYPE letitloop_three_strike_escalations_total counter" in headers
    samples = _sample_map(out)
    assert samples["letitloop_three_strike_escalations_total"] == [1.0]


def test_empty_collector_renders_valid_output():
    out = render_prometheus(MetricsCollector())
    assert out.endswith("\n")
    headers = "\n".join(_header_lines(out))
    # Histogram family is omitted entirely when there are no phase records.
    assert "# HELP letitloop_contract_duration_seconds" not in headers
    assert "letitloop_contract_duration_seconds" not in out
    samples = _sample_map(out)
    assert samples['letitloop_budget_tokens_total{type="prompt"}'] == [0.0]
    assert samples["letitloop_three_strike_escalations_total"] == [0.0]


def test_extra_labels_propagate():
    out = render_prometheus(_populated_collector(), extra_labels={"env": "test"})
    assert 'env="test"' in out
    samples = _sample_map(out)
    assert samples['letitloop_budget_tokens_total{env="test",type="prompt"}'] == [100.0]
    assert samples['letitloop_three_strike_escalations_total{env="test"}'] == [1.0]


def test_write_prometheus_file_roundtrip(tmp_path):
    mc = _populated_collector()
    target = str(tmp_path / "metrics.prom")
    write_prometheus_file(mc, target)
    with open(target, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert content == render_prometheus(mc)
    leftovers = [p for p in tmp_path.iterdir() if p.name != "metrics.prom"]
    assert leftovers == []


def test_load_old_metrics_json_without_new_keys(tmp_path):
    old = {
        "goal_id": "g-old",
        "total_elapsed_sec": 1.0,
        "phase_elapsed": {"p": 1.0},
        "phase_counts": {"p": 1},
        "attempt_counts": {"t": 2},
        "total_attempts": 2,
        "phases": [{"phase": "p", "task_id": "", "elapsed_sec": 1.0, "timestamp": "2026-01-01T00:00:00"}],
    }
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    mc = MetricsCollector.load(str(path))
    assert dict(mc.counters) == {}
    assert mc.token_usage == {"prompt": 0, "completion": 0}
    out = render_prometheus(mc)
    samples = _sample_map(out)
    assert samples['letitloop_contract_duration_seconds_count{phase="p"}'] == [1.0]
    assert samples['letitloop_budget_tokens_total{type="prompt"}'] == [0.0]
    assert samples["letitloop_three_strike_escalations_total"] == [0.0]


@pytest.mark.skipif(HAS_OTEL, reason="opentelemetry is installed")
def test_init_otel_tracing_returns_none_when_missing():
    assert pe.init_otel_tracing("http://127.0.0.1:4318") is None


def test_init_otel_tracing_none_when_module_absent(monkeypatch):
    monkeypatch.setattr(pe, "_otel_trace", None, raising=False)
    monkeypatch.setattr(pe, "_tracer", None, raising=False)
    assert pe.init_otel_tracing("http://127.0.0.1:4318") is None
    assert pe._tracer is None


def test_start_span_noop_context_manager(monkeypatch):
    monkeypatch.setattr(pe, "_otel_trace", None, raising=False)
    monkeypatch.setattr(pe, "_tracer", None, raising=False)
    with pe.start_span("unit-op", {"key": "value"}) as span:
        assert span is None


def test_incr_counters_and_to_dict():
    mc = MetricsCollector("g1")
    mc.incr("foo")
    mc.incr("foo")
    mc.incr("bar", amount=5)
    mc.incr("labeled", task_id="t1", status="ok")
    assert mc.counters["foo"] == 2
    assert mc.counters["bar"] == 5
    assert mc.counters["labeled"] == 1
    d = mc.to_dict()
    assert d["counters"]["foo"] == 2
    snap = mc.snapshot()
    assert snap.counters["bar"] == 5


def test_record_token_usage_accumulates():
    mc = MetricsCollector("g1")
    mc.record_token_usage(10, 5)
    mc.record_token_usage(1, 1)
    assert mc.token_usage == {"prompt": 11, "completion": 6}
    snap = mc.snapshot()
    assert snap.token_usage == {"prompt": 11, "completion": 6}


def test_record_three_strike_escalation():
    mc = MetricsCollector("g1")
    mc.record_three_strike_escalation()
    mc.record_three_strike_escalation()
    assert mc.counters["three_strike_escalations"] == 2


def test_save_load_roundtrip_with_counters(tmp_path):
    mc = _populated_collector()
    path = str(tmp_path / "metrics.json")
    mc.save(path)
    loaded = MetricsCollector.load(path)
    assert dict(loaded.counters) == dict(mc.counters)
    assert loaded.token_usage == mc.token_usage
    assert loaded.attempts == mc.attempts
