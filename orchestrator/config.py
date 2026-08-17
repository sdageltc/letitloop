"""Centralized configuration for the orchestrator.

Supports env vars, JSON config file, and programmatic overrides.
All settings fall back to sensible defaults.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .models import ModelRegistry

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__))))
DEFAULT_RUN_DIR = os.path.join(WORKSPACE_ROOT, "scratch", "orchestrator_runs")


@dataclass
class WorkerConfig:
    model: str = ModelRegistry.default_worker()
    max_attempts: int = 3
    timeout_sec: int = 300
    max_output_size: int = 524288


@dataclass
class PoolConfig:
    enabled: bool = False
    max_workers: int = 3


@dataclass
class ScopeConfig:
    allow: List[str] = field(default_factory=lambda: ["scratch/"])
    deny: List[str] = field(default_factory=lambda: ["AGENTS.md", "memory/", ".opencode/"])


@dataclass
class QCConfig:
    required: bool = False
    lens: str = "code_correctness"


@dataclass
class LockConfig:
    stale_timeout_sec: int = 300


@dataclass
class OrchestratorConfig:
    workspace_root: str = WORKSPACE_ROOT
    run_dir: str = ""
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    pool: PoolConfig = field(default_factory=PoolConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    qc: QCConfig = field(default_factory=QCConfig)
    lock: LockConfig = field(default_factory=LockConfig)

    def __post_init__(self):
        if not self.run_dir:
            self.run_dir = DEFAULT_RUN_DIR

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OrchestratorConfig":
        worker_d = d.get("worker", {})
        pool_d = d.get("pool", {})
        scope_d = d.get("scope", {})
        qc_d = d.get("qc", {})
        lock_d = d.get("lock", {})

        cfg = cls(
            workspace_root=d.get("workspace_root", WORKSPACE_ROOT),
            run_dir=d.get("run_dir", ""),
        )
        cfg.worker = WorkerConfig(**worker_d) if worker_d else cfg.worker
        cfg.pool = PoolConfig(**pool_d) if pool_d else cfg.pool
        cfg.scope = ScopeConfig(**scope_d) if scope_d else cfg.scope
        cfg.qc = QCConfig(**qc_d) if qc_d else cfg.qc
        cfg.lock = LockConfig(**lock_d) if lock_d else cfg.lock
        return cfg

    @classmethod
    def load(cls, path: str = "") -> "OrchestratorConfig":
        cfg = cls()
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cfg = cls.from_dict(data)
            except (OSError, json.JSONDecodeError):
                pass

        cfg._apply_env()
        return cfg

    def _apply_env(self):
        if "ORCHESTRATOR_RUN_DIR" in os.environ:
            self.run_dir = os.environ["ORCHESTRATOR_RUN_DIR"]
        if "ORCHESTRATOR_MODEL" in os.environ:
            self.worker.model = os.environ["ORCHESTRATOR_MODEL"]
        if "ORCHESTRATOR_MAX_ATTEMPTS" in os.environ:
            try:
                self.worker.max_attempts = int(os.environ["ORCHESTRATOR_MAX_ATTEMPTS"])
            except ValueError:
                pass
        if "ORCHESTRATOR_TIMEOUT" in os.environ:
            try:
                self.worker.timeout_sec = int(os.environ["ORCHESTRATOR_TIMEOUT"])
            except ValueError:
                pass
        if "ORCHESTRATOR_PARALLEL" in os.environ:
            val = os.environ["ORCHESTRATOR_PARALLEL"].lower()
            self.pool.enabled = val in ("1", "true", "yes")
        if "ORCHESTRATOR_MAX_WORKERS" in os.environ:
            try:
                self.pool.max_workers = int(os.environ["ORCHESTRATOR_MAX_WORKERS"])
            except ValueError:
                pass

    def display(self) -> str:
        def _fmt(v):
            if isinstance(v, list):
                return ", ".join(v) if v else "(none)"
            return str(v)

        lines = ["=== Orchestrator Configuration ==="]
        lines.append(f"  workspace: {self.workspace_root}")
        lines.append(f"  run_dir:   {self.run_dir}")
        lines.append("")
        lines.append("  Worker:")
        lines.append(f"    model:         {self.worker.model}")
        lines.append(f"    max_attempts:  {self.worker.max_attempts}")
        lines.append(f"    timeout_sec:   {self.worker.timeout_sec}")
        lines.append("")
        lines.append("  Pool:")
        lines.append(f"    enabled:       {self.pool.enabled}")
        lines.append(f"    max_workers:   {self.pool.max_workers}")
        lines.append("")
        lines.append("  Scope:")
        lines.append(f"    allow:         {_fmt(self.scope.allow)}")
        lines.append(f"    deny:          {_fmt(self.scope.deny)}")
        lines.append("")
        lines.append("  QC:")
        lines.append(f"    required:      {self.qc.required}")
        lines.append(f"    lens:          {self.qc.lens}")
        lines.append("")
        lines.append("  Lock:")
        lines.append(f"    stale_timeout: {self.lock.stale_timeout_sec}s")
        return "\n".join(lines)
