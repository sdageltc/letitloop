"""Centralized model string registry and dynamic thinking budget configuration.

Single source of truth for all verified official model IDs, provider routing, and model-specific compute allocation.
Verified official provider endpoints:
- Google: Gemini 2.5 Pro / Flash, Gemini 2.0 Flash / Lite, Gemini 1.5 Pro / Flash, Gemini 3.7 Flash
- Anthropic: Claude 3.7 Sonnet, Claude 3.5 Sonnet, Claude 3.5 Haiku, Claude 3 Opus
- OpenAI: GPT-4o, GPT-4o-mini, o1, o1-mini, o3-mini, GPT-4-turbo
- DeepSeek: DeepSeek-Chat (V3), DeepSeek-Reasoner (R1)
- Gateways: Omniroute, OpenRouter, Groq, Ollama
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Set


class ThinkingBudget:
    """Dynamic thinking budget token targets across execution phases."""

    PLANNING = 4096       # High thinking for deep architectural DAG planning
    QC = 2048             # Deep verification and multi-lens critique
    WORKER_STANDARD = 0   # 0 thinking tokens for instantaneous code edits / tests (<3s)
    WORKER_COMPLEX = 1024 # Targeted reasoning for multi-file refactors

    @classmethod
    def budget_for(cls, phase_or_task_type: str) -> int:
        """Resolve dynamic thinking token budget for a given phase or task type."""
        phase = (phase_or_task_type or "").lower()
        if "plan" in phase or "propose" in phase or "architect" in phase:
            return cls.PLANNING
        if "qc" in phase or "critique" in phase or "audit" in phase or "review" in phase:
            return cls.QC
        if "complex" in phase or "refactor" in phase:
            return cls.WORKER_COMPLEX
        return cls.WORKER_STANDARD


class ModelThinkingConfig:
    """Model-specific dynamic thinking and reasoning configuration engine."""

    REASONING_MODELS_OPENAI: Set[str] = {
        "o1", "o1-mini", "o1-preview", "o3-mini"
    }

    THINKING_MODELS_ANTHROPIC: Set[str] = {
        "claude-3-7-sonnet", "claude-3-7-sonnet-20250219", "claude-3-7-sonnet-latest", "claude-3.7-sonnet"
    }

    THINKING_MODELS_GEMINI: Set[str] = {
        "gemini-3.7-flash", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.7-flash-latest"
    }

    @classmethod
    def is_anthropic_thinking_model(cls, model_id: str) -> bool:
        mid = model_id.lower()
        return any(m in mid for m in cls.THINKING_MODELS_ANTHROPIC)

    @classmethod
    def is_openai_reasoning_model(cls, model_id: str) -> bool:
        mid = model_id.lower()
        return any(m in mid for m in cls.REASONING_MODELS_OPENAI)

    @classmethod
    def is_gemini_thinking_model(cls, model_id: str) -> bool:
        mid = model_id.lower()
        return any(m in mid for m in cls.THINKING_MODELS_GEMINI)

    @classmethod
    def apply_thinking_config(
        cls,
        model_id: str,
        provider: str,
        payload: Dict[str, Any],
        thinking_budget: Optional[int] = None,
        phase: str = "worker",
    ) -> None:
        """Mutate payload in-place to apply model-safe thinking / reasoning parameters.

        Enforces strict provider rules:
        - Anthropic: budget_tokens >= 1024 if enabled, max_tokens > budget_tokens, no temperature != 1.0.
                     If budget == 0 (standard worker), thinking is cleanly omitted for sub-3s response.
        - Gemini: passes extra_body.google.thinking_config.thinking_budget (0 for instant response).
        - OpenAI: only passes reasoning_effort ('low', 'medium', 'high') to reasoning models (o1, o3-mini);
                  never passes reasoning_effort to standard models (gpt-4o, gpt-4o-mini) to prevent 400 errors.
        """
        budget = thinking_budget if thinking_budget is not None else ThinkingBudget.budget_for(phase)

        if provider == "anthropic":
            if cls.is_anthropic_thinking_model(model_id) and budget >= 1024:
                payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
                current_max = payload.get("max_tokens", 4096)
                if current_max <= budget:
                    payload["max_tokens"] = budget + 2048
                payload.pop("temperature", None)
            else:
                payload.pop("thinking", None)

        elif provider == "gemini":
            if cls.is_gemini_thinking_model(model_id) or "gemini" in model_id.lower():
                payload["extra_body"] = {"google": {"thinking_config": {"thinking_budget": budget}}}
                if budget > 0:
                    current_max = payload.get("max_tokens", 4096)
                    if current_max <= budget:
                        payload["max_tokens"] = budget + 4096

        elif provider == "openai":
            if cls.is_openai_reasoning_model(model_id):
                if budget == 0:
                    payload["reasoning_effort"] = "low"
                elif budget >= 4096:
                    payload["reasoning_effort"] = "high"
                else:
                    payload["reasoning_effort"] = "medium"
            else:
                payload.pop("reasoning_effort", None)


class ModelRegistry:
    # Default workhorse & QC reviewer
    WORKER = "gemini-2.5-flash"
    QC = "gemini-2.5-pro"
    WORKER_PREFIXED = f"gemini:{WORKER}"
    QC_PREFIXED = f"gemini:{QC}"
    WORKER_FALLBACK = "gemini:gemini-2.0-flash-lite"
    QC_FALLBACK = "gemini:gemini-2.5-flash"
    HYBRID = f"hybrid:{WORKER_PREFIXED}"
    FALLBACK = "gemini:gemini-2.0-flash-lite"
    OPUS = "claude-3-opus-latest"
    KIMI = "kimi-k2"

    # OpenAI Family (Official)
    GPT_4O = "openai:gpt-4o"
    GPT_4O_MINI = "openai:gpt-4o-mini"
    O1 = "openai:o1"
    O1_MINI = "openai:o1-mini"
    O3_MINI = "openai:o3-mini"
    GPT_4_TURBO = "openai:gpt-4-turbo"

    # Anthropic Claude Family (Official)
    CLAUDE_3_7_SONNET = "anthropic:claude-3-7-sonnet-latest"
    CLAUDE_3_5_SONNET = "anthropic:claude-3-5-sonnet-latest"
    CLAUDE_3_5_HAIKU = "anthropic:claude-3-5-haiku-latest"
    CLAUDE_3_OPUS = "anthropic:claude-3-opus-latest"

    # Google Gemini Family (Official)
    GEMINI_3_7_FLASH = "gemini:gemini-3.7-flash"
    GEMINI_2_5_PRO = "gemini:gemini-2.5-pro"
    GEMINI_2_5_FLASH = "gemini:gemini-2.5-flash"
    GEMINI_2_0_FLASH = "gemini:gemini-2.0-flash"
    GEMINI_2_0_FLASH_LITE = "gemini:gemini-2.0-flash-lite"
    GEMINI_1_5_PRO = "gemini:gemini-1.5-pro"
    GEMINI_1_5_FLASH = "gemini:gemini-1.5-flash"

    # DeepSeek Family (Official)
    DEEPSEEK_CHAT = "deepseek:deepseek-chat"
    DEEPSEEK_REASONER = "deepseek:deepseek-reasoner"

    # Gateways
    OMNIROUTE_AUTO = "omniroute:auto"

    @classmethod
    def default_worker(cls) -> str:
        """Effective worker model (env WORKER_MODEL wins over auto-detected configured provider)."""
        if "WORKER_MODEL" in os.environ:
            return os.environ["WORKER_MODEL"]
        if os.environ.get("GEMINI_API_KEY"):
            return cls.WORKER_PREFIXED
        if os.environ.get("OPENAI_API_KEY"):
            return "openai:gpt-4o-mini"
        if os.environ.get("DEEPSEEK_API_KEY"):
            return "deepseek:deepseek-chat"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic:claude-3-5-sonnet-latest"
        return cls.WORKER_PREFIXED

    @classmethod
    def default_qc(cls) -> str:
        """Effective QC model (env QC_MODEL wins over auto-detected configured provider)."""
        if "QC_MODEL" in os.environ:
            return os.environ["QC_MODEL"]
        if os.environ.get("GEMINI_API_KEY"):
            return cls.QC_PREFIXED
        if os.environ.get("OPENAI_API_KEY"):
            return "openai:gpt-4o"
        if os.environ.get("DEEPSEEK_API_KEY"):
            return "deepseek:deepseek-reasoner"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic:claude-3-5-sonnet-latest"
        return cls.QC_PREFIXED

    @classmethod
    def prefixed(cls, model: Optional[str] = None) -> str:
        return f"gemini:{model or cls.WORKER}"

    @classmethod
    def hybrid(cls, model: Optional[str] = None) -> str:
        return f"hybrid:gemini:{model or cls.WORKER}"

    @classmethod
    def is_hybrid(cls, model: str) -> bool:
        return model.startswith("hybrid:")

    @classmethod
    def strip_hybrid_prefix(cls, model: str) -> str:
        return model[7:] if model.startswith("hybrid:") else model
