"""Generic LLM transport layer.

Synchronous chat-completions over stdlib `urllib` — zero external dependencies.

Provider resolution is driven by the model-string prefix:

    openai:<model>     -> OPENAI_API_KEY      -> OpenAI  (or OPENAI_BASE_URL override)
    anthropic:<model>  -> ANTHROPIC_API_KEY   -> Anthropic Messages API
    gemini:<model>     -> GEMINI_API_KEY      -> Google Gemini (OpenAI-compatible endpoint)
    deepseek:<model>   -> DEEPSEEK_API_KEY    -> DeepSeek (OpenAI-compatible)
    any:<model>        -> LLM_API_KEY         -> LLM_BASE_URL (default https://api.openai.com/v1)
    <model>            -> treated as `any:` (LLM_API_KEY + LLM_BASE_URL)

`any:` is the escape hatch that makes the system work with *any* OpenAI-compatible
endpoint: Ollama, vLLM, LM Studio, Together, Groq, Azure OpenAI (via base URL),
self-hosted gateways, etc. A single key + base URL is enough.

Each provider may also be pointed elsewhere via `<PROVIDER>_BASE_URL`, e.g.
`OPENAI_BASE_URL=https://my-proxy.example/v1`.

The transport is deliberately small and deterministic:
- JSON-in/JSON-out chat completions only (no streaming, no tool calling here —
  determinism is preserved upstream by the orchestrator layers).
- Hard timeout per call (default 300s), raised as `LLMError` with structured info.
- No API key is ever written to disk or logs; keys are read from the environment
  at call time.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

# provider -> (env key, default base url, schema)
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "schema": "openai",
    },
    "anthropic": {
        "env_key": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com/v1",
        "schema": "anthropic",
    },
    "gemini": {
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "schema": "openai",
    },
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "schema": "openai",
    },
    "omniroute": {
        "env_key": "OMNIROUTE_API_KEY",
        "base_url": "http://localhost:8000/v1",
        "schema": "openai",
    },
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "schema": "openai",
    },
    "groq": {
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "schema": "openai",
    },
    "ollama": {
        "env_key": "OLLAMA_API_KEY",
        "base_url": "http://localhost:11434/v1",
        "schema": "openai",
    },
    "any": {
        "env_key": "LLM_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "schema": "openai",
    },
}

DEFAULT_TIMEOUT_S = 300


class LLMError(RuntimeError):
    """Raised for any transport-level failure (network, auth, rate limit, parse)."""

    def __init__(self, message: str, *, provider: str = "", status: Optional[int] = None):
        super().__init__(message)
        self.provider = provider
        self.status = status


def provider_of(model: str) -> str:
    """Return the provider key for a model string, defaulting to 'any'."""
    if not model:
        return "any"
    lowered = model.strip().lower()
    for prefix in PROVIDERS:
        if lowered.startswith(prefix + ":"):
            return prefix
    return "any"


def strip_provider(model: str) -> str:
    """Return the model id without its provider prefix."""
    p = provider_of(model)
    prefix = p + ":"
    return model[len(prefix) :] if model.startswith(prefix) else model


def base_url(provider: str) -> str:
    """Effective base URL for a provider (env override wins)."""
    conf = PROVIDERS[provider]
    return os.environ.get(f"{provider.upper()}_BASE_URL", conf["base_url"])


def api_key(provider: str) -> Optional[str]:
    """API key for a provider, or None when not configured."""
    conf = PROVIDERS[provider]
    return os.environ.get(conf["env_key"]) or None


def is_configured(provider: str) -> bool:
    """True when the provider's API key is present in the environment."""
    return api_key(provider) is not None


def configured_providers() -> list[str]:
    """All providers with a key present (used by tests/CI to decide live runs)."""
    return [p for p in PROVIDERS if is_configured(p)]


def default_model() -> str:
    """Worker default model, overridable via WORKER_MODEL."""
    return os.environ.get("WORKER_MODEL", "gemini:gemini-3.6-flash")


def qc_model() -> str:
    """QC default model, overridable via QC_MODEL."""
    return os.environ.get("QC_MODEL", "gemini:gemini-3.1-pro")


def planner_model() -> str:
    """Planner default model, overridable via PLANNER_MODEL."""
    return os.environ.get("PLANNER_MODEL", default_model())


def _http_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            from .qc_review import _redact_secrets

            detail = _redact_secrets(detail)
        except Exception:
            pass
        raise LLMError(
            f"HTTP {e.code} from provider: {detail or e.reason}",
            provider="unknown",
            status=e.code,
        ) from e
    except urllib.error.URLError as e:
        raise LLMError(f"connection failed: {e.reason}") from e
    except TimeoutError as e:
        raise LLMError(f"timed out after {timeout_s}s") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"non-JSON response: {raw[:200]!r}") from e


def call_llm(
    prompt: str,
    model: str,
    *,
    system: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    """Call a chat model and return ``{"text": str, "usage": dict, "model": str, "provider": str}``.

    Raises ``LLMError`` on any transport failure so callers can degrade deterministically.
    """
    provider = provider_of(model)
    key = api_key(provider)
    if not key:
        raise LLMError(
            f"provider '{provider}' is not configured: set {PROVIDERS[provider]['env_key']} "
            f"(or LLM_API_KEY/LLM_BASE_URL for any OpenAI-compatible endpoint)",
            provider=provider,
        )
    model_id = strip_provider(model)
    start = time.time()

    if PROVIDERS[provider]["schema"] == "anthropic":
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        messages = [{"role": "user", "content": prompt}]
        payload: Dict[str, Any] = {"model": model_id, "messages": messages, "max_tokens": max_tokens or 4096}
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        data = _http_json(f"{base_url(provider)}/messages", headers, payload, timeout_s)
        try:
            text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        except (AttributeError, TypeError):
            text = ""
        usage = data.get("usage", {})
    else:
        headers = {
            "Authorization": f"Bearer {key}",
            "content-type": "application/json",
        }
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens or 4096,
        }
        if system:
            payload["messages"].insert(0, {"role": "system", "content": system})
        if temperature is not None:
            payload["temperature"] = temperature
        data = _http_json(f"{base_url(provider)}/chat/completions", headers, payload, timeout_s)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"unexpected provider response shape: {str(data)[:300]!r}", provider=provider)
        usage = data.get("usage", {}) or {}

    if not isinstance(text, str):
        text = str(text)
    return {
        "text": text,
        "usage": usage,
        "model": model,
        "provider": provider,
        "elapsed_sec": time.time() - start,
    }
