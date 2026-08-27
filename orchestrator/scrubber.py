"""High-entropy string and regex secret scrubber (Sprint 6).

Scans prompts, AST diffs, and tool outputs for API keys and private tokens
before persisting to state.wal.jsonl. Masks secrets with <secret:REDACTED>.

Zero heavy deps: stdlib only (re, hashlib, math, pathlib).
"""

from __future__ import annotations

import math
import re

# Regex patterns for known secret formats
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # OpenAI
    (re.compile(r"sk-[A-Za-z0-9]{20,}[A-Za-z0-9]*"), "<secret:REDACTED:openai>"),
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"), "<secret:REDACTED:openai-proj>"),
    # GitHub
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"), "<secret:REDACTED:github>"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{82,}"), "<secret:REDACTED:github-pat>"),
    # AWS
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<secret:REDACTED:aws>"),
    (re.compile(r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{40}"), "<secret:REDACTED:aws-secret>"),
    # Private keys
    (re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----"), "<secret:REDACTED:private-key>"),
    # Generic Bearer / API keys in headers
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]+"), "<secret:REDACTED:bearer>"),
    # Slack
    (re.compile(r"xox[bpras]-[0-9A-Za-z\-]{10,48}"), "<secret:REDACTED:slack>"),
    # Stripe
    (re.compile(r"sk_(?:live|test)_[0-9a-zA-Z]{24,}"), "<secret:REDACTED:stripe>"),
]


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    entropy = 0.0
    length = len(s)
    for count in freq.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def is_high_entropy(s: str, threshold: float = 4.5, min_length: int = 20) -> bool:
    """Return True if s has high Shannon entropy and looks like a secret."""
    if len(s) < min_length:
        return False
    # Only consider strings that are mostly base64-like (alphanumeric + _-+/=)
    if not re.fullmatch(r"[A-Za-z0-9_\-+/=]+", s):
        return False
    return _shannon_entropy(s) >= threshold


_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9_\-+/=]{20,}")


def scrub_text(text: str) -> str:
    """Mask secrets in text with <secret:REDACTED> markers (deterministic)."""
    if not text:
        return text
    out = text
    # 1) Known patterns
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)

    # 2) High-entropy generic detection (conservative: only if not already redacted)
    def _replace_entropy(m: re.Match[str]) -> str:
        token = m.group(0)
        # Skip if already part of a redacted marker or common word
        if "<secret" in token or token.lower() in {"letitloop", "github", "workflow"}:
            return token
        if is_high_entropy(token):
            return "<secret:REDACTED:high-entropy>"
        return token

    out = _HIGH_ENTROPY_RE.sub(_replace_entropy, out)
    return out


def scrub_dict(data: dict) -> dict:
    """Recursively scrub all string values in a dict (for WAL events)."""
    out: dict = {}
    for k, v in data.items():
        if isinstance(v, str):
            out[k] = scrub_text(v)
        elif isinstance(v, dict):
            out[k] = scrub_dict(v)  # type: ignore
        elif isinstance(v, list):
            out[k] = [scrub_text(x) if isinstance(x, str) else scrub_dict(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out
