"""Centralized model string registry. Single source of truth for all model IDs.

Ground truth updated for 2026 frontier model generations:
- OpenAI: GPT-5.6 Series (Sol, Terra, Luna, Cyber)
- Anthropic: Claude 5 Series (Opus 5, Sonnet 5, Fable 5, Haiku 4.5)
- Google: Gemini 3 Series (3.7 Flash, 3.6 Flash, 3.1 Pro)
- DeepSeek: V4 Series (DeepSeek-V4 Pro, DeepSeek-V4 Flash)
- Gateways: Omniroute, OpenRouter, Groq, Ollama
"""

import os


class ModelRegistry:
    # Default workhorse & QC reviewer
    WORKER = "gemini-3.7-flash"
    QC = "gemini-3.1-pro"
    WORKER_PREFIXED = f"gemini:{WORKER}"
    QC_PREFIXED = f"gemini:{QC}"
    HYBRID = f"hybrid:{WORKER_PREFIXED}"
    FALLBACK = "openai:gpt-5.6-luna"

    # OpenAI GPT-5.6 Family (2026)
    GPT_SOL = "openai:gpt-5.6-sol"
    GPT_TERRA = "openai:gpt-5.6-terra"
    GPT_LUNA = "openai:gpt-5.6-luna"
    GPT_CYBER = "openai:gpt-5.6-cyber"

    # Anthropic Claude 5 Family (2026)
    CLAUDE_OPUS_5 = "anthropic:claude-opus-5"
    CLAUDE_SONNET_5 = "anthropic:claude-sonnet-5"
    CLAUDE_FABLE_5 = "anthropic:claude-fable-5"
    CLAUDE_HAIKU_4_5 = "anthropic:claude-haiku-4-5"

    # Google Gemini 3 Family (2026)
    GEMINI_3_7_FLASH = "gemini:gemini-3.7-flash"
    GEMINI_3_6_FLASH = "gemini:gemini-3.6-flash"
    GEMINI_3_1_PRO = "gemini:gemini-3.1-pro"
    GEMINI_2_5_PRO = "gemini:gemini-2.5-pro"

    # DeepSeek V4 Family (2026)
    DEEPSEEK_V4_PRO = "deepseek:deepseek-v4-pro"
    DEEPSEEK_V4_FLASH = "deepseek:deepseek-v4-flash"
    DEEPSEEK_CHAT = "deepseek:deepseek-chat"
    DEEPSEEK_REASONER = "deepseek:deepseek-reasoner"

    # Moonshot AI & Gateway Models
    KIMI_K3 = "kimi:kimi-k3"
    KIMI_K2 = "kimi:kimi-k2"
    OMNIROUTE_AUTO = "omniroute:auto"

    @classmethod
    def default_worker(cls) -> str:
        """Effective worker model (env WORKER_MODEL wins over registry default)."""
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
