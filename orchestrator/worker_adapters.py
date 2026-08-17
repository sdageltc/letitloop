"""Pluggable Worker Adapter Framework for letitloop.

Provides unified execution interfaces for various backend agents and CLIs:
Claude Code, OpenCode, Aider, Script executors, and Direct LLM APIs.
"""

import os
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseWorkerAdapter(ABC):
    """Abstract base worker adapter interface."""

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}

    @abstractmethod
    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 300) -> Dict[str, Any]:
        """Execute the worker on the given task and prompt."""
        raise NotImplementedError


class MockWorkerAdapter(BaseWorkerAdapter):
    """Deterministic mock worker for fast testing and dry-run execution."""

    def __init__(self, name: str = "mock", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.call_history: List[Dict[str, Any]] = []

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 300) -> Dict[str, Any]:
        result = {
            "exit_code": 0,
            "stdout": f"Mock execution succeeded for {task_id}",
            "stderr": "",
            "approach": "mock_default",
            "modified_files": [],
        }
        self.call_history.append({"task_id": task_id, "prompt": prompt})
        return result


class ScriptWorkerAdapter(BaseWorkerAdapter):
    """Executes a local Python or Shell script worker."""

    def __init__(self, script_command: str, name: str = "script", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.script_command = script_command

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 300) -> Dict[str, Any]:
        env = dict(os.environ)
        env["LIL_TASK_ID"] = task_id
        env["LIL_WORKSPACE_ROOT"] = workspace_root

        try:
            proc = subprocess.run(
                self.script_command,
                shell=True,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=workspace_root,
                timeout=timeout,
                env=env,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "approach": f"script:{self.script_command}",
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"Script execution timed out after {timeout} seconds",
                "approach": "timeout",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Failed to execute script worker: {e}",
                "approach": "error",
            }


class ClaudeCodeWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via the Claude Code CLI."""

    def __init__(self, name: str = "claude-code", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.cli_binary = self.config.get("binary", "claude")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        cmd = [self.cli_binary, "-p", prompt, "--dangerously-skip-permissions"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace_root,
                timeout=timeout,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "approach": "claude_code_autonomous",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Claude Code invocation error: {e}",
                "approach": "error",
            }


class WorkerRegistry:
    """Registry to register and resolve custom worker adapters dynamically."""

    _adapters: Dict[str, BaseWorkerAdapter] = {
        "mock": MockWorkerAdapter("mock"),
    }

    @classmethod
    def register(cls, name: str, adapter: BaseWorkerAdapter):
        cls._adapters[name] = adapter

    @classmethod
    def get(cls, name: str) -> Optional[BaseWorkerAdapter]:
        return cls._adapters.get(name)

    @classmethod
    def list_available(cls) -> List[str]:
        return list(cls._adapters.keys())
