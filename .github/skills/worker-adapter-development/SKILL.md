---
name: worker-adapter-development
description: Guide for developing, testing, and hardening worker execution adapters (Claude Code, Agy, OpenCode, Hermes, Cline, Aider, Docker) in letitloop.
metadata:
  author: letitloop-maintainers
  version: "1.0.0"
compatibility: Cross-platform (Subprocess & Worker API)
---

# Worker Adapter Development Guide for letitloop

Worker adapters (`orchestrator/worker_adapters.py`) bridge external agent CLIs, container engines, or LLM APIs into `letitloop`'s deterministic contract execution loop.

## Base Worker Interface

Every worker adapter MUST inherit from `BaseWorkerAdapter` and implement `execute()`:

```python
from typing import Any, Dict
from .worker_adapters import BaseWorkerAdapter

class CustomWorkerAdapter(BaseWorkerAdapter):
    """Custom worker adapter implementation."""

    def __init__(self, model_name: str, config: Dict[str, Any] = None):
        super().__init__(model_name)
        self.config = config or {}

    def execute(
        self,
        prompt: str,
        workspace_root: str,
        task_id: str,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """Execute the worker and return standardized execution dictionary.
        
        Returns:
            {
                "exit_code": int (0 for success, non-zero for failure),
                "stdout": str,
                "stderr": str,
                "cost_usd": float (optional),
                "tokens_used": int (optional)
            }
        """
        ...
```

## Security & Reliability Guidelines for Adapters

1. **Subprocess Management & Environment Scrubbing**:
   - Scrub sensitive tokens from the child environment unless explicitly required.
   - Set `LIL_TASK_ID` and `LIL_WORKSPACE_ROOT` in the child environment.
   - Use `subprocess.run(..., capture_output=True, text=True, timeout=timeout)`.
2. **Process Tree Cleanup on Timeout**:
   - Always catch `subprocess.TimeoutExpired` and invoke process tree killing (`_kill_process_tree`) to prevent orphan runaway processes.
3. **Bandit Security Annotation**:
   - If using `shell=True` for user-configured script worker execution, annotate with `# nosec B602`.
