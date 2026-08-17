"""Robust LLM output parser — multi-tier artifact extraction from model responses."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import List

RE_XML_FILE = re.compile(
    r'<file\s+(?:path|name)=["\']([^"\']+)["\']\s*>([\s\S]*?)(?:</file>|$)',
    re.IGNORECASE,
)
RE_HEADER_FENCE = re.compile(
    r"(?:^|\n)([\w\/\.\-\_\\]+\.[a-zA-Z0-9]+)\s*\n```(?:([a-zA-Z0-9_\-\+]+)\s+)?\n([\s\S]*?)(?:```|$)",
)
RE_ANNOTATED_FENCE = re.compile(
    r'```([a-zA-Z0-9_\-\+]+)?:(?:\s*path=)?["\']?([\w\/\.\-\_\\]+\.[a-zA-Z0-9]+)["\']?\s*\n([\s\S]*?)(?:```|$)',
)
RE_GENERIC_FENCE = re.compile(
    r"```([a-zA-Z0-9_\-\+]+)?\s*\n([\s\S]*?)(?:```|$)",
)
RE_FENCE_MARKER = re.compile(r"^```", re.MULTILINE)


@dataclass
class ParsedArtifact:
    path: str
    content: str
    language: str
    parser_tier: str


@dataclass
class ParseResult:
    ok: bool
    artifacts: List[ParsedArtifact] = field(default_factory=list)
    error: str = ""
    raw_length: int = 0


def sanitize_text(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def repair_unclosed_fences(text: str) -> str:
    matches = list(RE_FENCE_MARKER.finditer(text))
    if len(matches) % 2 != 0:
        text = text.rstrip() + "\n```"
    return text


def _strip_llm_chatter(text: str) -> str:
    pat = re.compile(
        r"(?i)^\s*(sure!|here is the|i\'?m (sorry|unable)|as an ai|i hope|let me know|the output has been)",
        re.MULTILINE,
    )
    return pat.sub("", text).strip()


def _detect_language(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".go": "go",
        ".rs": "rust",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
        ".sh": "shell",
        ".ps1": "powershell",
        ".bat": "batch",
    }
    return mapping.get(ext, "text")


def _is_path_acceptable(
    proposed: str,
    expected_paths: List[str],
) -> bool:
    norm = proposed.replace("\\", "/")
    if norm.startswith("/"):
        return False
    if norm[1:3] == ":\\" if len(norm) > 2 else False:
        return False
    if ".." in norm.split("/") or ".." in norm.split("\\"):
        return False
    if proposed in expected_paths:
        return True
    for ep in expected_paths:
        if ep.replace("\\", "/") == norm:
            return True
    return False


def parse_llm_artifacts(
    raw_output: str,
    expected_paths: List[str],
) -> ParseResult:
    raw_length = len(raw_output)
    if not raw_output or not raw_output.strip():
        return ParseResult(ok=False, error="empty model output", raw_length=raw_length)

    text = sanitize_text(raw_output)
    text = text.strip()

    artifacts: List[ParsedArtifact] = []
    used_tiers: List[str] = []

    def _accept(artifact: ParsedArtifact) -> bool:
        if not _is_path_acceptable(artifact.path, expected_paths):
            return False
        artifacts.append(artifact)
        used_tiers.append(artifact.parser_tier)
        return True

    _rejected = False

    # Tier 1: JSON array
    try:
        if text.startswith("[") and text.endswith("]"):
            data = json.loads(text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "path" in item and "content" in item:
                        p = item["path"]
                        c = item["content"]
                        lang = item.get("language", _detect_language(p))
                        if not _accept(ParsedArtifact(path=p, content=c, language=lang, parser_tier="T1_JSON")):
                            _rejected = True
                if artifacts:
                    return ParseResult(ok=True, artifacts=artifacts, raw_length=raw_length)
                if _rejected:
                    return ParseResult(ok=False, error="JSON artifact path not allowed", raw_length=raw_length)
    except (json.JSONDecodeError, ValueError):
        pass
    artifacts.clear()

    # Tier 2: XML file tags
    xml_matches = RE_XML_FILE.findall(text)
    _rejected = False
    if xml_matches:
        for path, content in xml_matches:
            path = path.strip()
            content = content.lstrip("\n")
            lang = _detect_language(path)
            if not _accept(ParsedArtifact(path=path, content=content, language=lang, parser_tier="T2_XML")):
                _rejected = True
        if artifacts:
            return ParseResult(ok=True, artifacts=artifacts, raw_length=raw_length)
        if _rejected:
            return ParseResult(ok=False, error="XML artifact path not allowed", raw_length=raw_length)
    artifacts.clear()

    # Tier 3: Header + code fence (GPT-Engineer style)
    header_matches = RE_HEADER_FENCE.findall(text)
    _rejected = False
    if header_matches:
        for path, lang_tag, content in header_matches:
            path = path.strip()
            lang = lang_tag or _detect_language(path)
            if not _accept(ParsedArtifact(path=path, content=content, language=lang, parser_tier="T3_HeaderFence")):
                _rejected = True
        if artifacts:
            return ParseResult(ok=True, artifacts=artifacts, raw_length=raw_length)
        if _rejected:
            return ParseResult(ok=False, error="Header-fence artifact path not allowed", raw_length=raw_length)
    artifacts.clear()

    # Tier 4: Annotated fence (path embedded in language tag)
    annotated = RE_ANNOTATED_FENCE.findall(text)
    _rejected = False
    if annotated:
        for lang_tag, path, content in annotated:
            path = path.strip()
            lang = lang_tag or _detect_language(path)
            if not _accept(ParsedArtifact(path=path, content=content, language=lang, parser_tier="T4_AnnotatedFence")):
                _rejected = True
        if artifacts:
            return ParseResult(ok=True, artifacts=artifacts, raw_length=raw_length)
        if _rejected:
            return ParseResult(ok=False, error="Annotated fence path not allowed", raw_length=raw_length)
    artifacts.clear()

    # Tier 5: Generic code fence (no path)
    generic = RE_GENERIC_FENCE.findall(text)
    if generic:
        if len(generic) == 1 and len(expected_paths) == 1:
            lang_tag, content = generic[0]
            path = expected_paths[0]
            lang = lang_tag or _detect_language(path)
            _accept(ParsedArtifact(path=path, content=content, language=lang, parser_tier="T5_GenericFence"))
            return ParseResult(ok=True, artifacts=artifacts, raw_length=raw_length)
        elif len(generic) == len(expected_paths):
            for idx, (lang_tag, content) in enumerate(generic):
                path = expected_paths[idx]
                lang = lang_tag or _detect_language(path)
                _accept(ParsedArtifact(path=path, content=content, language=lang, parser_tier="T5_GenericFence"))
            return ParseResult(ok=True, artifacts=artifacts, raw_length=raw_length)
        return ParseResult(ok=False, error="generic fence count mismatch with expected paths", raw_length=raw_length)
    artifacts.clear()

    # Tier 6: Whole-body fallback (single expected output only)
    if len(expected_paths) == 1:
        cleaned = _strip_llm_chatter(text)
        if not cleaned:
            cleaned = text
        _accept(
            ParsedArtifact(
                path=expected_paths[0],
                content=cleaned,
                language=_detect_language(expected_paths[0]),
                parser_tier="T6_WholeBodyFallback",
            )
        )
        return ParseResult(ok=True, artifacts=artifacts, raw_length=raw_length)

    return ParseResult(ok=False, error="no parseable artifacts found in model output", raw_length=raw_length)
