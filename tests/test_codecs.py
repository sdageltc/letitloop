"""tests/test_codecs.py — Unit tests for DurableCodecRegistry and type preservation."""

import dataclasses
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from orchestrator.codecs import (
    DurableCodecError,
    DurableCodecRegistry,
    decode_step_result,
    encode_step_result,
)
from orchestrator.decorators import durable, step


@dataclasses.dataclass
class Coordinate:
    x: float
    y: float


def test_builtin_codecs_roundtrip():
    """Verify built-in type codecs preserve concrete types and identity."""
    reg = DurableCodecRegistry()

    # Datetime
    now = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
    enc_dt = reg.encode(now)
    assert enc_dt == {"__lil_type__": "datetime", "v": "2026-10-12T12:00:00+00:00"}
    dec_dt = reg.decode(enc_dt)
    assert dec_dt == now
    assert isinstance(dec_dt, datetime)

    # UUID
    u = uuid.uuid4()
    enc_u = reg.encode(u)
    assert enc_u == {"__lil_type__": "uuid", "v": str(u)}
    dec_u = reg.decode(enc_u)
    assert dec_u == u
    assert isinstance(dec_u, uuid.UUID)

    # Path
    p = Path("data/raw/sample.csv")
    enc_p = reg.encode(p)
    assert enc_p == {"__lil_type__": "path", "v": str(p)}
    dec_p = reg.decode(enc_p)
    assert dec_p == p
    assert isinstance(dec_p, Path)

    # Tuple
    t = (1, "a", True)
    enc_t = reg.encode(t)
    assert enc_t == {"__lil_type__": "tuple", "v": [1, "a", True]}
    dec_t = reg.decode(enc_t)
    assert dec_t == t
    assert isinstance(dec_t, tuple)


def test_custom_codec_registration():
    """Verify custom codecs can be registered and hydrated."""
    reg = DurableCodecRegistry()
    reg.register_codec(
        cls=Coordinate,
        type_tag="coord",
        encode_fn=lambda c: [c.x, c.y],
        decode_fn=lambda pair: Coordinate(x=pair[0], y=pair[1]),
    )

    c = Coordinate(12.5, 45.2)
    enc = reg.encode(c)
    assert enc == {"__lil_type__": "coord", "v": [12.5, 45.2]}

    dec = reg.decode(enc)
    assert dec == c
    assert isinstance(dec, Coordinate)


def test_nested_container_hydration():
    """Verify nested structures with multiple types hydrate properly."""
    u = uuid.uuid4()
    dt = datetime(2026, 1, 1, 0, 0, 0)
    data = {
        "user_id": u,
        "timestamps": [dt],
        "tags": ("alpha", "beta"),
        "path": Path("output/log.txt"),
    }

    enc = encode_step_result(data)
    dec = decode_step_result(enc)

    assert dec["user_id"] == u
    assert isinstance(dec["user_id"], uuid.UUID)
    assert dec["timestamps"][0] == dt
    assert isinstance(dec["timestamps"][0], datetime)
    assert dec["tags"] == ("alpha", "beta")
    assert isinstance(dec["tags"], tuple)
    assert dec["path"] == Path("output/log.txt")
    assert isinstance(dec["path"], Path)


def test_durable_workflow_type_preservation():
    """Verify @durable and step() preserve types across first-run and WAL replay."""
    temp_dir = tempfile.mkdtemp(prefix="lil_durable_codecs_")
    u = uuid.uuid4()
    now = datetime(2026, 10, 12, 10, 30, 0)
    p = Path("records/file.txt")

    try:

        @durable(goal_id="type_preservation_test", wal_dir=temp_dir)
        def my_workflow():
            val1 = step("generate_uuid", lambda: u)
            val2 = step("generate_time", lambda: now)
            val3 = step("generate_path", lambda: p)
            return {"u": val1, "t": val2, "p": val3}

        # First run: values must match types
        out1 = my_workflow()
        assert out1["u"] == u
        assert isinstance(out1["u"], uuid.UUID)
        assert out1["t"] == now
        assert isinstance(out1["t"], datetime)
        assert out1["p"] == p
        assert isinstance(out1["p"], Path)

        # Second run (replayed from WAL): must yield exact same hydrated types
        out2 = my_workflow()
        assert out2["u"] == u
        assert isinstance(out2["u"], uuid.UUID)
        assert out2["t"] == now
        assert isinstance(out2["t"], datetime)
        assert out2["p"] == p
        assert isinstance(out2["p"], Path)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_unsupported_type_fail_closed():
    """Verify that unsupported objects fail closed with DurableCodecError."""

    class CustomNonSerializable:
        pass

    with pytest.raises(DurableCodecError):
        encode_step_result(CustomNonSerializable())


def test_circular_reference_fail_closed():
    """Verify that circular references in lists, dicts, and tuples fail closed with DurableCodecError."""
    # Circular list
    circular_list = [1, 2]
    circular_list.append(circular_list)
    with pytest.raises(DurableCodecError, match="Circular reference detected"):
        encode_step_result(circular_list)

    # Circular dict
    circular_dict = {"a": 1}
    circular_dict["self"] = circular_dict
    with pytest.raises(DurableCodecError, match="Circular reference detected"):
        encode_step_result(circular_dict)


def test_malformed_tagged_payloads_fail_closed():
    """Verify that corrupted tagged payloads in decode fail closed with DurableCodecError."""
    reg = DurableCodecRegistry()

    # Malformed tuple: raw 'v' is an integer instead of iterable
    with pytest.raises(DurableCodecError, match="Corrupted tuple payload"):
        reg.decode({"__lil_type__": "tuple", "v": 12345})

    # Malformed datetime: invalid isoformat string
    with pytest.raises(DurableCodecError, match="Failed to hydrate tagged 'datetime' payload"):
        reg.decode({"__lil_type__": "datetime", "v": "NOT_A_VALID_DATETIME"})

    # Malformed uuid: invalid hex
    with pytest.raises(DurableCodecError, match="Failed to hydrate tagged 'uuid' payload"):
        reg.decode({"__lil_type__": "uuid", "v": "not-a-valid-uuid-string"})

    # Malformed path: integer instead of string
    with pytest.raises(DurableCodecError, match="Failed to hydrate tagged 'path' payload"):
        reg.decode({"__lil_type__": "path", "v": 12345})


def test_deep_nesting_depth_guard():
    """Verify that excessively deep payload nesting fails closed with DurableCodecError."""
    reg = DurableCodecRegistry()

    # Create 300-level nested list
    deep_list = [1]
    for _ in range(300):
        deep_list = [deep_list]

    with pytest.raises(DurableCodecError, match="Maximum encoding depth"):
        reg.encode(deep_list)

    with pytest.raises(DurableCodecError, match="Maximum decoding depth"):
        reg.decode(deep_list)


def test_custom_codec_with_nested_builtin_types_roundtrip():
    """Custom codec returning nested datetime/UUID/Path structures must recursively encode and hydrate."""

    @dataclasses.dataclass
    class EventRecord:
        event_id: uuid.UUID
        created_at: datetime
        artifact: Path

    reg = DurableCodecRegistry()
    reg.register_codec(
        cls=EventRecord,
        type_tag="event_rec",
        encode_fn=lambda e: {"id": e.event_id, "ts": e.created_at, "path": e.artifact},
        decode_fn=lambda d: EventRecord(event_id=d["id"], created_at=d["ts"], artifact=d["path"]),
    )

    rec = EventRecord(
        event_id=uuid.uuid4(),
        created_at=datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc),
        artifact=Path("logs/trace.jsonl"),
    )
    payload = encode_step_result(rec, registry=reg)
    hydrated = decode_step_result(payload, registry=reg)
    assert hydrated == rec
    assert isinstance(hydrated, EventRecord)
    assert isinstance(hydrated.event_id, uuid.UUID)
    assert isinstance(hydrated.created_at, datetime)
    assert isinstance(hydrated.artifact, Path)
