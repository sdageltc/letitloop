"""LILWAL02 checksummed WAL v2 — roundtrip, torn-tail truncate, CRC, and legacy compat."""

import json
import os
import zlib

import pytest

from orchestrator.state import (
    StateError,
    _wal_frame_encode,
    create_initial_state,
    load_state,
    save_state,
)

pytestmark = pytest.mark.fast


def _read_wal_raw(wal_path: str) -> bytes:
    with open(wal_path, "rb") as f:
        return f.read()


def _decode_frame_line(line: str):
    """Helper mirroring _wal_decode_line for test assertions."""
    assert line.startswith("LILWAL02:")
    _, length_hex, crc_hex, payload = line.split(":", 3)
    payload_bytes = payload.encode("utf-8")
    assert len(payload_bytes) == int(length_hex, 16)
    assert (zlib.crc32(payload_bytes) & 0xFFFFFFFF) == int(crc_hex, 16)
    return json.loads(payload)


class TestLilWal02Framing:
    def test_wal_frames_written_in_lilwal02_format(self, tmp_path):
        td = str(tmp_path / "t_frame")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_frame", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="a")
        st.transition("READY", reason="b")
        wal = os.path.join(td, "state.wal.jsonl")
        raw = _read_wal_raw(wal)
        text = raw.decode("utf-8")
        frames = [ln.strip() for ln in text.splitlines() if ln.strip()]
        assert len(frames) >= 3  # INIT + 2 transitions
        for ln in frames:
            assert ln.startswith("LILWAL02:"), f"expected LILWAL02 frame, got {ln[:40]!r}"
            _decode_frame_line(ln)

    def test_roundtrip_recovery(self, tmp_path):
        td = str(tmp_path / "t_roundtrip")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_roundtrip", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="r1")
        st.transition("READY", reason="r2")
        st.transition("WORKING", reason="r3")
        snap = os.path.join(td, "state.json")
        save_state(st, snap)
        loaded = load_state(snap, journal_dir=td)
        assert loaded.status == "WORKING"
        assert loaded.task_id == "t_roundtrip"
        assert len(loaded.events) == len(st.events)
        # file still framed after load (no truncation on clean file)
        raw = _read_wal_raw(os.path.join(td, "state.wal.jsonl"))
        assert b"LILWAL02:" in raw

    def test_torn_frame_truncated_on_load(self, tmp_path):
        td = str(tmp_path / "t_torn")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_torn", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="a")
        st.transition("READY", reason="b")
        snap = os.path.join(td, "state.json")
        save_state(st, snap)
        wal = os.path.join(td, "state.wal.jsonl")
        before = _read_wal_raw(wal)
        # append torn partial frame (half of a valid frame, no trailing newline)
        valid_frame = _wal_frame_encode({"seq": 999, "payload": "x"})
        torn = valid_frame[: len(valid_frame) // 2]
        with open(wal, "ab") as f:
            f.write(torn.encode("utf-8"))
        # load must recover valid prefix, truncate torn tail, set audit flag
        loaded = load_state(snap, journal_dir=td)
        assert loaded.status == "READY"
        assert loaded.data.get("wal_torn_tail_recovered") is True
        after = _read_wal_raw(wal)
        assert after == before  # torn bytes removed
        assert torn.encode("utf-8") not in after

    def test_crc_mismatch_tail_truncated(self, tmp_path):
        td = str(tmp_path / "t_crc_tail")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_crc_tail", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="a")
        st.transition("READY", reason="b")
        snap = os.path.join(td, "state.json")
        save_state(st, snap)
        wal = os.path.join(td, "state.wal.jsonl")
        with open(wal, "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        # corrupt last frame's payload byte (keep length/crc header stale to force mismatch)
        last = lines[-1]
        assert last.startswith("LILWAL02:")
        # proper split: LILWAL02:length:crc:payload
        _, lh, ch, pl = last.split(":", 3)
        # flip one char inside payload
        pl_corrupt = pl[:-2] + ("X" if pl[-2] != "X" else "Y") + pl[-1:]
        corrupt_line = f"LILWAL02:{lh}:{ch}:{pl_corrupt}"
        with open(wal, "w", encoding="utf-8") as f:
            for ln in lines[:-1]:
                f.write(ln + "\n")
            f.write(corrupt_line + "\n")
        loaded = load_state(snap, journal_dir=td)
        # tail corruption truncated -> status rolls back to previous valid frame's state
        assert loaded.data.get("wal_torn_tail_recovered") is True
        assert loaded.status == "PREFLIGHT_RUNNING"  # last valid is one before corrupt tail
        # file was truncated to last good offset
        after_text = open(wal, "r", encoding="utf-8").read()
        assert corrupt_line not in after_text
        assert "LILWAL02:" in after_text

    def test_crc_mismatch_mid_file_fails_closed(self, tmp_path):
        td = str(tmp_path / "t_crc_mid")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_crc_mid", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="a")
        st.transition("READY", reason="b")
        st.transition("WORKING", reason="c")
        snap = os.path.join(td, "state.json")
        save_state(st, snap)
        wal = os.path.join(td, "state.wal.jsonl")
        with open(wal, "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        assert len(lines) >= 4
        # corrupt a MIDDLE frame (index 1), keep header stale -> CRC mismatch mid-file must fail closed
        mid_idx = 1
        _, lh, ch, pl = lines[mid_idx].split(":", 3)
        pl_corrupt = pl[:-2] + ("X" if pl[-2] != "X" else "Y") + pl[-1:]
        lines[mid_idx] = f"LILWAL02:{lh}:{ch}:{pl_corrupt}"
        with open(wal, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
        with pytest.raises(StateError, match="CRC mismatch|corrupt"):
            load_state(snap, journal_dir=td)

    def test_legacy_plain_jsonl_still_loads(self, tmp_path):
        td = str(tmp_path / "t_legacy")
        os.makedirs(td, exist_ok=True)
        snap = os.path.join(td, "state.json")
        wal = os.path.join(td, "state.wal.jsonl")
        # hand-write legacy plain JSONL WAL (no LILWAL02 headers) with a valid chain

        from orchestrator.state import _canonical, _event_hash

        def _mk(seq, typ, payload, prev):
            ev = {
                "seq": seq,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "event_type": typ,
                "task_id": "t_legacy",
                "prev_hash": prev,
                "payload": payload,
            }
            ev["event_hash"] = _event_hash(ev)
            return ev

        ev1 = _mk(
            1,
            "INIT",
            {
                "status": "DRAFTED",
                "attempt": 1,
                "changed_approaches": [],
                "evidence": {},
                "worker_results": [],
                "data": {},
            },
            "",
        )
        ev2 = {
            "seq": 2,
            "timestamp": "2026-01-01T00:00:01+00:00",
            "event_type": "TRANSITION",
            "task_id": "t_legacy",
            "prev_hash": ev1["event_hash"],
            "payload": {"from": "DRAFTED", "to": "PREFLIGHT_RUNNING"},
        }
        ev2["event_hash"] = _event_hash(ev2)
        with open(wal, "w", encoding="utf-8") as f:
            f.write(_canonical(ev1) + "\n")
            f.write(_canonical(ev2) + "\n")
        # snapshot is minimal drafted; WAL drives recovery
        snap_state = create_initial_state("t_legacy", journal_dir=td)
        # overwrite WAL we just created? create_initial_state already wrote framed WAL; replace with legacy
        with open(wal, "w", encoding="utf-8") as f:
            f.write(_canonical(ev1) + "\n")
            f.write(_canonical(ev2) + "\n")
        # Need a snapshot file on disk; use the one from snap_state
        save_state(snap_state, snap)
        # Replace WAL again with legacy after save (save doesn't touch WAL)
        with open(wal, "w", encoding="utf-8") as f:
            f.write(_canonical(ev1) + "\n")
            f.write(_canonical(ev2) + "\n")
        loaded = load_state(snap, journal_dir=td)
        assert loaded.status == "PREFLIGHT_RUNNING"
        assert loaded.task_id == "t_legacy"

    def test_mixed_legacy_and_frames(self, tmp_path):
        td = str(tmp_path / "t_mixed")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_mixed", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="a")
        snap = os.path.join(td, "state.json")
        save_state(st, snap)
        wal = os.path.join(td, "state.wal.jsonl")
        # append a legacy line manually after framed lines
        from orchestrator.state import _canonical, _event_hash

        with open(wal, "r", encoding="utf-8") as f:
            existing = [ln.rstrip("\n") for ln in f if ln.strip()]
        # decode last event to get prev_hash/seq
        last_payload = json.loads(existing[-1].split(":", 3)[3])
        last_hash = last_payload["event_hash"]
        last_seq = last_payload["seq"]
        nxt = {
            "seq": last_seq + 1,
            "timestamp": "2026-01-01T00:00:02+00:00",
            "event_type": "TRANSITION",
            "task_id": "t_mixed",
            "prev_hash": last_hash,
            "payload": {"from": "PREFLIGHT_RUNNING", "to": "READY"},
        }
        nxt["event_hash"] = _event_hash(nxt)
        with open(wal, "a", encoding="utf-8") as f:
            f.write(_canonical(nxt) + "\n")
        loaded = load_state(snap, journal_dir=td)
        assert loaded.status == "READY"

    def test_length_hex_mismatch_fails_or_truncates_tail(self, tmp_path):
        td = str(tmp_path / "t_len")
        os.makedirs(td, exist_ok=True)
        st = create_initial_state("t_len", journal_dir=td)
        st.transition("PREFLIGHT_RUNNING", reason="a")
        snap = os.path.join(td, "state.json")
        save_state(st, snap)
        wal = os.path.join(td, "state.wal.jsonl")
        with open(wal, "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        _, lh, ch, pl = lines[-1].split(":", 3)
        corrupt = f"LILWAL02:0:{ch}:{pl}"  # zero length -> mismatch
        with open(wal, "w", encoding="utf-8") as f:
            for ln in lines[:-1]:
                f.write(ln + "\n")
            f.write(corrupt + "\n")
        loaded = load_state(snap, journal_dir=td)
        assert loaded.data.get("wal_torn_tail_recovered") is True
