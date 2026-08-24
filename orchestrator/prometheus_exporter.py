"""Prometheus text-format exporter over the runtime metrics layer (stdlib only).

Renders a MetricsCollector as Prometheus exposition text without requiring
prometheus-client. An optional OpenTelemetry tracing bridge is exported
(init_otel_tracing / start_span); it degrades to silent no-ops when the
opentelemetry packages are not installed and is never auto-enabled.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Any, Dict, Iterator, List, Optional

try:
    from opentelemetry import trace as _otel_trace
except ImportError:
    _otel_trace = None

_tracer: Any = None

BUCKETS: tuple = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)

_DURATION = "letitloop_contract_duration_seconds"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(extra_labels: Optional[Dict[str, str]], **base: str) -> str:
    labels: Dict[str, str] = {k: str(v) for k, v in base.items() if v}
    if extra_labels:
        labels.update({str(k): str(v) for k, v in extra_labels.items()})
    if not labels:
        return ""
    inner = ",".join(f'{key}="{_escape_label(val)}"' for key, val in sorted(labels.items()))
    return "{" + inner + "}"


def _format_value(value: float) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return repr(value)


def _record_field(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _histogram_section(records: List[Any], extra_labels: Optional[Dict[str, str]]) -> List[str]:
    lines = [
        f"# HELP {_DURATION} Contract phase durations in seconds.",
        f"# TYPE {_DURATION} histogram",
    ]
    groups: Dict[str, List[float]] = {}
    for record in records:
        phase = _record_field(record, "phase") or ""
        elapsed = _record_field(record, "elapsed_sec")
        if elapsed is None:
            continue
        groups.setdefault(str(phase), []).append(float(elapsed))
    if not groups:
        # No phase data: omit the family entirely (an unlabeled zero series
        # would be format-valid but semantically ambiguous for scrapers).
        return []
    for phase in sorted(groups):
        values = groups[phase]
        for bound in BUCKETS:
            count = sum(1 for v in values if v <= bound)
            labels = _render_labels(extra_labels, phase=phase, le=_format_value(bound))
            lines.append(f"{_DURATION}_bucket{labels} {count}")
        inf_labels = _render_labels(extra_labels, phase=phase, le="+Inf")
        lines.append(f"{_DURATION}_bucket{inf_labels} {len(values)}")
        base_labels = _render_labels(extra_labels, phase=phase)
        lines.append(f"{_DURATION}_sum{base_labels} {_format_value(sum(values))}")
        lines.append(f"{_DURATION}_count{base_labels} {len(values)}")
    return lines


def _contract_statuses(collector: Any) -> Dict[str, int]:
    by_status = getattr(collector, "contracts_by_status", None)
    if isinstance(by_status, dict) and by_status:
        out: Dict[str, int] = {}
        for key, value in by_status.items():
            status = str(key)
            out[status] = out.get(status, 0) + int(value)
        return out
    derived: Dict[str, int] = {}
    attempts = getattr(collector, "attempts", None)
    if isinstance(attempts, dict):
        for info in attempts.values():
            if isinstance(info, dict) and info.get("status") is not None:
                status = str(info["status"])
                derived[status] = derived.get(status, 0) + 1
    return derived


def _contracts_section(collector: Any, extra_labels: Optional[Dict[str, str]]) -> List[str]:
    statuses = _contract_statuses(collector)
    if not statuses:
        return []
    lines = [
        "# HELP letitloop_contracts_total Contracts by terminal status.",
        "# TYPE letitloop_contracts_total counter",
    ]
    for status in sorted(statuses):
        labels = _render_labels(extra_labels, status=status)
        lines.append(f"letitloop_contracts_total{labels} {int(statuses[status])}")
    return lines


def _budget_section(collector: Any, extra_labels: Optional[Dict[str, str]]) -> List[str]:
    usage = getattr(collector, "token_usage", None) or {}
    prompt = int(usage.get("prompt", 0))
    completion = int(usage.get("completion", 0))
    return [
        "# HELP letitloop_budget_tokens_total LLM tokens consumed by type.",
        "# TYPE letitloop_budget_tokens_total counter",
        f"letitloop_budget_tokens_total{_render_labels(extra_labels, type='prompt')} {prompt}",
        f"letitloop_budget_tokens_total{_render_labels(extra_labels, type='completion')} {completion}",
    ]


def _escalations_section(collector: Any, extra_labels: Optional[Dict[str, str]]) -> List[str]:
    counters = getattr(collector, "counters", None) or {}
    escalations = int(counters.get("three_strike_escalations", 0))
    return [
        "# HELP letitloop_three_strike_escalations_total Three-strike escalation events.",
        "# TYPE letitloop_three_strike_escalations_total counter",
        f"letitloop_three_strike_escalations_total{_render_labels(extra_labels)} {escalations}",
    ]


def render_prometheus(collector: Any, extra_labels: Optional[Dict[str, str]] = None) -> str:
    """Render collector state as Prometheus exposition text."""
    records = list(getattr(collector, "phases", None) or [])
    sections = [
        _histogram_section(records, extra_labels),
        _contracts_section(collector, extra_labels),
        _budget_section(collector, extra_labels),
        _escalations_section(collector, extra_labels),
    ]
    body = "\n".join("\n".join(section) for section in sections if section)
    return body + "\n"


def write_prometheus_file(collector: Any, path: str) -> None:
    """Atomically write exposition text to path (tmp file + os.replace)."""
    text = render_prometheus(collector)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent or ".", prefix=".prom_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def init_otel_tracing(endpoint: Optional[str] = None) -> Any:
    """Best-effort OpenTelemetry tracer setup.

    Returns a tracer, or None when opentelemetry is unavailable. The optional
    OTLP HTTP exporter is attached when endpoint is given and installed;
    any failure degrades silently to a provider without an exporter.
    """
    global _tracer
    if _otel_trace is None:
        return None
    if _tracer is not None:
        return _tracer
    try:
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            except ImportError:
                pass
        _otel_trace.set_tracer_provider(provider)
        _tracer = provider.get_tracer("letitloop")
    except Exception:
        return None
    return _tracer


@contextlib.contextmanager
def start_span(name: str, attributes: Optional[Dict[str, Any]] = None) -> Iterator[Any]:
    """Context manager yielding an OTel span, or None as a no-op when tracing is off."""
    tracer = _tracer
    if tracer is None or _otel_trace is None:
        yield None
        return
    with tracer.start_as_current_span(name, attributes=dict(attributes or {})) as span:
        yield span
