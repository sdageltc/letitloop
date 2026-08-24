"""Centralized model string registry and dynamic thinking budget configuration.

Single source of truth for all model IDs, provider routing, and model-specific compute allocation.
Ground truth updated for 2026 frontier model generations:
- OpenAI: GPT-5.6 Series (Sol, Terra, Luna, Cyber), o3, o3-mini, o4-mini, o1
- Anthropic: Claude 5 Series (Opus 5, Sonnet 5, Fable 5, Haiku 4.5), Claude 3.7 Sonnet
- Google: Gemini 3 Series (3.7 Flash, 3.6 Flash, 3.5 Flash Lite, 3.1 Pro), Gemini 2.5 Pro / Flash
- DeepSeek: V4 Series (DeepSeek-V4 Pro, DeepSeek-V4 Flash), DeepSeek-Chat (V3), DeepSeek-Reasoner (R1)
- Kimi: Kimi K3, Kimi K2
- Gateways: Omniroute, OpenRouter, Groq, Ollama
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Set


class ThinkingBudget:
    """Dynamic thinking budget token targets across execution phases."""

    PLANNING = 4096  # High thinking for deep architectural DAG planning
    QC = 2048  # Deep verification and multi-lens critique
    WORKER_STANDARD = 0  # 0 thinking tokens for instantaneous code edits / tests (<3s)
    WORKER_COMPLEX = 1024  # Targeted reasoning for multi-file refactors

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
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.6-cyber",
        "o1",
        "o1-mini",
        "o1-preview",
        "o3",
        "o3-mini",
        "o4-mini",
    }

    THINKING_MODELS_ANTHROPIC: Set[str] = {
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-haiku-4-5",
        "claude-3-7-sonnet",
        "claude-3-7-sonnet-20250219",
        "claude-3-7-sonnet-latest",
        "claude-3.7-sonnet",
        "claude-opus-4",
        "claude-sonnet-4",
    }

    THINKING_MODELS_GEMINI: Set[str] = {
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-3.7-flash-latest",
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
        - OpenAI: passes reasoning_effort ('low', 'medium', 'high') to reasoning models (gpt-5.6 series, o1, o3, o4);
                  never passes reasoning_effort to standard non-reasoning models (gpt-4o, gpt-4o-mini) to prevent 400 errors.
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
    WORKER = "gemini-3.7-flash"
    QC = "gemini-3.1-pro"
    WORKER_PREFIXED = f"gemini:{WORKER}"
    QC_PREFIXED = f"gemini:{QC}"
    WORKER_FALLBACK = "gemini:gemini-3.6-flash"
    QC_FALLBACK = "gemini:gemini-3.7-flash"
    HYBRID = f"hybrid:{WORKER_PREFIXED}"
    FALLBACK = "gemini:gemini-3.6-flash"
    OPUS = "claude-opus-5"
    KIMI = "kimi-k3"

    # OpenAI 2026 Generation
    GPT_SOL = "openai:gpt-5.6-sol"
    GPT_TERRA = "openai:gpt-5.6-terra"
    GPT_LUNA = "openai:gpt-5.6-luna"
    GPT_CYBER = "openai:gpt-5.6-cyber"
    O3 = "openai:o3"
    O3_MINI = "openai:o3-mini"
    O4_MINI = "openai:o4-mini"
    O1 = "openai:o1"
    O1_MINI = "openai:o1-mini"
    GPT_4O = "openai:gpt-4o"
    GPT_4O_MINI = "openai:gpt-4o-mini"

    # Anthropic Claude 5 Generation
    CLAUDE_OPUS_5 = "anthropic:claude-opus-5"
    CLAUDE_SONNET_5 = "anthropic:claude-sonnet-5"
    CLAUDE_FABLE_5 = "anthropic:claude-fable-5"
    CLAUDE_HAIKU_4_5 = "anthropic:claude-haiku-4-5"
    CLAUDE_3_7_SONNET = "anthropic:claude-3-7-sonnet-latest"
    CLAUDE_3_5_SONNET = "anthropic:claude-3-5-sonnet-latest"
    CLAUDE_3_OPUS = "anthropic:claude-3-opus-latest"

    # Google Gemini 3 Generation
    GEMINI_3_7_FLASH = "gemini:gemini-3.7-flash"
    GEMINI_3_6_FLASH = "gemini:gemini-3.6-flash"
    GEMINI_3_5_FLASH_LITE = "gemini:gemini-3.5-flash-lite"
    GEMINI_3_1_PRO = "gemini:gemini-3.1-pro"
    GEMINI_2_5_PRO = "gemini:gemini-2.5-pro"
    GEMINI_2_5_FLASH = "gemini:gemini-2.5-flash"

    # DeepSeek V4 Generation
    DEEPSEEK_V4_PRO = "deepseek:deepseek-v4-pro"
    DEEPSEEK_V4_FLASH = "deepseek:deepseek-v4-flash"
    DEEPSEEK_CHAT = "deepseek:deepseek-chat"
    DEEPSEEK_REASONER = "deepseek:deepseek-reasoner"

    # Kimi Generation
    KIMI_K3 = "kimi:kimi-k3"
    KIMI_K2 = "kimi:kimi-k2"

    # Gateways
    OMNIROUTE_AUTO = "omniroute:auto"

    @classmethod
    def default_worker(cls) -> str:
        """Effective worker model (env WORKER_MODEL wins over default)."""
        if "WORKER_MODEL" in os.environ:
            return os.environ["WORKER_MODEL"]
        return cls.WORKER_PREFIXED

    @classmethod
    def default_qc(cls) -> str:
        """Effective QC model (env QC_MODEL wins over default)."""
        if "QC_MODEL" in os.environ:
            return os.environ["QC_MODEL"]
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


# ---------------------------------------------------------------------------
# Cost/risk tiering (issue #19): cheap/standard/frontier ladders used by the
# risk-aware provider router. Tier 1 = cheapest, tier 3 = most capable.

MODEL_TIERS: Dict[int, List[str]] = {
    1: ["gemini-2.5-flash-lite", "qwen2.5-coder"],
    2: ["gemini-2.5-flash", "claude-3-5-haiku", "gpt-4o-mini"],
    3: ["claude-3-5-sonnet", "gpt-4o", "deepseek-r1"],
}

_TIER_ONE_RISKS = ("trivial", "boilerplate", "format")
_TIER_THREE_RISKS = ("crucible", "architecture", "high_risk")

# Ordered cheapest-first; substring families checked tier by tier so that
# specific families ("flash-lite") win over generic ones ("flash").
_TIER_FAMILIES: Dict[int, tuple] = {
    1: ("flash-lite", "flashlite", "qwen"),
    2: ("haiku", "4o-mini"),
    3: ("sonnet", "opus", "gpt-4o", "deepseek", "reasoner"),
}


def _bare_model_id(model: str) -> str:
    """Lowercased bare model id with provider/hybrid prefixes stripped."""
    text = (model or "").strip().lower()
    while ":" in text:
        head, _, tail = text.partition(":")
        if not tail:
            break
        text = tail
    return text


def model_tiers() -> Dict[int, List[str]]:
    """Effective tier ladder; LETITLOOP_MODEL_TIERS JSON overrides the default."""
    raw = os.environ.get("LETITLOOP_MODEL_TIERS", "")
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed:
            try:
                overridden = {int(tier): [str(m) for m in models] for tier, models in parsed.items() if models}
            except (TypeError, ValueError):
                overridden = {}
            if overridden:
                return overridden
    return {tier: list(models) for tier, models in MODEL_TIERS.items()}


def classify_model(model: str) -> int:
    """Classify a model id into its cost tier (1 cheapest .. 3 frontier).

    Exact ladder membership first, then substring/family match against known
    families. Unknown models default to tier 2 (standard).
    """
    bare = _bare_model_id(model)
    tiers = model_tiers()
    ordered = sorted(tiers)
    for tier in ordered:
        if bare in {_bare_model_id(m) for m in tiers[tier]}:
            return int(tier)
    for tier in ordered:
        if any(family in bare for family in _TIER_FAMILIES.get(int(tier), ())):
            return int(tier)
    return 2


def tier_for_risk(risk_hint: str) -> int:
    """Map a task risk hint to a target tier ('' / unknown hints default to 2)."""
    hint = (risk_hint or "").strip().lower().replace("-", "_")
    if hint in _TIER_ONE_RISKS:
        return 1
    if hint in _TIER_THREE_RISKS:
        return 3
    return 2


def escalation_ladder(start_model: Optional[str] = None, start_tier: Optional[int] = None) -> List[str]:
    """Ordered deduped candidate sequence ascending from the starting tier.

    The starting model (when given) heads the ladder, followed by the rest of
    its tier and then every higher tier in order.
    """
    tiers = model_tiers()
    lo, hi = min(tiers), max(tiers)
    if start_tier is None:
        start_tier = classify_model(start_model) if start_model else lo
    try:
        base = int(start_tier)
    except (TypeError, ValueError):
        base = lo
    base = max(lo, min(hi, base))
    sequence: List[str] = []
    if start_model and start_model.strip():
        sequence.append(start_model.strip())
    for tier in range(base, hi + 1):
        sequence.extend(tiers.get(tier, []))
    seen: Set[str] = set()
    ladder: List[str] = []
    for candidate in sequence:
        key = _bare_model_id(candidate)
        if key and key not in seen:
            seen.add(key)
            ladder.append(candidate)
    return ladder
