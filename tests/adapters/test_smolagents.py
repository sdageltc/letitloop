"""Unit tests for Hugging Face Smolagents durability adapter."""

import os

from letitloop.adapters.smolagents import SmolagentsWALCallback


class MockSmolagentStep:
    def __init__(self, step_number: int, tool_name: str = "python_eval", action: str = "print('hi')"):
        self.step_number = step_number
        self.tool_calls = [{"name": tool_name, "arguments": {"code": action}}]
        self.observations = "hi"
        self.action_output = "hi"
        self.model_output = "I will print hi"


class MockAgentWithLogs:
    def __init__(self):
        self.logs = []
        self.step_callbacks = []


def test_smolagents_callback_init(tmp_path):
    wal_dir = tmp_path / "smol_wal"
    cb = SmolagentsWALCallback(wal_dir=str(wal_dir), session_id="smol_1")
    assert os.path.isdir(str(wal_dir))
    assert os.path.isfile(cb.wal_file)
    assert len(cb.get_completed_steps()) == 0


def test_smolagents_step_recording_and_resume(tmp_path):
    wal_dir = tmp_path / "smol_wal"
    cb = SmolagentsWALCallback(wal_dir=str(wal_dir), session_id="smol_2")

    # Step 1
    step1 = MockSmolagentStep(step_number=1, tool_name="search", action="search('Python 3.12')")
    cb(step1)
    assert len(cb.get_completed_steps()) == 1

    # Step 2
    step2 = {"step_number": 2, "action": "calculator", "result": 42}
    cb(step2)
    assert len(cb.get_completed_steps()) == 2

    # Simulate restart
    resume_cb = SmolagentsWALCallback(wal_dir=str(wal_dir), session_id="smol_2", auto_resume=True)
    assert len(resume_cb.get_completed_steps()) == 2

    # Restore memory on new agent
    agent = MockAgentWithLogs()
    restored = resume_cb.restore_agent_memory(agent)
    assert restored == 2
    assert len(agent.logs) == 2


def test_smolagents_wrap_agent(tmp_path):
    wal_dir = tmp_path / "smol_wal"
    cb = SmolagentsWALCallback(wal_dir=str(wal_dir), session_id="smol_wrap")
    agent = MockAgentWithLogs()
    cb.wrap_agent(agent)
    assert cb in agent.step_callbacks
