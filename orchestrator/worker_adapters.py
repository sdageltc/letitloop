"""Pluggable Worker Adapter Framework for letitloop.

Provides unified execution interfaces for various backend agents and CLIs:
Claude Code, Google Antigravity, OpenCode, Hermes Agent, Cline, Aider, Omniroute,
Script executors, and Direct LLM APIs.
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
        if not os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("LIL_TEST_MODE"):
            import sys

            print(
                f"[SECURITY WARNING] Running with MockWorkerAdapter on task {task_id}. Execution is simulated!",
                file=sys.stderr,
            )

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


class AntigravityCliWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via the Google Antigravity CLI (`agy`)."""

    def __init__(self, name: str = "antigravity-cli", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.cli_binary = self.config.get("binary", "agy")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        cmd = [self.cli_binary, "exec", "--prompt", prompt, "--dir", workspace_root]
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
                "approach": "antigravity_cli_exec",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Antigravity CLI execution error: {e}",
                "approach": "error",
            }


class OpenCodeWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via the OpenCode CLI."""

    def __init__(self, name: str = "opencode", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.cli_binary = self.config.get("binary", "opencode")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        cmd = [self.cli_binary, "run", "--prompt", prompt, "--workspace", workspace_root]
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
                "approach": "opencode_run",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"OpenCode execution error: {e}",
                "approach": "error",
            }


class HermesWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via the Nous Research Hermes Agent CLI."""

    def __init__(self, name: str = "hermes", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.cli_binary = self.config.get("binary", "hermes")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        cmd = [self.cli_binary, "exec", "--prompt", prompt, "--path", workspace_root]
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
                "approach": "hermes_agent_exec",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Hermes Agent execution error: {e}",
                "approach": "error",
            }


class ClineWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via the Cline CLI / headless runner."""

    def __init__(self, name: str = "cline", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.cli_binary = self.config.get("binary", "cline")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        cmd = [self.cli_binary, "--prompt", prompt, "--cwd", workspace_root, "--yes"]
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
                "approach": "cline_autonomous",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Cline execution error: {e}",
                "approach": "error",
            }


class AiderWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via the Aider CLI pair programmer."""

    def __init__(self, name: str = "aider", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.cli_binary = self.config.get("binary", "aider")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        cmd = [self.cli_binary, "--message", prompt, "--no-git", "--yes-always"]
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
                "approach": "aider_pair_programmer",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Aider execution error: {e}",
                "approach": "error",
            }


class OmnirouteWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks through the Omniroute routing gateway."""

    def __init__(self, name: str = "omniroute", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.endpoint = self.config.get("base_url", "http://localhost:8000/v1")
        self.model = self.config.get("model", "omniroute:auto")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 300) -> Dict[str, Any]:
        from .llm import call_llm

        try:
            response = call_llm(
                prompt=prompt,
                model=self.model,
                timeout_s=timeout,
                system_prompt="You are an autonomous software engineering agent tasked with completing a scoped unit task.",
            )
            return {
                "exit_code": 0,
                "stdout": response,
                "stderr": "",
                "approach": "omniroute_gateway",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Omniroute gateway execution error: {e}",
                "approach": "error",
            }


class CodexWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via the OpenAI Codex CLI."""

    def __init__(self, name: str = "codex", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.cli_binary = self.config.get("binary", "codex")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        cmd = [self.cli_binary, "exec", "--prompt", prompt, "--path", workspace_root]
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
                "approach": "codex_cli_exec",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Codex CLI execution error: {e}",
                "approach": "error",
            }


class WorkerRegistry:
    """Registry to register and resolve custom worker adapters dynamically."""

    _adapters: Dict[str, BaseWorkerAdapter] = {
        "mock": MockWorkerAdapter("mock"),
        "claude-code": ClaudeCodeWorkerAdapter("claude-code"),
        "antigravity-cli": AntigravityCliWorkerAdapter("antigravity-cli"),
        "opencode": OpenCodeWorkerAdapter("opencode"),
        "hermes": HermesWorkerAdapter("hermes"),
        "cline": ClineWorkerAdapter("cline"),
        "aider": AiderWorkerAdapter("aider"),
        "omniroute": OmnirouteWorkerAdapter("omniroute"),
        "codex": CodexWorkerAdapter("codex"),
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
