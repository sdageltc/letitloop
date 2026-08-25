"""
orchestrator/patch_applier.py
Transactional Search/Replace Delta Patching Engine.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PatchChunk:
    search_block: str
    replace_block: str
    original_line_start: Optional[int] = None


@dataclass
class PatchResult:
    success: bool
    modified_content: str
    diff_preview: str
    chunks_applied: int
    error_message: Optional[str] = None


class SearchReplacePatchParser:
    """Parses and validates Aider-style Search/Replace blocks."""

    SEARCH_RE = re.compile(
        r"<{5,9}\s*SEARCH\s*\n(.*?)\n={5,9}\s*\n(.*?)\n>{5,9}\s*REPLACE",
        re.DOTALL,
    )

    @classmethod
    def parse_chunks(cls, raw_text: str) -> List[PatchChunk]:
        chunks = []
        for match in cls.SEARCH_RE.finditer(raw_text):
            search_content = match.group(1)
            replace_content = match.group(2)
            chunks.append(PatchChunk(search_block=search_content, replace_block=replace_content))
        return chunks


class PatchApplier:
    """Applies Search/Replace delta chunks with EOL normalization, uniqueness, and atomicity."""

    @staticmethod
    def normalize_eol(text: str) -> str:
        """Standardize all line endings to Unix LF."""
        return text.replace("\r\n", "\n").replace("\r", "\n")

    @classmethod
    def apply_patch(
        cls,
        original_content: str,
        patch_text: str,
        fuzzy_whitespace: bool = True,
    ) -> PatchResult:
        chunks = SearchReplacePatchParser.parse_chunks(patch_text)
        if not chunks:
            return PatchResult(
                success=False,
                modified_content=original_content,
                diff_preview="",
                chunks_applied=0,
                error_message="No valid <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE blocks found.",
            )

        norm_original = cls.normalize_eol(original_content)
        current_content = norm_original
        applied_count = 0

        # Transactional verification: verify all chunks match uniquely before mutating
        for i, chunk in enumerate(chunks, 1):
            norm_search = cls.normalize_eol(chunk.search_block)
            norm_replace = cls.normalize_eol(chunk.replace_block)

            # Check occurrence count
            count = current_content.count(norm_search)
            if count == 0 and fuzzy_whitespace:
                # Attempt fuzzy whitespace matching
                matched_target = cls._fuzzy_find(current_content, norm_search)
                if matched_target is None:
                    return PatchResult(
                        success=False,
                        modified_content=original_content,
                        diff_preview="",
                        chunks_applied=0,
                        error_message=f"Chunk {i} SEARCH block not found in target file.",
                    )
                norm_search = matched_target
                count = current_content.count(norm_search)

            if count > 1:
                return PatchResult(
                    success=False,
                    modified_content=original_content,
                    diff_preview="",
                    chunks_applied=0,
                    error_message=f"Ambiguous patch: Chunk {i} matches {count} locations. Expand SEARCH context.",
                )

            if count == 0:
                return PatchResult(
                    success=False,
                    modified_content=original_content,
                    diff_preview="",
                    chunks_applied=0,
                    error_message=f"Chunk {i} SEARCH block not found in target file.",
                )

            # Apply single unique replacement
            current_content = current_content.replace(norm_search, norm_replace, 1)
            applied_count += 1

        # Generate unified diff preview
        diff = "".join(
            difflib.unified_diff(
                norm_original.splitlines(keepends=True),
                current_content.splitlines(keepends=True),
                fromfile="original",
                tofile="modified",
                n=3,
            )
        )

        return PatchResult(
            success=True,
            modified_content=current_content,
            diff_preview=diff,
            chunks_applied=applied_count,
        )

    @classmethod
    def _fuzzy_find(cls, haystack: str, needle: str) -> Optional[str]:
        """Fuzzy match ignoring trailing whitespace and blank line discrepancies."""
        haystack_lines = haystack.splitlines()
        needle_lines = needle.splitlines()
        if not needle_lines:
            return None

        n_len = len(needle_lines)
        needle_stripped = [line.rstrip() for line in needle_lines]

        for i in range(len(haystack_lines) - n_len + 1):
            window = [haystack_lines[i + j].rstrip() for j in range(n_len)]
            if window == needle_stripped:
                # Return exact slice from haystack preserving original line endings
                return "\n".join(haystack_lines[i : i + n_len])
        return None
