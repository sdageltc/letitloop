"""Event-sourced task state with append-only signed WAL, hash chain, replay.

Phase 0.5: all state changes are events appended to a WAL with
monotonic sequence numbers and a SHA-256 hash chain; current state is a
derived projection. Snapshots are atomic (tmp + os.replace) and replay
restores attempt/evidence/worker_results/data from the WAL.

Public API is backward-compatible with the pre-0.5 snapshot state store:
- State methods: transition, force_complete, record_approach, increment_attempt,
  add_evidence, add_worker_result, add_retry_metadata, pause, cancel,
  legal_transitions, is_terminal, can_resume, set_journal_dir,
  recover_from_journal, to_dict, from_dict, qc_was_executed.
- Module functions: create_initial_state, load_state, replay_wal,
  recover_from_journal, save_state.
- state.events remains a list of transition-style dicts (from/to/reason) so
  existing consumers (handoff, cli) keep working; the WAL carries the full
  event-sourced records with seq/hash chain.
"""

import copy
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict

from .exceptions import IllegalTransitionError, StateError

JOURNAL_FILENAME = "state.journal.jsonl"
WAL_FILENAME = "state.wal.jsonl"
SNAPSHOT_FILENAME = "state.json"
STATE_SCHEMA_VERSION = 2

STATES = frozenset(
    {
        "DRAFTED",
        "PREFLIGHT_RUNNING",
        "PREFLIGHT_FAILED",
        "BLOCKED",
        "READY",
        "WORKING",
        "VERIFYING",
        "VERIFICATION_FAILED",
        "RETRY_PENDING",
        "CRASHED",
        "ESCALATED",
        "VERIFIED",
        "QC_RUNNING",
        "QC_REJECTED",
        "QC_INSUFFICIENT_EVIDENCE",
        "QC_CONDITIONAL_PASS",
        "QC_PASSED",
        "COMPLETE",
        "FORCE_COMPLETE",
        "DEGRADED_PASS",
        "PAUSED",
        "CANCELLED",
    }
)

TERMINAL_STATES = frozenset({"COMPLETE", "FORCE_COMPLETE", "DEGRADED_PASS", "ESCALATED", "BLOCKED", "CANCELLED"})

LEGAL_TRANSITIONS = {
    "DRAFTED": {"PREFLIGHT_RUNNING", "RETRY_PENDING", "CANCELLED"},
    "PREFLIGHT_RUNNING": {"PREFLIGHT_FAILED", "READY", "PAUSED", "CANCELLED"},
    "PREFLIGHT_FAILED": {"BLOCKED", "PREFLIGHT_RUNNING", "CANCELLED"},
    "BLOCKED": {"PREFLIGHT_RUNNING", "DRAFTED", "CANCELLED"},
    "READY": {"WORKING", "PAUSED", "CANCELLED"},
    "WORKING": {"VERIFYING", "CRASHED", "PAUSED", "CANCELLED"},
    "VERIFYING": {"VERIFICATION_FAILED", "VERIFIED", "PAUSED", "CANCELLED"},
    "VERIFICATION_FAILED": {"RETRY_PENDING", "FORCE_COMPLETE", "ESCALATED", "CANCELLED"},
    "RETRY_PENDING": {"WORKING", "ESCALATED", "QC_REJECTED", "PAUSED", "CANCELLED"},
    "CRASHED": {"RETRY_PENDING", "DRAFTED", "CANCELLED"},
    "VERIFIED": {"QC_RUNNING", "COMPLETE", "CANCELLED"},
    "QC_RUNNING": {
        "QC_REJECTED",
        "QC_INSUFFICIENT_EVIDENCE",
        "QC_CONDITIONAL_PASS",
        "QC_PASSED",
        "PAUSED",
        "CANCELLED",
    },
    "QC_REJECTED": {"RETRY_PENDING", "FORCE_COMPLETE", "ESCALATED", "CANCELLED"},
    "QC_INSUFFICIENT_EVIDENCE": {"RETRY_PENDING", "FORCE_COMPLETE", "ESCALATED", "CANCELLED"},
    "QC_CONDITIONAL_PASS": {"RETRY_PENDING", "FORCE_COMPLETE", "ESCALATED", "CANCELLED"},
    "QC_PASSED": {"COMPLETE", "DEGRADED_PASS", "CANCELLED"},
    "PAUSED": {"READY", "DRAFTED", "CANCELLED"},
    "ESCALATED": set(),
    "COMPLETE": set(),
    "FORCE_COMPLETE": set(),
    "DEGRADED_PASS": set(),
    "CANCELLED": set(),
}

_DELETE_SENTINEL = {"__delete__": True}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _event_hash(event: Dict[str, Any]) -> str:
    core = dict(event)
    core.pop("event_hash", None)
    return _sha256_text(_canonical(core))


def _fsync_dir(path: str) -> None:
    """Best-effort directory fsync (POSIX only; Windows handles via file fsync)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_jsonable(payload: Any) -> None:
    """Reject non-JSON-serializable payloads (objects/functions)."""
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise StateError(f"event payload not JSON-serializable: {e}") from e


class State:
    """Event-sourced task state.

    All mutations emit an event to the WAL (append + fsync) and then apply
    it in memory. The snapshot is a derived projection written atomically.

    Attributes:
        task_id: str
        status: str (current state name)
        attempt: int (current retry attempt, 1-based)
        changed_approaches: list[str] — recorded approach descriptions per retry
        events: list[dict] — transition-style entries (from/to/reason), derived
        evidence: dict — keyed by event/check id, value = path to evidence file
        worker_results: list[dict] — past worker runs
        data: dict — extensible metadata
        _journal_dir: str or None — directory for WAL + journal
        _seq: int — last applied WAL sequence number
        _hash_head: str — hash of last applied WAL event
    """

    def __init__(
        self,
        task_id,
        status="DRAFTED",
        attempt=1,
        changed_approaches=None,
        events=None,
        evidence=None,
        worker_results=None,
        data=None,
        journal_dir=None,
        *,
        seq=0,
        hash_head="",
        schema_version=STATE_SCHEMA_VERSION,
    ):
        if status not in STATES and status is not None:
            raise StateError(f"invalid state: {status}")
        if not isinstance(attempt, int) or attempt < 1:
            raise StateError(f"attempt must be int >= 1, got {attempt!r}")
        self.task_id = task_id
        self.status = status
        self.attempt = attempt
        self.changed_approaches = list(changed_approaches or [])
        self.events = list(events or [])
        self.evidence = dict(evidence or {})
        self.worker_results = list(worker_results or [])
        self.data = dict(data or {})
        self._journal_dir = journal_dir
        self._seq = int(seq)
        self._hash_head = hash_head
        self._schema_version = schema_version

    # ------------------------------------------------------------------
    # WAL plumbing
    # ------------------------------------------------------------------

    @property
    def wal_path(self):
        if not self._journal_dir:
            raise StateError("journal_dir not set")
        return os.path.join(self._journal_dir, WAL_FILENAME)

    @property
    def journal_path(self):
        if not self._journal_dir:
            raise StateError("journal_dir not set")
        return os.path.join(self._journal_dir, JOURNAL_FILENAME)

    def set_journal_dir(self, journal_dir):
        """Set the directory for WAL/journal persistence."""
        self._journal_dir = journal_dir

    def _build_event(self, event_type, payload, reason=""):
        """Build a full WAL event with seq, prev_hash and self-hash."""
        _validate_jsonable(payload)
        seq = self._seq + 1
        event = {
            "seq": seq,
            "timestamp": _now(),
            "event_type": event_type,
            "task_id": self.task_id,
            "prev_hash": self._hash_head,
            "payload": payload,
        }
        if reason:
            event["reason"] = reason
        event["event_hash"] = _event_hash(event)
        return event

    def _append_wal(self, event):
        """Append + fsync one event to the WAL. Fail-closed on I/O error.

        When no journal_dir is set the state is in-memory only: the event is
        not persisted (matching the pre-0.5 ephemeral semantic), but the
        in-memory projection still applies.
        """
        if not self._journal_dir:
            return
        try:
            os.makedirs(self._journal_dir, exist_ok=True)
            with open(self.wal_path, "a", encoding="utf-8") as f:
                f.write(_canonical(event) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise StateError(f"failed to append WAL: {e}") from e

    def _append_journal(self, event):
        """Append + fsync one event to the audit journal (best-effort)."""
        if not self._journal_dir:
            return
        try:
            os.makedirs(self._journal_dir, exist_ok=True)
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(_canonical(event) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise StateError(f"failed to append journal: {e}") from e

    # ------------------------------------------------------------------
    # Event application (replay + live)
    # ------------------------------------------------------------------

    def _apply_event(self, event, *, replay=False):
        """Verify chain/sequence/hash and apply one event to in-memory state."""
        if not isinstance(event, dict):
            raise StateError("event must be dict")
        required = {"seq", "timestamp", "event_type", "task_id", "prev_hash", "payload", "event_hash"}
        missing = required - set(event.keys())
        if missing:
            raise StateError(f"event missing fields: {sorted(missing)}")
        if event["task_id"] != self.task_id:
            raise StateError("event task_id mismatch")
        if not isinstance(event["seq"], int) or event["seq"] != self._seq + 1:
            raise StateError(f"sequence mismatch: expected {self._seq + 1}, got {event['seq']}")
        if event["prev_hash"] != self._hash_head:
            raise StateError("hash chain mismatch: prev_hash mismatch")
        if _event_hash(event) != event["event_hash"]:
            raise StateError("event hash mismatch")

        event_type = event["event_type"]
        payload = event["payload"]
        if not isinstance(payload, dict):
            raise StateError("payload must be dict")

        if event_type == "INIT":
            if self._seq != 0:
                raise StateError("duplicate INIT")
            if payload.get("status", "DRAFTED") not in STATES:
                raise StateError("invalid init status")
            self.status = payload.get("status", "DRAFTED")
            self.attempt = int(payload.get("attempt", 1))
            self.changed_approaches = list(payload.get("changed_approaches", []))
            self.evidence = dict(payload.get("evidence", {}))
            self.worker_results = list(payload.get("worker_results", []))
            self.data = dict(payload.get("data", {}))
            self.events.append(
                {
                    "timestamp": event["timestamp"],
                    "from": "",
                    "to": self.status,
                    "reason": event.get("reason", "state initialized"),
                }
            )
        elif event_type == "TRANSITION":
            new_status = payload["to"]
            if new_status not in STATES:
                raise IllegalTransitionError(self.status, new_status, f"unknown target state: {new_status}")
            self._validate_transition(self.status, new_status)
            self.status = new_status
            transition_entry = {
                "timestamp": event["timestamp"],
                "from": payload.get("from", self.status),
                "to": new_status,
                "reason": payload.get("reason", event.get("reason", "")),
            }
            if payload.get("evidence_path"):
                transition_entry["evidence_path"] = payload["evidence_path"]
            self.events.append(transition_entry)
        elif event_type == "ATTEMPT_INCREMENT":
            self.attempt += int(payload.get("delta", 1))
            if self.attempt < 1:
                raise StateError("attempt invalid after increment")
        elif event_type == "APPROACH_RECORDED":
            self.changed_approaches.append(str(payload.get("description", "")))
        elif event_type == "EVIDENCE_ADD":
            self.evidence[str(payload["key"])] = str(payload["path"])
        elif event_type == "WORKER_RESULT_ADD":
            self.worker_results.append(copy.deepcopy(payload["result"]))
        elif event_type == "RETRY_METADATA_ADD":
            self.data.setdefault("retry_metadata", [])
            self.data["retry_metadata"].append(copy.deepcopy(payload["metadata"]))
        elif event_type == "DATA_PATCH":
            patch = payload.get("patch", {})
            if not isinstance(patch, dict):
                raise StateError("DATA_PATCH patch must be dict")
            for k, v in patch.items():
                if v is _DELETE_SENTINEL:
                    self.data.pop(k, None)
                else:
                    self.data[k] = copy.deepcopy(v)
        elif event_type == "FORCE_COMPLETE":
            self.status = "FORCE_COMPLETE"
            self.data["force_complete"] = copy.deepcopy(payload)
        elif event_type == "ESCALATE":
            if self.status in ("COMPLETE", "FORCE_COMPLETE", "DEGRADED_PASS"):
                raise IllegalTransitionError(f"cannot force-escalate a {self.status} task")
            self.status = "ESCALATED"
            self.data["escalation_reason"] = event.get("reason", "")
            self.events.append(
                {
                    "timestamp": event["timestamp"],
                    "from": payload.get("from", ""),
                    "to": "ESCALATED",
                    "reason": event.get("reason", "force escalation"),
                }
            )
        elif event_type == "FORCE_BLOCK":
            if self.status in ("COMPLETE", "FORCE_COMPLETE", "DEGRADED_PASS"):
                raise IllegalTransitionError(f"cannot force-block a {self.status} task")
            self.status = "BLOCKED"
            self.data["block_reason"] = event.get("reason", "")
            self.events.append(
                {
                    "timestamp": event["timestamp"],
                    "from": payload.get("from", ""),
                    "to": "BLOCKED",
                    "reason": event.get("reason", "forced block"),
                }
            )
        elif event_type == "WORKER_RESULT_PATCH":
            idx = int(payload["index"])
            if idx < 0 or idx >= len(self.worker_results):
                raise StateError(f"WORKER_RESULT_PATCH index {idx} out of range")
            self.worker_results[idx] = copy.deepcopy(payload["result"])
        elif event_type == "PAUSE":
            self._validate_transition(self.status, "PAUSED")
            self.status = "PAUSED"
            self.events.append(
                {
                    "timestamp": event["timestamp"],
                    "from": payload.get("from", ""),
                    "to": "PAUSED",
                    "reason": event.get("reason", "operator pause"),
                }
            )
        elif event_type == "CANCEL":
            self._validate_transition(self.status, "CANCELLED")
            self.status = "CANCELLED"
            self.events.append(
                {
                    "timestamp": event["timestamp"],
                    "from": payload.get("from", ""),
                    "to": "CANCELLED",
                    "reason": event.get("reason", "operator cancel"),
                }
            )
        else:
            raise StateError(f"unknown event_type: {event_type}")

        self._seq = event["seq"]
        self._hash_head = event["event_hash"]

    def _validate_transition(self, old, new):
        allowed = LEGAL_TRANSITIONS.get(old, set())
        if new not in allowed:
            if old in TERMINAL_STATES:
                raise IllegalTransitionError(f"cannot transition from terminal state {old}")
            raise IllegalTransitionError(f"illegal transition: {old} -> {new} (allowed from {old}: {sorted(allowed)})")

    # ------------------------------------------------------------------
    # Public mutation API (all event-sourced)
    # ------------------------------------------------------------------

    def append_event(self, event_type, payload, reason=""):
        """Append a raw event to the WAL and apply it. Returns the event dict."""
        if self._seq == 0 and event_type != "INIT":
            # Raw-constructed state (e.g. State(task_id, status=...)) that was
            # never created via create_initial_state. Synthesize an INIT from
            # the current projection so the WAL chain starts cleanly instead of
            # mid-chain (fail-closed replay requires a leading INIT, seq=1).
            self.append_event(
                "INIT",
                {
                    "status": self.status,
                    "attempt": self.attempt,
                    "changed_approaches": list(self.changed_approaches),
                    "evidence": dict(self.evidence),
                    "worker_results": list(self.worker_results),
                    "data": dict(self.data),
                },
                reason="synthesized INIT for raw-constructed state",
            )
        event = self._build_event(event_type, payload, reason=reason)
        prev_seq = self._seq
        prev_hash = self._hash_head
        prev_status = self.status
        self._apply_event(event, replay=False)
        try:
            self._append_wal(event)
            self._append_journal(event)
        except Exception:
            self._seq = prev_seq
            self._hash_head = prev_hash
            self.status = prev_status
            raise
        return event

    def transition(self, new_status, reason="", evidence_path=None):
        """Transition to new_status if legal. Event-sourced."""
        payload = {"from": self.status, "to": new_status}
        if evidence_path:
            payload["evidence_path"] = evidence_path
        self.append_event("TRANSITION", payload, reason=reason)

    def force_complete(self, reason="", failed_checks=None, output_hash="", waived_files=None, cleanup_decision=""):
        """Transition to FORCE_COMPLETE with auditable waiver metadata."""
        payload = {
            "reason": reason,
            "failed_checks": failed_checks or [],
            "output_hash": output_hash,
            "waived_files": waived_files or [],
            "cleanup_decision": cleanup_decision,
            "timestamp": _now(),
        }
        self.append_event("FORCE_COMPLETE", payload, reason=reason)

    def record_approach(self, description):
        """Record a changed approach description for the current retry."""
        self.append_event("APPROACH_RECORDED", {"description": description})

    def increment_attempt(self):
        """Increment the retry attempt counter."""
        self.append_event("ATTEMPT_INCREMENT", {"delta": 1}, reason="increment attempt")

    def add_evidence(self, key, path):
        """Link evidence artifact to the state."""
        self.append_event("EVIDENCE_ADD", {"key": key, "path": path})

    def add_worker_result(self, result):
        """Append a recorded worker run."""
        self.append_event("WORKER_RESULT_ADD", {"result": result})

    def add_retry_metadata(self, metadata: dict):
        """Append structured retry metadata."""
        self.append_event("RETRY_METADATA_ADD", {"metadata": metadata})

    def patch_data(self, patch: dict):
        """Event-sourced data mutation. None values delete the key."""
        normalized = {}
        for k, v in patch.items():
            normalized[k] = _DELETE_SENTINEL if v is None else v
        self.append_event("DATA_PATCH", {"patch": normalized})

    def delete_data_key(self, key: str):
        """Event-sourced removal of a data key."""
        self.append_event("DATA_PATCH", {"patch": {key: _DELETE_SENTINEL}})

    def force_escalate(self, reason="stall: no progress in supervision loop"):
        """Privileged escalation to ESCALATED from any non-success state.

        Audited via the ESCALATE event. Refuses to demote a completed task.
        Used by the stall sweeper and failsafe retry paths only.
        """
        self.append_event("ESCALATE", {"from": self.status}, reason=reason)

    def force_block(self, reason="task crashed"):
        """Privileged forced block from any non-success state.

        Audited via the FORCE_BLOCK event. Refuses to demote a completed task.
        Used by the task-crash barrier only.
        """
        self.append_event("FORCE_BLOCK", {"from": self.status}, reason=reason)

    def patch_worker_result(self, index: int, result: dict):
        """Event-sourced replacement of a recorded worker result."""
        self.append_event("WORKER_RESULT_PATCH", {"index": index, "result": result})

    # ------------------------------------------------------------------
    # Query API (unchanged)
    # ------------------------------------------------------------------

    def legal_transitions(self):
        return LEGAL_TRANSITIONS.get(self.status, set())

    def is_terminal(self):
        return self.status in TERMINAL_STATES

    def can_resume(self):
        """Return True if this state is in a resumable (non-terminal) state."""
        return self.status not in TERMINAL_STATES and self.status != "DRAFTED"

    def pause(self, reason="operator pause"):
        """Transition to PAUSED if legal. Returns True on success."""
        if "PAUSED" in self.legal_transitions():
            self.append_event("PAUSE", {"from": self.status}, reason=reason)
            return True
        return False

    def cancel(self, reason="operator cancel"):
        """Transition to CANCELLED if legal. Returns True on success."""
        if "CANCELLED" in self.legal_transitions():
            self.append_event("CANCEL", {"from": self.status}, reason=reason)
            return True
        return False

    def recover_from_journal(self) -> bool:
        """Flag state when the journal contains events the snapshot does not.

        Does NOT rewrite persistence — only marks data["recovered_from_journal"]
        for the audit trail. The WAL replay is authoritative for recovery.
        """
        if not self._journal_dir:
            return False
        journal_path = self.journal_path
        if not os.path.isfile(journal_path):
            return False
        journal_events = []
        try:
            with open(journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        journal_events.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            return False
        ahead = len(journal_events) > len(self.events)
        if not ahead and journal_events and self.events:
            ahead = journal_events[-1] != self.events[-1]
        if ahead:
            self.data["recovered_from_journal"] = True
        return ahead

    @property
    def qc_was_executed(self):
        return "qc_verdict" in self.evidence

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "status": self.status,
            "attempt": self.attempt,
            "changed_approaches": list(self.changed_approaches),
            "events": list(self.events),
            "evidence": dict(self.evidence),
            "worker_results": list(self.worker_results),
            "data": dict(self.data),
            "schema_version": self._schema_version,
            "seq": self._seq,
            "hash_head": self._hash_head,
        }

    @classmethod
    def from_dict(cls, d, journal_dir=None):
        if not isinstance(d, dict) or not isinstance(d.get("task_id"), str) or not d["task_id"]:
            raise StateError("snapshot missing required 'task_id' field")
        return cls(
            task_id=d["task_id"],
            status=d.get("status", "DRAFTED"),
            attempt=d.get("attempt", 1),
            changed_approaches=list(d.get("changed_approaches", [])),
            events=list(d.get("events", [])),
            evidence=dict(d.get("evidence", {})),
            worker_results=list(d.get("worker_results", [])),
            data=dict(d.get("data", {})),
            journal_dir=journal_dir,
            seq=int(d.get("seq", 0)),
            hash_head=d.get("hash_head", ""),
            schema_version=int(d.get("schema_version", 1)),
        )

    def __repr__(self):
        return f"<State {self.task_id} status={self.status} attempt={self.attempt}>"


def _require_task_id(d: dict) -> str:
    task_id = d.get("task_id") if isinstance(d, dict) else None
    if not isinstance(task_id, str) or not task_id:
        raise StateError("snapshot missing required 'task_id' field")
    return task_id


def _migrate_legacy_snapshot(d: dict, journal_dir: str) -> State:
    """Convert a pre-0.5 snapshot (no seq/hash_head) into an event-sourced State.

    The legacy snapshot is treated as a synthetic INIT so the WAL hash chain
    has a valid head. No data is lost; unknown data keys survive in `data`.
    The INIT is appended to the WAL so subsequent events chain correctly.
    """
    state = State(
        task_id=_require_task_id(d),
        status=d.get("status", "DRAFTED"),
        attempt=d.get("attempt", 1),
        changed_approaches=list(d.get("changed_approaches", [])),
        events=list(d.get("events", [])),
        evidence=dict(d.get("evidence", {})),
        worker_results=list(d.get("worker_results", [])),
        data=dict(d.get("data", {})),
        journal_dir=journal_dir,
    )
    state.data.setdefault("migrated_from_snapshot_v1", True)
    if journal_dir:
        init_event = state._build_event(
            "INIT",
            {
                "status": state.status,
                "attempt": state.attempt,
                "changed_approaches": list(state.changed_approaches),
                "evidence": dict(state.evidence),
                "worker_results": list(state.worker_results),
                "data": dict(state.data),
            },
            reason="migrated from snapshot v1",
        )
        state._append_wal(init_event)
        state._seq = init_event["seq"]
        state._hash_head = init_event["event_hash"]
        state.events = []
    return state


def create_initial_state(task_id, journal_dir=None):
    """Create a fresh DRAFTED state for the given task_id (event-sourced)."""
    state = State(
        task_id=task_id,
        status="DRAFTED",
        attempt=1,
        events=[],
        journal_dir=journal_dir,
    )
    # Append INIT + a DRAFTED marker event so the WAL chain starts cleanly.
    # INIT carries the initial projection; replay must not require a
    # persisted snapshot to reconstruct the state.
    state.append_event(
        "INIT",
        {
            "status": "DRAFTED",
            "attempt": 1,
            "changed_approaches": [],
            "evidence": {},
            "worker_results": [],
            "data": {},
        },
        reason="state initialized",
    )
    return state


def load_state(path, journal_dir=None):
    """Load state from a JSON file. Returns State or raises.

    Reads the snapshot (if present), then replays the WAL (if present) that
    may be ahead of the snapshot, verifying the hash chain and transitions.
    Fail-closed: corrupt JSON, hash mismatch, sequence gap, or illegal
    transition all raise StateError/IllegalTransitionError.
    """
    if not os.path.isfile(path):
        raise StateError(f"state file not found: {path}")
    effective_journal_dir = journal_dir or os.path.dirname(os.path.abspath(path))
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise StateError(f"state file corrupt: {exc}") from exc

    if not isinstance(raw, dict):
        raise StateError("state file must be a JSON object")

    if raw.get("schema_version") == STATE_SCHEMA_VERSION:
        state = State.from_dict(raw, journal_dir=effective_journal_dir)
    else:
        state = _migrate_legacy_snapshot(raw, effective_journal_dir)
        # Persist the migrated snapshot immediately (best-effort; if it fails
        # the in-memory state is still usable for this run).
        try:
            save_state(state, path, backup=True)
        except StateError:
            pass

    # Replay WAL events ahead of the snapshot (verifies chain + seq + legality).
    state = replay_wal(path, state=state)
    state.recover_from_journal()
    return state


def replay_wal(state_path, state=None):
    """Replay the WAL and return the authoritative derived State.

    When the WAL starts with a valid new-format INIT (seq=1), the ENTIRE
    chain is replayed from scratch — every event hash, sequence number, and
    transition is verified. The snapshot is only a checkpoint, never trusted
    over the WAL. Fail-closed on any integrity violation.

    Fallbacks:
    - No WAL file: snapshot stands (legacy / checkpoint-only state).
    - Legacy-format WAL (no seq/event_hash) with a migrated snapshot: legacy
      lines are skipped — they carry no chain to verify; snapshot is truth.
    - New-format WAL without a leading INIT: hard error (inconsistent).
    """
    journal_dir = os.path.dirname(os.path.abspath(state_path))
    if state is None:
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            state = State.from_dict(raw, journal_dir=journal_dir)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise StateError(f"state file corrupt: {exc}") from exc

    wal_path = os.path.join(journal_dir, WAL_FILENAME)
    if not os.path.isfile(wal_path):
        return state
    wal_events = []
    try:
        with open(wal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                wal_events.append(json.loads(line))
    except (OSError, json.JSONDecodeError, ValueError):
        raise StateError("WAL file corrupt: failed to parse events")

    if not wal_events:
        return state

    first = wal_events[0]
    is_new_format = isinstance(first, dict) and first.get("event_type") == "INIT" and first.get("seq") == 1
    if not is_new_format:
        # Legacy WAL: only tolerated when the snapshot was migrated from v1.
        if state.data.get("migrated_from_snapshot_v1"):
            return state
        raise StateError("WAL does not start with INIT (seq=1)")

    # Authoritative full-chain replay from a fresh empty state.
    task_id = first.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise StateError("WAL INIT event missing task_id")
    fresh = State(task_id=task_id, status="DRAFTED", journal_dir=journal_dir)
    for event in wal_events:
        fresh._apply_event(event, replay=True)
    fresh.data.setdefault("recovered_from_wal", True)
    return fresh


def recover_from_journal(journal_path, journal_dir=None):
    """Rebuild state from journal file. Returns State or None.

    Legacy journal entries (from/to dicts) are turned into a best-effort
    reconstructed state; full hash-chain integrity is only guaranteed via WAL.
    """
    if not os.path.isfile(journal_path):
        return None
    task_id = ""
    events = []
    data = {}
    attempt = 1
    changed_approaches = []
    evidence = {}
    worker_results = []
    last_status = None
    try:
        with open(journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("event_type") == "INIT":
                    task_id = entry.get("task_id", "")
                    last_status = entry.get("payload", {}).get("status", last_status)
                elif entry.get("event_type") == "TRANSITION":
                    events.append(
                        {
                            "timestamp": entry.get("timestamp", ""),
                            "from": entry.get("payload", {}).get("from", ""),
                            "to": entry.get("payload", {}).get("to", last_status),
                            "reason": entry.get("payload", {}).get("reason", ""),
                        }
                    )
                    last_status = entry.get("payload", {}).get("to", last_status)
                elif entry.get("from") is not None:
                    # Legacy journal line
                    task_id = entry.get("_task_id", task_id)
                    events.append(entry)
                    last_status = entry.get("to", last_status)
    except (json.JSONDecodeError, OSError):
        return None
    if not events and not last_status:
        return None
    state = State(
        task_id=task_id or "recovered",
        status=last_status or "DRAFTED",
        attempt=attempt,
        changed_approaches=changed_approaches,
        events=events,
        evidence=evidence,
        worker_results=worker_results,
        data=data,
        journal_dir=journal_dir,
    )
    return state


def save_state(state, path, backup=True):
    """Persist state to a JSON file with atomic write and optional versioned backup.

    Creates a timestamped backup before overwriting so previous states can be
    reconstructed if the file is later corrupted. Writes tmp + fsync +
    os.replace (atomic).
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if backup and os.path.isfile(path):
        backup_dir = os.path.join(os.path.dirname(path), "state_backups")
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup_path = os.path.join(backup_dir, f"state.{ts}.bak.json")
        shutil.copy2(path, backup_path)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path)
    except OSError as e:
        raise StateError(f"failed to save state: {e}") from e
