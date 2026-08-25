"""Pluggable Worker Adapter Framework for letitloop.

Provides unified execution interfaces for various backend agents and CLIs:
Claude Code, Google Antigravity, OpenCode, Hermes Agent, Cline, Aider, Omniroute,
Script executors, Direct LLM APIs, Docker sandboxes, and local tool-calling LLMs.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from orchestrator import process_guard


class BaseWorkerAdapter(ABC):
    """Abstract base worker adapter interface."""

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}

    def is_available(self) -> bool:
        """Check if the worker CLI binary is available on system PATH."""
        import shutil

        binary = getattr(self, "cli_binary", self.name)
        return shutil.which(binary) is not None

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
            popen_kwargs = dict(
                stdin=subprocess.PIPE,
                text=True,
                cwd=workspace_root,
                env=env,
            )
            popen_kwargs.update(process_guard.containment_kwargs())
            proc = subprocess.Popen(  # nosec B602
                self.script_command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **popen_kwargs,
            )
            job = process_guard.attach_containment(proc)
            try:
                try:
                    stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
                except subprocess.TimeoutExpired:
                    # Kill the whole tree (children included) before reporting the timeout.
                    process_guard.kill_process_tree(proc.pid)
                    try:
                        proc.communicate(timeout=5)
                    except Exception:
                        pass
                    return {
                        "exit_code": 124,
                        "stdout": "",
                        "stderr": f"Script execution timed out after {timeout} seconds",
                        "approach": "timeout",
                    }
            finally:
                process_guard.close_job_handle(job)
            return {
                "exit_code": proc.returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "approach": f"script:{self.script_command}",
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
        import shutil

        executable = shutil.which(self.cli_binary) or self.cli_binary
        cmd = [executable, "-p", prompt, "--dangerously-skip-permissions"]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace_root,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
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


def _docker_host_path(host_path: str) -> str:
    """Normalize a host path for a docker -v spec (POSIX separators, no drive colon)."""
    posix = str(host_path).replace("\\", "/")
    # 'C:/ws' -> '/c/ws' style avoids the ambiguous drive-letter colon in -v specs.
    if len(posix) >= 2 and posix[1] == ":":
        posix = "/" + posix[0].lower() + posix[2:]
    return posix


class DockerWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks inside an isolated Docker sandbox container."""

    CONTAINER_WORKSPACE = "/workspace"

    def __init__(self, name: str = "docker", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config)
        self.image = self.config.get("image", "python:3.11-slim")
        self.network = self.config.get("network", "none")
        self.cpus = self.config.get("cpus", "1.0")
        self.memory = self.config.get("memory", "512m")
        self.script_command = self.config.get("script", 'cat "${LIL_INSTRUCTIONS}"')

    def is_available(self) -> bool:
        return self._docker_available()

    def _docker_available(self) -> bool:
        """Check the Docker CLI answers `docker info` (daemon reachable)."""
        try:
            proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _build_volumes(self, workspace_root: str) -> List[str]:
        """Build -v mount specs: each allow path rw, root ro unless fully covered."""
        ws_abs = os.path.abspath(workspace_root)
        scope = self.config.get("workspace_scope") or {}
        allows = [str(a).strip().replace("\\", "/") for a in scope.get("allow", []) if str(a).strip()]
        fully_covered = any(a in (".", "./") for a in allows)

        volumes = [(ws_abs, self.CONTAINER_WORKSPACE, "rw" if fully_covered else "ro")]
        seen = {ws_abs.lower()}
        for allow in allows:
            if allow in (".", "./"):
                continue
            host = os.path.normpath(os.path.join(ws_abs, allow))
            if host.lower() in seen:
                continue
            seen.add(host.lower())
            volumes.append((host, f"{self.CONTAINER_WORKSPACE}/{allow.rstrip('/')}", "rw"))
        return [f"{_docker_host_path(host)}:{container}:{mode}" for host, container, mode in volumes]

    def _build_run_argv(self, prompt: str, workspace_root: str, task_id: str) -> List[str]:
        """Build the full `docker run` argv and stage the brief as a read-only file."""
        scratch_dir = os.path.join(workspace_root, "scratch")
        os.makedirs(scratch_dir, exist_ok=True)
        instructions_name = f"docker_instructions_{task_id}.txt"
        with open(os.path.join(scratch_dir, instructions_name), "w", encoding="utf-8") as f:
            f.write(prompt)
        instructions_container = f"{self.CONTAINER_WORKSPACE}/scratch/{instructions_name}"

        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            str(self.network),
            "--cpus",
            str(self.cpus),
            "--memory",
            str(self.memory),
        ]
        for volume in self._build_volumes(workspace_root):
            argv.extend(["-v", volume])
        argv.extend(["-w", self.CONTAINER_WORKSPACE])
        argv.extend(
            [
                "-e",
                f"LIL_TASK_ID={task_id}",
                "-e",
                f"LIL_WORKSPACE_ROOT={self.CONTAINER_WORKSPACE}",
                "-e",
                f"LIL_INSTRUCTIONS={instructions_container}",
            ]
        )
        argv.append(self.image)
        argv.extend(["/bin/sh", "-c", self.script_command])
        return argv

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        if not self._docker_available():
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "Docker daemon unreachable: `docker info` failed or timed out; cannot run sandbox worker",
                "approach": "error",
            }
        try:
            cmd = self._build_run_argv(prompt, workspace_root, task_id)
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                cwd=workspace_root,
                timeout=timeout,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "approach": "docker_sandbox",
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"Docker sandbox execution timed out after {timeout} seconds",
                "approach": "timeout",
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Docker sandbox execution error: {e}",
                "approach": "error",
            }


REPAIR_NUDGE = (
    "Your previous response contained no valid tool call and no final answer. "
    "Either call one of the provided tools, or reply with your complete final answer as plain text."
)


class LocalToolWorkerAdapter(BaseWorkerAdapter):
    """Executes tasks via a local OpenAI-compatible LLM using native tool calling.

    Talks to Ollama / vLLM / LM Studio style endpoints ({base_url}/chat/completions),
    runs model tool calls against a sandboxed local registry, and journals every
    turn to <workspace_root>/scratch/orchestrator_runs/<task_id>/worker_output.log.
    """

    def __init__(
        self,
        name: str = "local-tool",
        config: Optional[Dict[str, Any]] = None,
        transport: Optional[Callable[..., Dict[str, Any]]] = None,
    ):
        super().__init__(name, config)
        self.base_url = self.config.get("base_url", "http://localhost:11434/v1")
        self.model = self.config.get("model")
        self.max_turns = int(self.config.get("max_turns", 8))
        self.api_key = self.config.get("api_key")
        self._transport = transport

    def is_available(self) -> bool:
        """Endpoint-backed adapter: available whenever a model is configured."""
        return bool(self.model)

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are an autonomous software engineering agent completing a scoped unit task. "
            "The user message contains the task objective and constraints.\n"
            "TOOL RULES:\n"
            "- Use the provided tools (read_file, write_file, replace_lines, execute_command) "
            "to inspect and modify the workspace.\n"
            "- Prefer acting through tools over printing code blocks.\n"
            "- When the task is complete, reply with ONLY a short plain-text final summary and no tool call.\n"
            "SCOPE LIMITS:\n"
            "- Only read or write files inside the workspace paths allowed by the task.\n"
            "- Never attempt to escape the workspace or access paths outside it.\n"
            "- Do not run git commands, deploys, or anything mutating files outside the workspace."
        )

    def _http_transport(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], timeout_s: int
    ) -> Dict[str, Any]:
        url = f"{str(self.base_url).rstrip('/')}/chat/completions"
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model, "messages": messages, "tools": tools}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # nosec B310
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:500]
            except OSError:
                pass
            raise RuntimeError(f"HTTP {e.code} from local LLM endpoint: {detail or e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"connection failed to local LLM endpoint: {e.reason}") from e
        return json.loads(raw)

    def _extract_native_tool_calls(self, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        for tc in message.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name")
            arguments = fn.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            if name:
                calls.append({"id": tc.get("id") or f"call_{len(calls)}", "name": name, "arguments": arguments})
        return calls

    @staticmethod
    def _invoke_tool(registry: Any, call: Dict[str, Any]) -> str:
        from .local_tool_calling import ToolCallingError

        try:
            result = registry.execute_call(call["name"], call["arguments"])
        except ToolCallingError as e:
            return f"TOOL ERROR: {e}"
        except Exception as e:
            return f"TOOL ERROR: {e}"
        if isinstance(result, dict):
            return json.dumps(result)
        return str(result)

    @staticmethod
    def _open_journal(workspace_root: str, task_id: str) -> str:
        run_dir = os.path.join(workspace_root, "scratch", "orchestrator_runs", task_id)
        os.makedirs(run_dir, exist_ok=True)
        return os.path.join(run_dir, "worker_output.log")

    @staticmethod
    def _append_journal(log_path: str, turn: int, exit_code: int, stdout_text: str, stderr_text: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"=== TURN {turn} ===\n")
            f.write(f"EXIT CODE: {exit_code}\n")
            f.write("--- STDOUT ---\n")
            f.write(stdout_text)
            f.write("\n--- STDERR ---\n")
            f.write(stderr_text)
            f.write("\n")

    def execute(self, prompt: str, workspace_root: str, task_id: str, timeout: int = 600) -> Dict[str, Any]:
        if not self.model:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": "LocalToolWorkerAdapter requires a 'model' config value",
                "approach": "error",
            }
        from .local_tool_calling import LocalToolRegistry, build_default_registry

        scope = self.config.get("workspace_scope") or {"allow": ["."], "deny": []}
        registry = build_default_registry(workspace_root, scope)
        tools = registry.get_openai_tools()
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": prompt},
        ]
        transport = self._transport or (lambda msgs, tls: self._http_transport(msgs, tls, timeout))
        journal_path = self._open_journal(workspace_root, task_id)
        repair_nudged = False

        try:
            for turn in range(1, self.max_turns + 1):
                data = transport(messages, tools)
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message", {}) if isinstance(choice, dict) else {}
                content = message.get("content") or ""
                calls = self._extract_native_tool_calls(message)
                native = bool(calls)
                if not calls:
                    calls = [
                        {"id": f"call_{idx}", "name": parsed["name"], "arguments": parsed.get("arguments") or {}}
                        for idx, parsed in enumerate(LocalToolRegistry.parse_tool_calls(content))
                    ]

                if not calls:
                    if str(content).strip():
                        self._append_journal(journal_path, turn, 0, f"FINAL ANSWER:\n{content}", "")
                        return {
                            "exit_code": 0,
                            "stdout": content,
                            "stderr": "",
                            "approach": "local_tool_calling",
                        }
                    if not repair_nudged:
                        repair_nudged = True
                        self._append_journal(
                            journal_path,
                            turn,
                            1,
                            "GARBAGE TURN: no usable tool call or final answer; sending repair nudge",
                            "no usable model response",
                        )
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": REPAIR_NUDGE})
                        continue
                    self._append_journal(
                        journal_path,
                        turn,
                        1,
                        "GARBAGE TURN persists after repair nudge; aborting",
                        "no usable model response after repair nudge",
                    )
                    return {
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "model produced no usable tool call or final answer after repair nudge",
                        "approach": "error",
                    }

                if native:
                    messages.append(message)
                else:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": [
                                {
                                    "id": c["id"],
                                    "type": "function",
                                    "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
                                }
                                for c in calls
                            ],
                        }
                    )

                summary_lines = []
                for call in calls:
                    result_text = self._invoke_tool(registry, call)
                    summary_lines.append(
                        f"{call['name']}({json.dumps(call['arguments'], sort_keys=True)}) -> {result_text[:500]}"
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": result_text,
                        }
                    )
                self._append_journal(journal_path, turn, 0, "\n".join(summary_lines), "")
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Local tool-calling execution error: {e}",
                "approach": "error",
            }

        self._append_journal(
            journal_path, self.max_turns, 1, "MAX TURNS reached without final answer", "max_turns exceeded"
        )
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Local tool-calling worker hit max_turns ({self.max_turns}) without a final answer",
            "approach": "error",
            "success": False,
            "reason": "max_turns",
        }


class WorkerRegistry:
    """Registry to register and resolve custom worker adapters dynamically."""

    _adapters: Dict[str, BaseWorkerAdapter] = {
        "mock": MockWorkerAdapter("mock"),
        "claude-code": ClaudeCodeWorkerAdapter("claude-code"),
        "claude": ClaudeCodeWorkerAdapter("claude"),
        "antigravity-cli": AntigravityCliWorkerAdapter("antigravity-cli"),
        "antigravity": AntigravityCliWorkerAdapter("antigravity"),
        "agy": AntigravityCliWorkerAdapter("agy"),
        "opencode": OpenCodeWorkerAdapter("opencode"),
        "hermes": HermesWorkerAdapter("hermes"),
        "cline": ClineWorkerAdapter("cline"),
        "aider": AiderWorkerAdapter("aider"),
        "omniroute": OmnirouteWorkerAdapter("omniroute"),
        "codex": CodexWorkerAdapter("codex"),
        "local-tool": LocalToolWorkerAdapter("local-tool"),
        "docker": DockerWorkerAdapter("docker"),
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
