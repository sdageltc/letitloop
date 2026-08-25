"""Secret redaction firewall — single source of truth for credential masking.

Used before external transmission (QC reviewer prompts) and before journaling
(worker_output.log / evidence artifacts) so LLM- or CLI-emitted secrets never
land unredacted in the audit trail.
"""

from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"(github_pat_[A-Za-z0-9_]{22,})"),
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"(AIza[0-9A-Za-z-_]{35})"),
    re.compile(r"(hf_[A-Za-z0-9]{34,})"),
    re.compile(r"(xox[baprs]-[0-9A-Za-z-]{10,})"),
    re.compile(r"((?:AKIA|ASIA)[0-9A-Z]{16})"),
    re.compile(r"(gw_sk_[A-Za-z0-9]{16,})"),
    # AWS secret access key: 40-char base64-ish value paired with an explicit key name
    re.compile(
        r"(aws[_-]?secret[_-]?access[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{20,}['\"]?)",
        re.IGNORECASE,
    ),
    re.compile(r"(api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_-]{16,}['\"]?)", re.IGNORECASE),
    re.compile(r"(token\s*[=:]\s*['\"]?[A-Za-z0-9_.-]{16,}['\"]?)", re.IGNORECASE),
    re.compile(r"(password\s*[=:]\s*['\"]?[^\s'\"]{4,})", re.IGNORECASE),
    re.compile(r"(Bearer\s+[A-Za-z0-9_\-\.~+/]+=*)", re.IGNORECASE),
    re.compile(r"(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

_MASK = "[REDACTED]"


def _sub(m: "re.Match[str]") -> str:
    g = m.group(0)
    return g[:6] + _MASK if len(g) > 8 else _MASK


def redact(text: str) -> str:
    """Mask well-formed credential shapes. Conservative: only exact shapes."""
    if not text:
        return ""
    result = text
    for pat in _PATTERNS:
        result = pat.sub(_sub, result)
    return result
