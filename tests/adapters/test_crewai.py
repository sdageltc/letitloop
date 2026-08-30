"""Unit tests for CrewAI durability adapter."""

import os

from letitloop.adapters.crewai import CrewAIDurabilityHandler


class MockTask:
    def __init__(self, description: str, task_id: str = "t1"):
        self.description = description
        self.id = task_id
        self.callback = None


class MockAgent:
    def __init__(self, role: str = "Financial Analyst"):
        self.role = role


class MockCrew:
    def __init__(self, tasks, agents=None):
        self.tasks = tasks
        self.agents = agents or []
        self.step_callback = None


def test_crewai_handler_init(tmp_path):
    wal_dir = tmp_path / "crewai_wal"
    handler = CrewAIDurabilityHandler(wal_dir=str(wal_dir), session_id="crew_test_1")
    assert os.path.isdir(str(wal_dir))
    assert os.path.isfile(handler.wal_file)
    state = handler.get_crew_state()
    assert state["session_id"] == "crew_test_1"
    assert state["total_completed_tasks"] == 0


def test_crewai_task_lifecycle(tmp_path):
    wal_dir = tmp_path / "crewai_wal"
    handler = CrewAIDurabilityHandler(wal_dir=str(wal_dir), session_id="crew_test_2")
    task1 = MockTask(description="Fetch AAPL revenue", task_id="task_aapl")
    agent = MockAgent(role="Market Researcher")

    # Start task
    cached = handler.on_task_start(task1, agent)
    assert cached is None
    assert not handler.is_task_completed(task1)

    # Tool execution
    handler.on_tool_execute("yfinance_fetch", {"ticker": "AAPL"}, {"revenue": "94.9B"})

    # Complete task
    out = handler.on_task_end(task1, {"summary": "AAPL revenue is 94.9B in Q3"})
    assert out["summary"] == "AAPL revenue is 94.9B in Q3"
    assert handler.is_task_completed(task1)
    assert handler.get_cached_task_output(task1) == {"summary": "AAPL revenue is 94.9B in Q3"}

    # Resume test: new handler instance on same wal_dir
    resume_handler = CrewAIDurabilityHandler(wal_dir=str(wal_dir), session_id="crew_test_2", auto_resume=True)
    assert resume_handler.is_task_completed(task1)
    cached_resume = resume_handler.on_task_start(task1, agent)
    assert cached_resume == {"summary": "AAPL revenue is 94.9B in Q3"}


def test_crewai_wrap_task_and_crew(tmp_path):
    wal_dir = tmp_path / "crewai_wal"
    handler = CrewAIDurabilityHandler(wal_dir=str(wal_dir), session_id="crew_wrap")

    task = MockTask(description="Write report", task_id="report_task")
    called_original = False

    def orig_cb(output):
        nonlocal called_original
        called_original = True
        return output

    task.callback = orig_cb
    handler.wrap_task(task)

    # Trigger callback
    task.callback("Report content")
    assert called_original
    assert handler.is_task_completed(task)

    crew = MockCrew(tasks=[task])
    handler.wrap_crew(crew)
    assert callable(crew.step_callback)
    crew.step_callback(type("StepOutput", (), {"tool": "search", "tool_input": "q", "result": "res"})())
    state = handler.get_crew_state()
    assert state["total_tool_calls"] >= 1


def test_crewai_error_handling(tmp_path):
    wal_dir = tmp_path / "crewai_wal"
    handler = CrewAIDurabilityHandler(wal_dir=str(wal_dir), session_id="crew_err")
    task = MockTask(description="Broken task", task_id="err_task")
    handler.on_task_error(task, ValueError("API rate limit exceeded"))
    assert os.path.isfile(handler.wal_file)
