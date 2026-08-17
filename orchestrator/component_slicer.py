"""Deterministic component slicing for the quality plane.

Splits output files into up to `max_components` groups purely by
directory structure. Zero LLM calls. Zero side effects.

A ComponentSlice groups files that share a directory root, so each
reviewer sees a coherent subset of the output.
"""

from __future__ import annotations

import os
from typing import List, Optional


class ComponentSlice:
    """A group of output files reviewed together by one reviewer.

    Attributes:
        id: Unique string identifier (component_0, component_1, ...).
        files: File paths assigned to this component.
        description: Human-readable summary of what this component covers.
        total_lines: Sum of file sizes in bytes (simple stat, not git-aware).
    """

    def __init__(
        self,
        id: str,
        files: List[str],
        description: str = "",
        total_lines: int = 0,
    ):
        self.id = id
        self.files = sorted(files)
        self.description = description
        self.total_lines = total_lines

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "files": list(self.files),
            "description": self.description,
            "total_lines": self.total_lines,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ComponentSlice:
        return cls(
            id=d["id"],
            files=d.get("files", []),
            description=d.get("description", ""),
            total_lines=d.get("total_lines", 0),
        )


def _group_key(path: str) -> str:
    """Return the grouping key for a file path.

    - Files inside a directory: use the top-level directory name.
    - Files in the working root: use the file extension (or "__root__" if no ext).
    - Absolute paths: the drive letter / leading separator is stripped first,
      so absolute and relative paths group identically.
    """
    normalised = path.replace("\\", "/")
    parts = [p for p in normalised.split("/") if p]
    if parts and len(parts[0]) == 2 and parts[0][1] == ":":
        parts = parts[1:]
    if len(parts) >= 2:
        return parts[0]
    _, ext = os.path.splitext(parts[0]) if parts else ("", "")
    return ext if ext else "__root__"


def slice_components(
    output_paths: List[str],
    max_components: int = 5,
    base_dir: Optional[str] = None,
) -> List[ComponentSlice]:
    """Split output files into at most *max_components* groups.

    The algorithm groups files by their top-level directory, then merges
    smallest groups when there are too many. Deterministic for the same
    input list.

    When *base_dir* is provided (e.g. the workspace root), paths are made
    relative to it before grouping so absolute paths group by their
    workspace-relative directory rather than the drive letter.
    """
    if not output_paths:
        return [ComponentSlice(id="component_0", files=[], description="no files")]

    # 1. Group by directory/extension key
    groups: dict = {}
    for p in output_paths:
        if base_dir:
            try:
                rel = os.path.relpath(p, base_dir)
            except ValueError:
                # Cross-drive (Windows C: vs D:) — fall back to the raw path;
                # _group_key still strips the drive letter.
                rel = p
        else:
            rel = p
        key = _group_key(rel)
        groups.setdefault(key, []).append(p)

    # 2. Convert to list of (key, files, total_lines) sorted by file count desc
    sorted_groups = sorted(
        [(key, files, _estimate_lines(files)) for key, files in groups.items()],
        key=lambda x: (len(x[1]), x[0]),
        reverse=True,
    )

    # 3. Merge smallest groups until within limit
    while len(sorted_groups) > max(max_components, 1):
        merged_key = sorted_groups[-1][0]
        merged_files = list(sorted_groups[-1][1])
        merged_lines = sorted_groups[-1][2]
        del sorted_groups[-1]

        target_key, target_files, target_lines = sorted_groups[-1]
        sorted_groups[-1] = (
            f"{target_key}+{merged_key}",
            target_files + merged_files,
            target_lines + merged_lines,
        )

    # 4. Sort final groups by file count desc, then key for stability
    sorted_groups.sort(key=lambda x: (len(x[1]), x[0]), reverse=True)

    # 5. Build ComponentSlice objects
    components: List[ComponentSlice] = []
    for i, (key, files, lines) in enumerate(sorted_groups):
        desc = _describe_component(key, files)
        components.append(
            ComponentSlice(
                id=f"component_{i}",
                files=files,
                description=desc,
                total_lines=lines,
            )
        )

    return components


def _estimate_lines(file_paths: List[str]) -> int:
    """Estimate total lines from file paths (byte count via stat).

    If a file doesn't exist or stat fails, it contributes 0 lines.
    This is acceptable because the slicer is purely advisory for
    grouping — line counts are a heuristic, not an audit trail.
    """
    total = 0
    for p in file_paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


def _describe_component(key: str, files: List[str]) -> str:
    """Build a short human-readable description for a component."""
    exts: set = set()
    for f in files:
        _, ext = os.path.splitext(f)
        if ext:
            exts.add(ext.lstrip("."))
    ext_str = ", ".join(sorted(exts)) if exts else "no extension"
    return f"{len(files)} file(s) in {key!r} ({ext_str})"


def merge_component_results(
    components: List[ComponentSlice],
) -> List[str]:
    """Flatten merged component keys back into a single file list.

    Useful when a component was created by merging multiple directory
    groups — returns the original directory keys.
    """
    all_files: List[str] = []
    for c in components:
        all_files.extend(c.files)
    return sorted(set(all_files))
