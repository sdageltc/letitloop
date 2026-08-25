"""Model Context Protocol (MCP) server for letitloop (LIL).

Exposes the full macro-task control loop, contract graph, verification engine,
and quality plane as machine-readable tools for AI assistants and IDEs.
"""

import json
import os
import sys
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from .contract import load_contract
from .goal import Goal, Plan
from .metrics import MetricsCollector
from .quality_plan import quality_plan_for_contract
from .quality_plane import run_quality_plane
from .reconcile import run_reconciliation
from .state import load_state
from .supervisor import Supervisor
from .verifier import run_verification

_DEFAULT_WORKSPACE = os.path.abspath(os.getcwd())
_DEFAULT_RUN_DIR = os.path.join(_DEFAULT_WORKSPACE, "scratch", "orchestrator_runs")


class LetItLoopMCPServer:
    """Core MCP Server implementation exposing orchestrator capabilities."""

    def __init__(self, workspace_root: Optional[str] = None, run_dir: Optional[str] = None):
        self.workspace_root = os.path.abspath(workspace_root or _DEFAULT_WORKSPACE)
        self.run_dir = os.path.abspath(run_dir or _DEFAULT_RUN_DIR)
        os.makedirs(self.run_dir, exist_ok=True)
        self.metrics = MetricsCollector()

    @classmethod
    def get_tool_definitions(cls) -> List[Dict[str, Any]]:
        """Return MCP tool definitions schema."""
        return [
            {
                "name": "create_goal",
                "description": "Initialize a new autonomous macro-goal with goal ID and title.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "goal_id": {"type": "string", "description": "Unique alphanumeric goal ID"},
                        "title": {"type": "string", "description": "High-level goal title"},
                        "description": {"type": "string", "description": "Detailed goal specification"},
                    },
                    "required": ["goal_id", "title"],
                },
            },
            {
                "name": "load_contract_file",
                "description": "Load, parse, and validate a contract JSON file from disk.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "contract_path": {
                            "type": "string",
                            "description": "Relative or absolute path to contract JSON",
                        },
                    },
                    "required": ["contract_path"],
                },
            },
            {
                "name": "run_contract_verification",
                "description": "Execute deterministic acceptance checks (files, regex, pytest commands) for a contract.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "contract_path": {"type": "string", "description": "Path to contract JSON file"},
                        "task_id": {"type": "string", "description": "Optional task identifier"},
                    },
                    "required": ["contract_path"],
                },
            },
            {
                "name": "run_quality_review",
                "description": "Run the multi-lens quality plane audit on contract outputs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "contract_path": {"type": "string", "description": "Path to contract JSON"},
                        "lens": {
                            "type": "string",
                            "description": "Quality lens to evaluate",
                            "enum": [
                                "code_correctness",
                                "security_hardening",
                                "documentation_clarity",
                                "test_completeness",
                            ],
                            "default": "code_correctness",
                        },
                    },
                    "required": ["contract_path"],
                },
            },
            {
                "name": "execute_supervisor_plan",
                "description": "Execute a full DAG plan using the fault-tolerant Supervisor loop with automated retries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "goal_id": {"type": "string", "description": "Goal ID to execute"},
                        "contracts": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "List of contract dictionaries defining the DAG",
                        },
                        "parallel": {
                            "type": "boolean",
                            "description": "Enable parallel worker pool execution",
                            "default": False,
                        },
                        "max_workers": {"type": "integer", "description": "Maximum concurrent workers", "default": 2},
                    },
                    "required": ["goal_id", "contracts"],
                },
            },
            {
                "name": "inspect_task_state",
                "description": "Inspect the internal event journal, evidence chain, and state for a specific task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task identifier to inspect"},
                        "run_dir": {"type": "string", "description": "Optional custom run directory"},
                    },
                    "required": ["task_id"],
                },
            },
            {
                "name": "reconcile_workspace_state",
                "description": "Audit and verify disk state, file hashes, and evidence integrity for a goal plan.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "goal_id": {"type": "string", "description": "Goal ID to reconcile"},
                        "contracts": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "List of plan contract dicts",
                        },
                    },
                    "required": ["goal_id", "contracts"],
                },
            },
            {
                "name": "get_system_health",
                "description": "Query letitloop engine health, active locks, and runtime configuration.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch tool calls to internal orchestrator logic."""
        if name == "create_goal":
            goal = Goal(
                goal_id=arguments["goal_id"],
                title=arguments["title"],
                description=arguments.get("description", ""),
            )
            return {"status": "SUCCESS", "goal": goal.to_dict()}

        elif name == "load_contract_file":
            path = arguments["contract_path"]
            if not os.path.isabs(path):
                path = os.path.join(self.workspace_root, path)
            contract, errors = load_contract(path, workspace_root=self.workspace_root)
            if errors:
                return {"status": "ERROR", "errors": errors}
            return {"status": "SUCCESS", "contract": contract.to_dict()}

        elif name == "run_contract_verification":
            path = arguments["contract_path"]
            if not os.path.isabs(path):
                path = os.path.join(self.workspace_root, path)
            contract, errors = load_contract(path, workspace_root=self.workspace_root)
            if errors or not contract:
                return {"status": "ERROR", "errors": errors or ["Failed to load contract"]}
            task_dir = os.path.join(self.run_dir, arguments.get("task_id", contract.task_id))
            passed, results, ev_path = run_verification(contract, self.workspace_root, task_dir)
            return {
                "status": "PASS" if passed else "FAIL",
                "all_passed": passed,
                "results": [r.to_dict() for r in results],
                "evidence_path": ev_path,
            }

        elif name == "run_quality_review":
            path = arguments["contract_path"]
            if not os.path.isabs(path):
                path = os.path.join(self.workspace_root, path)
            contract, errors = load_contract(path, workspace_root=self.workspace_root)
            if errors or not contract:
                return {"status": "ERROR", "errors": errors or ["Failed to load contract"]}

            lens = arguments.get("lens", "code_correctness")
            output_paths = [
                os.path.join(self.workspace_root, out["path"]) if not os.path.isabs(out["path"]) else out["path"]
                for out in contract.outputs
            ]
            qp = quality_plan_for_contract(contract.risk_tier, lens, contract.quality_spec)
            verdict = run_quality_plane(contract, output_paths, [], self.workspace_root, quality_plan=qp)
            return {
                "status": verdict.status,
                "passed": verdict.passed,
                "score": verdict.score,
                "issues": verdict.issues,
                "reason": verdict.reason,
            }

        elif name == "execute_supervisor_plan":
            goal = Goal(
                goal_id=arguments["goal_id"],
                title=f"Goal {arguments['goal_id']}",
                description="Executed via MCP server",
            )
            plan = Plan(goal_id=goal.goal_id, contracts=arguments["contracts"])
            sup = Supervisor(
                goal=goal,
                plan=plan,
                workspace_root=self.workspace_root,
                run_dir=self.run_dir,
                parallel=arguments.get("parallel", False),
                max_workers=arguments.get("max_workers", 2),
            )
            results = sup.execute_plan()
            return {
                "status": "SUCCESS" if goal.status == "COMPLETE" else "INCOMPLETE",
                "goal_status": goal.status,
                "results": results,
            }

        elif name == "inspect_task_state":
            task_id = arguments["task_id"]
            custom_run_dir = arguments.get("run_dir") or self.run_dir
            state_file = os.path.join(custom_run_dir, task_id, "state.json")
            if not os.path.isfile(state_file):
                return {"status": "NOT_FOUND", "message": f"No state file found at {state_file}"}
            st = load_state(state_file)
            return {
                "status": "SUCCESS",
                "task_id": st.task_id,
                "state_status": st.status,
                "attempt": st.attempt,
                "events_count": len(st.events),
                "evidence": st.evidence,
                "worker_results_count": len(st.worker_results),
            }

        elif name == "reconcile_workspace_state":
            goal_id = arguments["goal_id"]
            plan = Plan(goal_id=goal_id, contracts=arguments["contracts"])
            rep = run_reconciliation(goal_id, plan, self.workspace_root, self.run_dir)
            return {
                "status": "SUCCESS" if rep.passed else "ISSUES_FOUND",
                "passed": rep.passed,
                "checked_tasks": rep.checked_tasks,
                "total_tasks": rep.total_tasks,
                "issues": [asdict(i) for i in rep.issues],
            }

        elif name == "get_system_health":
            return {
                "status": "HEALTHY",
                "engine": "letitloop (LIL) 0.1.0",
                "workspace_root": self.workspace_root,
                "run_dir": self.run_dir,
                "python_version": sys.version,
                "pid": os.getpid(),
            }

        else:
            return {"status": "ERROR", "message": f"Unknown tool: {name}"}

    def run_stdio_server(self):
        """Run standard JSON-RPC stdio loop for MCP integration."""
        sys.stderr.write("[letitloop-mcp] Server starting on stdio...\n")
        sys.stderr.flush()

        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                req = json.loads(line)
                method = req.get("method", "")
                req_id = req.get("id")

                if method == "tools/list":
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"tools": self.get_tool_definitions()},
                    }
                elif method == "tools/call":
                    params = req.get("params", {})
                    tool_name = params.get("name", "")
                    tool_args = params.get("arguments", {})
                    out = self.handle_tool_call(tool_name, tool_args)
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]},
                    }
                elif method == "initialize":
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "letitloop-mcp", "version": "0.1.0"},
                        },
                    }
                else:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {},
                    }

                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": req.get("id") if isinstance(req, dict) else None,
                    "error": {"code": -32603, "message": str(e)},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


def main():
    server = LetItLoopMCPServer()
    server.run_stdio_server()


if __name__ == "__main__":
    main()
