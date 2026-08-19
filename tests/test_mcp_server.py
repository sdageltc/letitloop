"""Unit and integration tests for letitloop MCP Server."""

import json
import os
import tempfile

import pytest

from orchestrator.mcp_server import LetItLoopMCPServer


@pytest.fixture
def temp_mcp_env():
    with tempfile.TemporaryDirectory() as td:
        ws = os.path.join(td, "workspace")
        rd = os.path.join(td, "runs")
        os.makedirs(ws, exist_ok=True)
        os.makedirs(rd, exist_ok=True)
        yield ws, rd


def test_mcp_server_init(temp_mcp_env):
    ws, rd = temp_mcp_env
    server = LetItLoopMCPServer(workspace_root=ws, run_dir=rd)
    assert server.workspace_root == ws
    assert server.run_dir == rd


def test_mcp_get_tool_definitions(temp_mcp_env):
    ws, rd = temp_mcp_env
    server = LetItLoopMCPServer(workspace_root=ws, run_dir=rd)
    tools = server.get_tool_definitions()
    assert len(tools) == 8
    tool_names = {t["name"] for t in tools}
    assert "create_goal" in tool_names
    assert "load_contract_file" in tool_names
    assert "run_contract_verification" in tool_names
    assert "run_quality_review" in tool_names
    assert "execute_supervisor_plan" in tool_names
    assert "inspect_task_state" in tool_names
    assert "reconcile_workspace_state" in tool_names
    assert "get_system_health" in tool_names


def test_mcp_create_goal(temp_mcp_env):
    ws, rd = temp_mcp_env
    server = LetItLoopMCPServer(workspace_root=ws, run_dir=rd)
    resp = server.handle_tool_call(
        "create_goal",
        {"goal_id": "test_goal_1", "title": "Test Title", "description": "Goal Details"},
    )
    assert resp["status"] == "SUCCESS"
    assert resp["goal"]["goal_id"] == "test_goal_1"
    assert resp["goal"]["title"] == "Test Title"


def test_mcp_get_system_health(temp_mcp_env):
    ws, rd = temp_mcp_env
    server = LetItLoopMCPServer(workspace_root=ws, run_dir=rd)
    resp = server.handle_tool_call("get_system_health", {})
    assert resp["status"] == "HEALTHY"
    assert resp["engine"].startswith("letitloop")
    assert resp["workspace_root"] == ws


def test_mcp_load_contract_file(temp_mcp_env):
    ws, rd = temp_mcp_env
    contract_data = {
        "task_id": "c_task_1",
        "title": "Contract 1",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "Write hello.txt",
        "worker": {"model": "test", "max_attempts": 1},
        "inputs": [],
        "outputs": [{"path": "scratch/hello.txt"}],
        "acceptance_checks": [{"id": "chk1", "kind": "file_exists", "path": "scratch/hello.txt", "expected": True}],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    cpath = os.path.join(ws, "contract.json")
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump(contract_data, f)

    server = LetItLoopMCPServer(workspace_root=ws, run_dir=rd)
    resp = server.handle_tool_call("load_contract_file", {"contract_path": cpath})
    assert resp["status"] == "SUCCESS"
    assert resp["contract"]["task_id"] == "c_task_1"


def test_mcp_inspect_task_state_not_found(temp_mcp_env):
    ws, rd = temp_mcp_env
    server = LetItLoopMCPServer(workspace_root=ws, run_dir=rd)
    resp = server.handle_tool_call("inspect_task_state", {"task_id": "nonexistent_task"})
    assert resp["status"] == "NOT_FOUND"


def test_mcp_unknown_tool(temp_mcp_env):
    ws, rd = temp_mcp_env
    server = LetItLoopMCPServer(workspace_root=ws, run_dir=rd)
    resp = server.handle_tool_call("unknown_tool_xyz", {})
    assert resp["status"] == "ERROR"
