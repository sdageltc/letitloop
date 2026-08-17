"""Centralized model string registry. Single source of truth for all model IDs.

Model strings use a provider prefix (see ``orchestrator.llm`` for the transport
table). The defaults below are intentionally cheap, widely available public
models; every one of them can be overridden per-run via environment variables:

    WORKER_MODEL    worker / executor default
    QC_MODEL        QC reviewer default
    PLANNER_MODEL   planner default
"""

import os


class ModelRegistry:
    WORKER = "gemini-3.6-flash"
    QC = "gemini-3.1-pro"
    WORKER_PREFIXED = f"gemini:{WORKER}"
    QC_PREFIXED = f"gemini:{QC}"
    HYBRID = f"hybrid:{WORKER_PREFIXED}"
    FALLBACK = "openai:gpt-4o-mini"
    KIMI = "kimi-k2"
    OPUS = "claude-opus-4-1"

    @classmethod
    def default_worker(cls) -> str:
        """Effective worker model (env WORKER_MODEL wins over the registry default)."""
        return os.environ.get("WORKER_MODEL", cls.WORKER_PREFIXED)

    @classmethod
    def default_qc(cls) -> str:
        """Effective QC model (env QC_MODEL wins)."""
        return os.environ.get("QC_MODEL", cls.QC_PREFIXED)

    @classmethod
    def prefixed(cls, model: str = None) -> str:
        return f"gemini:{model or cls.WORKER}"

    @classmethod
    def hybrid(cls, model: str = None) -> str:
        return f"hybrid:gemini:{model or cls.WORKER}"

    @classmethod
    def is_hybrid(cls, model: str) -> bool:
        return model.startswith("hybrid:")

    @classmethod
    def strip_hybrid_prefix(cls, model: str) -> str:
        return model[7:] if model.startswith("hybrid:") else model
