import os

import pytest
from letitloop.adapters.autogen import AutoGenStateSerializer


class MockAutoGenAgent:
    def __init__(self, name="assistant"):
        self.name = name
        self.state = {}
        self.sent_messages = []

    def send(self, message, recipient, request_reply=True, silent=False):
        self.sent_messages.append((message, recipient))
        return True

    def load_state(self, state):
        self.state = state


def test_autogen_serializer_init(tmp_path):
    wal_dir = tmp_path / "autogen_wal"
    serializer = AutoGenStateSerializer(wal_dir=str(wal_dir), session_id="ag_1")
    assert os.path.isdir(str(wal_dir))
    assert os.path.isfile(serializer.wal_file)


def test_autogen_save_load_and_resume(tmp_path):
    wal_dir = tmp_path / "autogen_wal"
    serializer = AutoGenStateSerializer(wal_dir=str(wal_dir), session_id="ag_2")

    # Save agent state
    state = {"memory": ["msg1", "msg2"], "tool_cache": {"t1": "res1"}}
    serializer.save_agent_state("PlannerAgent", state)
    assert serializer.load_agent_state("PlannerAgent") == state

    # Checkpoint message
    serializer.checkpoint_message("UserProxy", "PlannerAgent", "Plan execution for Q3")
    assert len(serializer.get_message_history()) == 1

    # Checkpoint tool
    serializer.checkpoint_tool_call("CoderAgent", "call_123", "execute_code", {"code": "1+1"}, {"res": 2})

    # Snapshot
    serializer.create_checkpoint()

    # Resume test
    resume_serializer = AutoGenStateSerializer(wal_dir=str(wal_dir), session_id="ag_2", auto_resume=True)
    assert resume_serializer.load_agent_state("PlannerAgent") == state
    assert len(resume_serializer.get_message_history()) == 1


def test_autogen_wrap_agent(tmp_path):
    wal_dir = tmp_path / "autogen_wal"
    serializer = AutoGenStateSerializer(wal_dir=str(wal_dir), session_id="ag_wrap")

    agent1 = MockAutoGenAgent("UserProxy")
    agent2 = MockAutoGenAgent("Assistant")

    serializer.wrap_agent(agent1)
    agent1.send("Hello assistant", agent2)

    assert len(serializer.get_message_history()) == 1
    msg = serializer.get_message_history()[0]
    assert msg["sender"] == "UserProxy"
    assert msg["recipient"] == "Assistant"
    assert msg["message"] == "Hello assistant"


def test_autogen_corrupt_wal_fails_closed(tmp_path):
    wal_dir = tmp_path / "autogen_wal"
    serializer = AutoGenStateSerializer(wal_dir=str(wal_dir), session_id="ag_corrupt")
    serializer.save_agent_state("PlannerAgent", {"step": 1, "plan": "active"})

    with open(serializer.wal_file, "r", encoding="utf-8") as f:
        content = f.read()

    assert "LILWAL02:" in content
    # Tamper with the compact JSON payload while keeping frame header intact to cause CRC mismatch
    tampered_content = content.replace('"step":1', '"step":999')
    assert tampered_content != content
    with open(serializer.wal_file, "w", encoding="utf-8") as f:
        f.write(tampered_content)

    # Replay must fail closed with ValueError
    with pytest.raises(ValueError, match="Corrupt LILWAL02 frame CRC/length mismatch"):
        AutoGenStateSerializer(wal_dir=str(wal_dir), session_id="ag_corrupt", auto_resume=True)
