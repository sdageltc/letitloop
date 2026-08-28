"""Comprehensive unit tests covering edge cases and branches in orchestrator/models.py and orchestrator/state.py."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest
from orchestrator.exceptions import IllegalTransitionError, StateError
from orchestrator.models import ModelRegistry
from orchestrator.state import (
    JOURNAL_FILENAME,
    LEGAL_TRANSITIONS,
    STATES,
    WAL_FILENAME,
    State,
    _canonical,
    _event_hash,
    _fsync_dir,
    _now,
    _sha256_text,
    _validate_jsonable,
    create_initial_state,
    load_state,
    recover_from_journal,
    replay_wal,
    save_state,
)

pytestmark = pytest.mark.fast


class TestModelsCoverageExpansion:
    def test_default_worker_env_override(self, monkeypatch):
        monkeypatch.setenv("WORKER_MODEL", "custom:worker-model")
        assert ModelRegistry.default_worker() == "custom:worker-model"

    def test_default_worker_gemini_key(self, monkeypatch):
        monkeypatch.delenv("WORKER_MODEL", raising=False)
        assert ModelRegistry.default_worker() == ModelRegistry.WORKER_PREFIXED

    def test_default_worker_no_keys_fallback(self, monkeypatch):
        monkeypatch.delenv("WORKER_MODEL", raising=False)
        assert ModelRegistry.default_worker() == ModelRegistry.WORKER_PREFIXED

    def test_default_qc_env_override(self, monkeypatch):
        monkeypatch.setenv("QC_MODEL", "custom:qc-model")
        assert ModelRegistry.default_qc() == "custom:qc-model"

    def test_default_qc_no_keys_fallback(self, monkeypatch):
        monkeypatch.delenv("QC_MODEL", raising=False)
        assert ModelRegistry.default_qc() == ModelRegistry.QC_PREFIXED

    def test_prefix_helpers(self):
        assert ModelRegistry.prefixed() == ModelRegistry.WORKER_PREFIXED
        assert ModelRegistry.prefixed("gemini-3.1-pro") == "gemini:gemini-3.1-pro"
        assert ModelRegistry.hybrid() == f"hybrid:{ModelRegistry.WORKER_PREFIXED}"
        assert ModelRegistry.hybrid("gemini-3.1-pro") == "hybrid:gemini:gemini-3.1-pro"
        assert ModelRegistry.is_hybrid(f"hybrid:{ModelRegistry.WORKER_PREFIXED}") is True
        assert ModelRegistry.is_hybrid(ModelRegistry.WORKER_PREFIXED) is False
        assert (
            ModelRegistry.strip_hybrid_prefix(f"hybrid:{ModelRegistry.WORKER_PREFIXED}")
            == ModelRegistry.WORKER_PREFIXED
        )
        assert ModelRegistry.strip_hybrid_prefix(ModelRegistry.WORKER_PREFIXED) == ModelRegistry.WORKER_PREFIXED

    def test_constants_definitions(self):
        assert ModelRegistry.GPT_SOL == "openai:gpt-5.6-sol"
        assert ModelRegistry.GPT_TERRA == "openai:gpt-5.6-terra"
        assert ModelRegistry.GPT_LUNA == "openai:gpt-5.6-luna"
        assert ModelRegistry.GPT_CYBER == "openai:gpt-5.6-cyber"
        assert ModelRegistry.O3 == "openai:o3"
        assert ModelRegistry.O3_MINI == "openai:o3-mini"
        assert ModelRegistry.O4_MINI == "openai:o4-mini"
        assert ModelRegistry.O1 == "openai:o1"
        assert ModelRegistry.O1_MINI == "openai:o1-mini"
        assert ModelRegistry.GPT_4O == "openai:gpt-4o"
        assert ModelRegistry.GPT_4O_MINI == "openai:gpt-4o-mini"
        assert ModelRegistry.CLAUDE_OPUS_5 == "anthropic:claude-opus-5"
        assert ModelRegistry.CLAUDE_SONNET_5 == "anthropic:claude-sonnet-5"
        assert ModelRegistry.CLAUDE_FABLE_5 == "anthropic:claude-fable-5"
        assert ModelRegistry.CLAUDE_HAIKU_4_5 == "anthropic:claude-haiku-4-5"
        assert ModelRegistry.CLAUDE_3_7_SONNET == "anthropic:claude-3-7-sonnet-latest"
        assert ModelRegistry.CLAUDE_3_5_SONNET == "anthropic:claude-3-5-sonnet-latest"
        assert ModelRegistry.CLAUDE_3_OPUS == "anthropic:claude-3-opus-latest"
        assert ModelRegistry.GEMINI_3_7_FLASH == "gemini:gemini-3.7-flash"
        assert ModelRegistry.GEMINI_3_6_FLASH == "gemini:gemini-3.6-flash"
        assert ModelRegistry.GEMINI_3_5_FLASH_LITE == "gemini:gemini-3.5-flash-lite"
        assert ModelRegistry.GEMINI_3_1_PRO == "gemini:gemini-3.1-pro"
        assert ModelRegistry.GEMINI_2_5_PRO == "gemini:gemini-2.5-pro"
        assert ModelRegistry.GEMINI_2_5_FLASH == "gemini:gemini-2.5-flash"
        assert ModelRegistry.DEEPSEEK_V4_PRO == "deepseek:deepseek-v4-pro"
        assert ModelRegistry.DEEPSEEK_V4_FLASH == "deepseek:deepseek-v4-flash"
        assert ModelRegistry.DEEPSEEK_CHAT == "deepseek:deepseek-chat"
        assert ModelRegistry.DEEPSEEK_REASONER == "deepseek:deepseek-reasoner"
        assert ModelRegistry.KIMI_K3 == "kimi:kimi-k3"
        assert ModelRegistry.KIMI_K2 == "kimi:kimi-k2"
        assert ModelRegistry.OMNIROUTE_AUTO == "omniroute:auto"


class TestStateHelpersAndProperties:
    def test_now_and_canonical(self):
        now_str = _now()
        assert "T" in now_str
        canon = _canonical({"b": 2, "a": 1})
        assert canon == '{"a":1,"b":2}'

    def test_sha256_text(self):
        h = _sha256_text("hello")
        assert len(h) == 64

    def test_event_hash_ignores_event_hash_key(self):
        event1 = {"seq": 1, "task_id": "t1", "payload": {}}
        event2 = {"seq": 1, "task_id": "t1", "payload": {}, "event_hash": "dummy"}
        assert _event_hash(event1) == _event_hash(event2)

    def test_validate_jsonable(self):
        _validate_jsonable({"valid": [1, 2, "three"]})
        with pytest.raises(StateError, match="not JSON-serializable"):
            _validate_jsonable({"invalid": lambda x: x})
        with pytest.raises(StateError, match="not JSON-serializable"):
            _validate_jsonable({"invalid_set": {1, 2, 3}})

    def test_fsync_dir_posix_and_windows(self):
        with tempfile.TemporaryDirectory() as td:
            file_path = os.path.join(td, "sub", "test.txt")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                f.write("data")
            _fsync_dir(file_path)

            with patch("os.name", "posix"), patch.object(os, "O_DIRECTORY", 0x10000, create=True):
                with patch("os.open", side_effect=OSError("open failed")):
                    _fsync_dir(file_path)
                with (
                    patch("os.open", return_value=999),
                    patch("os.fsync") as mock_fsync,
                    patch("os.close") as mock_close,
                ):
                    _fsync_dir(file_path)
                    mock_fsync.assert_called_once_with(999)
                    mock_close.assert_called_once_with(999)

    def test_init_invalid_state_and_attempt(self):
        with pytest.raises(StateError, match="invalid state"):
            State(task_id="t1", status="INVALID_STATE")
        with pytest.raises(StateError, match="attempt must be int >= 1"):
            State(task_id="t1", attempt=0)
        with pytest.raises(StateError, match="attempt must be int >= 1"):
            State(task_id="t1", attempt="1")
        s_none = State(task_id="t1", status=None)
        assert s_none.status is None

    def test_wal_and_journal_path_without_journal_dir(self):
        s = State(task_id="t1")
        with pytest.raises(StateError, match="journal_dir not set"):
            _ = s.wal_path
        with pytest.raises(StateError, match="journal_dir not set"):
            _ = s.journal_path

    def test_wal_and_journal_path_with_journal_dir(self):
        s = State(task_id="t1", journal_dir="/tmp/test_dir")
        assert s.wal_path == os.path.join("/tmp/test_dir", WAL_FILENAME)
        assert s.journal_path == os.path.join("/tmp/test_dir", JOURNAL_FILENAME)
        s.set_journal_dir("/new/dir")
        assert s.wal_path == os.path.join("/new/dir", WAL_FILENAME)

    def test_repr_and_qc_was_executed(self):
        s = State(task_id="t1", status="WORKING", attempt=2)
        assert repr(s) == "<State t1 status=WORKING attempt=2>"
        assert s.qc_was_executed is False
        s.evidence["qc_verdict"] = "/path/to/verdict.json"
        assert s.qc_was_executed is True


class TestStateWALIntegrityAndApply:
    def test_apply_event_type_validations(self):
        s = State(task_id="t1")
        with pytest.raises(StateError, match="event must be dict"):
            s._apply_event("not-a-dict")

        with pytest.raises(StateError, match="event missing fields"):
            s._apply_event({"seq": 1})

        valid_base = {
            "seq": 1,
            "timestamp": _now(),
            "event_type": "INIT",
            "task_id": "other_task",
            "prev_hash": "",
            "payload": {"status": "DRAFTED"},
        }
        valid_base["event_hash"] = _event_hash(valid_base)
        with pytest.raises(StateError, match="event task_id mismatch"):
            s._apply_event(valid_base)

        valid_base["task_id"] = "t1"
        valid_base["seq"] = 2
        valid_base["event_hash"] = _event_hash(valid_base)
        with pytest.raises(StateError, match="sequence mismatch"):
            s._apply_event(valid_base)

        valid_base["seq"] = 1
        valid_base["prev_hash"] = "wrong_hash"
        valid_base["event_hash"] = _event_hash(valid_base)
        with pytest.raises(StateError, match="hash chain mismatch"):
            s._apply_event(valid_base)

        valid_base["prev_hash"] = ""
        valid_base["event_hash"] = "corrupted_hash"
        with pytest.raises(StateError, match="event hash mismatch"):
            s._apply_event(valid_base)

        valid_base["event_hash"] = _event_hash(valid_base)
        valid_base["payload"] = "not-a-dict"
        valid_base["event_hash"] = _event_hash(valid_base)
        with pytest.raises(StateError, match="payload must be dict"):
            s._apply_event(valid_base)

    def test_apply_init_duplicate_and_invalid_status(self):
        s = State(task_id="t1")
        event = s._build_event("INIT", {"status": "INVALID_STATUS"})
        with pytest.raises(StateError, match="invalid init status"):
            s._apply_event(event)

        init_event = s._build_event("INIT", {"status": "DRAFTED"})
        s._apply_event(init_event)
        assert s._seq == 1
        assert s.status == "DRAFTED"

        dup_init = {
            "seq": 2,
            "timestamp": _now(),
            "event_type": "INIT",
            "task_id": "t1",
            "prev_hash": s._hash_head,
            "payload": {"status": "DRAFTED"},
        }
        dup_init["event_hash"] = _event_hash(dup_init)
        with pytest.raises(StateError, match="duplicate INIT"):
            s._apply_event(dup_init)

    def test_apply_transition_unknown_and_illegal(self):
        s = create_initial_state("t1")
        event = s._build_event("TRANSITION", {"from": "DRAFTED", "to": "UNKNOWN_STATE"})
        with pytest.raises(IllegalTransitionError, match="unknown target state"):
            s._apply_event(event)

        event_illegal = s._build_event("TRANSITION", {"from": "DRAFTED", "to": "COMPLETE"})
        with pytest.raises(IllegalTransitionError, match="illegal transition"):
            s._apply_event(event_illegal)

        event_legal = s._build_event(
            "TRANSITION", {"from": "DRAFTED", "to": "PREFLIGHT_RUNNING", "evidence_path": "/ev.json"}
        )
        s._apply_event(event_legal)
        assert s.status == "PREFLIGHT_RUNNING"
        assert s.events[-1]["evidence_path"] == "/ev.json"

    def test_apply_attempt_increment_invalid(self):
        s = create_initial_state("t1")
        event = s._build_event("ATTEMPT_INCREMENT", {"delta": -5})
        with pytest.raises(StateError, match="attempt invalid after increment"):
            s._apply_event(event)

    def test_apply_approach_evidence_worker_results_retry_metadata(self):
        s = create_initial_state("t1")
        s.record_approach("approach 1")
        assert s.changed_approaches == ["approach 1"]

        s.add_evidence("k1", "/path/k1.json")
        assert s.evidence["k1"] == "/path/k1.json"

        s.add_worker_result({"exit_code": 0, "run": 1})
        assert len(s.worker_results) == 1
        assert s.worker_results[0]["run"] == 1

        s.add_retry_metadata({"retry_reason": "timeout"})
        assert s.data["retry_metadata"] == [{"retry_reason": "timeout"}]

    def test_apply_data_patch_and_delete_key(self):
        s = create_initial_state("t1")
        s.patch_data({"key1": "val1", "key2": "val2", "nested": {"a": 1}})
        assert s.data["key1"] == "val1"
        assert s.data["key2"] == "val2"
        assert s.data["nested"] == {"a": 1}

        s.delete_data_key("key1")
        assert "key1" not in s.data
        assert s.data["key2"] == "val2"

        s.patch_data({"key2": None, "key3": "val3"})
        assert "key2" not in s.data
        assert s.data["key3"] == "val3"

        event_bad_patch = s._build_event("DATA_PATCH", {"patch": "not-a-dict"})
        with pytest.raises(StateError, match="DATA_PATCH patch must be dict"):
            s._apply_event(event_bad_patch)

    def test_apply_force_complete_and_waivers(self):
        s = create_initial_state("t1")
        s.force_complete(
            reason="waived for release",
            failed_checks=["lint"],
            output_hash="abc123hash",
            waived_files=["foo.py"],
            cleanup_decision="retain",
        )
        assert s.status == "FORCE_COMPLETE"
        assert s.data["force_complete"]["reason"] == "waived for release"
        assert s.data["force_complete"]["failed_checks"] == ["lint"]
        assert s.data["force_complete"]["output_hash"] == "abc123hash"
        assert s.data["force_complete"]["waived_files"] == ["foo.py"]
        assert s.data["force_complete"]["cleanup_decision"] == "retain"

    def test_apply_escalate_and_force_block(self):
        s = create_initial_state("t1")
        s.force_escalate(reason="supervision loop stuck")
        assert s.status == "ESCALATED"
        assert s.data["escalation_reason"] == "supervision loop stuck"
        assert s.events[-1]["to"] == "ESCALATED"

        s_term = create_initial_state("t_term")
        s_term.status = "COMPLETE"
        with pytest.raises(IllegalTransitionError, match="cannot force-escalate"):
            s_term.force_escalate()

        s2 = create_initial_state("t2")
        s2.force_block(reason="crash detected")
        assert s2.status == "BLOCKED"
        assert s2.data["block_reason"] == "crash detected"
        assert s2.events[-1]["to"] == "BLOCKED"

        s3 = create_initial_state("t3")
        s3.status = "COMPLETE"
        with pytest.raises(IllegalTransitionError, match="cannot force-block"):
            s3.force_block()

    def test_apply_worker_result_patch(self):
        s = create_initial_state("t1")
        s.add_worker_result({"exit_code": 1})
        s.patch_worker_result(0, {"exit_code": 0, "fixed": True})
        assert s.worker_results[0]["exit_code"] == 0
        assert s.worker_results[0]["fixed"] is True

        with pytest.raises(StateError, match="out of range"):
            s.patch_worker_result(5, {"exit_code": 2})
        with pytest.raises(StateError, match="out of range"):
            s.patch_worker_result(-1, {"exit_code": 2})

    def test_pause_and_cancel_transitions(self):
        s = create_initial_state("t1")
        assert s.pause() is False
        assert s.cancel(reason="user requested") is True
        assert s.status == "CANCELLED"
        assert s.pause() is False
        assert s.cancel() is False

        s2 = create_initial_state("t2")
        s2.transition("PREFLIGHT_RUNNING")
        assert s2.pause(reason="pause preflight") is True
        assert s2.status == "PAUSED"
        assert s2.events[-1]["to"] == "PAUSED"
        s2.transition("READY")
        assert s2.status == "READY"

    def test_unknown_event_type_raises(self):
        s = create_initial_state("t1")
        event = s._build_event("UNKNOWN_TYPE", {})
        with pytest.raises(StateError, match="unknown event_type"):
            s._apply_event(event)

    def test_raw_constructed_state_synthesizes_init(self):
        s = State(task_id="raw_task", status="WORKING", attempt=3, evidence={"k": "v"})
        assert s._seq == 0
        s.increment_attempt()
        assert s._seq == 2
        assert s.attempt == 4
        assert s.events[0]["to"] == "WORKING"
        assert s.events[0]["reason"] == "synthesized INIT for raw-constructed state"


class TestStatePersistenceAndRecovery:
    def test_append_wal_and_journal_ioerror_handling(self):
        with tempfile.TemporaryDirectory() as td:
            s = create_initial_state("t_io", journal_dir=td)
            event = s._build_event("APPROACH_RECORDED", {"description": "test"})
            with patch("builtins.open", side_effect=OSError("disk full")):
                with pytest.raises(StateError, match="failed to append WAL"):
                    s._append_wal(event)
                with pytest.raises(StateError, match="failed to append journal"):
                    s._append_journal(event)

    def test_save_state_with_backup_and_ioerror(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "state.json")
            s = create_initial_state("t_save", journal_dir=td)
            save_state(s, state_path, backup=True)
            assert os.path.isfile(state_path)

            s.transition("PREFLIGHT_RUNNING")
            save_state(s, state_path, backup=True)
            backup_dir = os.path.join(td, "state_backups")
            assert os.path.isdir(backup_dir)
            backups = os.listdir(backup_dir)
            assert len(backups) == 1
            assert backups[0].startswith("state.") and backups[0].endswith(".bak.json")

            with patch("os.replace", side_effect=OSError("replace failed")):
                with pytest.raises(StateError, match="failed to save state"):
                    save_state(s, state_path)

    def test_load_state_errors(self):
        with pytest.raises(StateError, match="state file not found"):
            load_state("/nonexistent/file/state.json")

        with tempfile.TemporaryDirectory() as td:
            corrupt_file = os.path.join(td, "corrupt.json")
            with open(corrupt_file, "w") as f:
                f.write("{invalid json")
            with pytest.raises(StateError, match="state file corrupt"):
                load_state(corrupt_file)

            non_object_file = os.path.join(td, "non_object.json")
            with open(non_object_file, "w") as f:
                f.write('["an", "array"]')
            with pytest.raises(StateError, match="state file must be a JSON object"):
                load_state(non_object_file)

    def test_load_state_legacy_v1_migration(self):
        with tempfile.TemporaryDirectory() as td:
            legacy_path = os.path.join(td, "legacy_state.json")
            legacy_data = {
                "task_id": "legacy-task",
                "status": "WORKING",
                "attempt": 2,
                "data": {"custom_flag": True},
            }
            with open(legacy_path, "w") as f:
                json.dump(legacy_data, f)

            loaded = load_state(legacy_path, journal_dir=td)
            assert loaded.task_id == "legacy-task"
            assert loaded.status == "WORKING"
            assert loaded.data["migrated_from_snapshot_v1"] is True
            assert loaded.data["custom_flag"] is True
            assert loaded._seq == 1
            assert os.path.isfile(os.path.join(td, WAL_FILENAME))

    def test_replay_wal_edge_cases(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "state.json")
            wal_path = os.path.join(td, WAL_FILENAME)

            with open(state_path, "w") as f:
                f.write("corrupt")
            with pytest.raises(StateError, match="state file corrupt"):
                replay_wal(state_path, state=None)

            s = create_initial_state("t_wal", journal_dir=td)
            save_state(s, state_path)
            if os.path.exists(wal_path):
                os.remove(wal_path)
            replayed = replay_wal(state_path, state=s)
            assert replayed.task_id == "t_wal"

            with open(wal_path, "w") as f:
                f.write("corrupt wal line\n")
            with pytest.raises(StateError, match="WAL file corrupt"):
                replay_wal(state_path, state=s)

            with open(wal_path, "w") as f:
                f.write("\n\n")
            assert replay_wal(state_path, state=s).task_id == "t_wal"

            bad_first = {
                "seq": 2,
                "timestamp": _now(),
                "event_type": "TRANSITION",
                "task_id": "t_wal",
                "prev_hash": "",
                "payload": {"from": "DRAFTED", "to": "PREFLIGHT_RUNNING"},
            }
            bad_first["event_hash"] = _event_hash(bad_first)
            with open(wal_path, "w") as f:
                f.write(json.dumps(bad_first) + "\n")
            with pytest.raises(StateError, match="WAL does not start with INIT"):
                replay_wal(state_path, state=s)

            s.data["migrated_from_snapshot_v1"] = True
            with pytest.raises(StateError, match="WAL does not start with INIT"):
                replay_wal(state_path, state=s)

            bad_init = {
                "seq": 1,
                "timestamp": _now(),
                "event_type": "INIT",
                "prev_hash": "",
                "payload": {"status": "DRAFTED"},
            }
            bad_init["event_hash"] = _event_hash(bad_init)
            with open(wal_path, "w") as f:
                f.write(json.dumps(bad_init) + "\n")
            s.data.pop("migrated_from_snapshot_v1", None)
            with pytest.raises(StateError, match="WAL INIT event missing task_id"):
                replay_wal(state_path, state=s)

            with tempfile.TemporaryDirectory() as td_fresh:
                state_path_fresh = os.path.join(td_fresh, "state.json")
                fresh_state = create_initial_state("chain_test", journal_dir=td_fresh)
                fresh_state.transition("PREFLIGHT_RUNNING")
                fresh_state.transition("READY")
                fresh_state.transition("WORKING")
                save_state(fresh_state, state_path_fresh)

                full_replayed = replay_wal(state_path_fresh)
                assert full_replayed.task_id == "chain_test"
                assert full_replayed.status == "WORKING"
                assert full_replayed.data["recovered_from_wal"] is True

    def test_recover_from_journal_method(self):
        with tempfile.TemporaryDirectory() as td:
            s = create_initial_state("t_rec", journal_dir=td)
            assert s.recover_from_journal() is True
            assert s.data["recovered_from_journal"] is True

            s_no_j = State(task_id="t_no_j", journal_dir=os.path.join(td, "nonexistent"))
            assert s_no_j.recover_from_journal() is False

            corrupt_dir = os.path.join(td, "corrupt_j")
            os.makedirs(corrupt_dir, exist_ok=True)
            s_corrupt = State(task_id="t_corrupt", journal_dir=corrupt_dir)
            with open(s_corrupt.journal_path, "w") as f:
                f.write("bad json\n")
            assert s_corrupt.recover_from_journal() is False

            s_fresh = create_initial_state("t_fresh", journal_dir=td)
            s_fresh.events = [{"event_type": "TRANSITION", "payload": {"to": "WORKING"}}]
            assert s_fresh.recover_from_journal() is True

    def test_recover_from_journal_module_function(self):
        with tempfile.TemporaryDirectory() as td:
            journal_path = os.path.join(td, JOURNAL_FILENAME)

            assert recover_from_journal(os.path.join(td, "missing.jsonl")) is None

            with open(journal_path, "w") as f:
                f.write("{invalid\n")
            assert recover_from_journal(journal_path) is None

            with open(journal_path, "w") as f:
                f.write("\n\n")
            assert recover_from_journal(journal_path) is None

            lines = [
                {"event_type": "INIT", "task_id": "j_task", "payload": {"status": "DRAFTED"}},
                {
                    "event_type": "TRANSITION",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "payload": {"from": "DRAFTED", "to": "PREFLIGHT_RUNNING", "reason": "init run"},
                },
            ]
            with open(journal_path, "w") as f:
                for line in lines:
                    f.write(json.dumps(line) + "\n")

            recovered = recover_from_journal(journal_path, journal_dir=td)
            assert recovered is not None
            assert recovered.task_id == "j_task"
            assert recovered.status == "PREFLIGHT_RUNNING"
            assert len(recovered.events) == 1
            assert recovered.events[0]["to"] == "PREFLIGHT_RUNNING"

            legacy_lines = [
                {"_task_id": "legacy_j_task", "from": "READY", "to": "WORKING", "reason": "legacy worker start"},
            ]
            with open(journal_path, "w") as f:
                for leg_line in legacy_lines:
                    f.write(json.dumps(leg_line) + "\n")

            recovered_legacy = recover_from_journal(journal_path, journal_dir=td)
            assert recovered_legacy is not None
            assert recovered_legacy.task_id == "legacy_j_task"
            assert recovered_legacy.status == "WORKING"


class TestAllTransitionsMatrix:
    def test_complete_transition_matrix(self):
        for start_state in STATES:
            legal_targets = LEGAL_TRANSITIONS.get(start_state, set())
            for target_state in STATES:
                s = State(task_id="matrix_test", status=start_state)
                if target_state in legal_targets:
                    s.transition(target_state, reason=f"testing {start_state}->{target_state}")
                    assert s.status == target_state
                else:
                    with pytest.raises(IllegalTransitionError):
                        s.transition(target_state, reason=f"testing illegal {start_state}->{target_state}")
