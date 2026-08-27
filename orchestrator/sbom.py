"""Lightweight CycloneDX / SPDX SBOM diff generator (Sprint 5).

# EXPERIMENTAL: Enterprise compliance features — not for production use without [compliance] extra

Zero heavy deps: stdlib only (dataclasses, hashlib, json, pathlib).
Generates a minimal CycloneDX-like SBOM for a workspace and diffs it.

Bindings for ProofReceipt: base SBOM hash + patched SBOM hash + diff.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
from typing import Any, Dict, List


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _guess_component_type(path: pathlib.Path) -> str:
    if path.suffix == ".py":
        return "library"
    if path.name in ("package.json", "package-lock.json"):
        return "application"
    if path.name == "pyproject.toml":
        return "application"
    if path.name == "requirements.txt":
        return "application"
    return "file"


@dataclasses.dataclass
class SbomComponent:
    name: str
    version: str
    type: str
    path: str
    sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def generate_sbom(workspace: str | pathlib.Path = ".", include_patterns: List[str] | None = None) -> Dict[str, Any]:
    """Generate a minimal CycloneDX-like SBOM for workspace.

    Scans pyproject.toml, requirements.txt, package.json, and *.py files (hashes).
    """
    root = pathlib.Path(workspace).resolve()
    components: List[SbomComponent] = []

    # Key manifest files
    for rel in ["pyproject.toml", "requirements.txt", "package.json", "package-lock.json"]:
        p = root / rel
        if p.is_file():
            components.append(
                SbomComponent(
                    name=rel,
                    version="1.0",
                    type=_guess_component_type(p),
                    path=rel,
                    sha256=_sha256_file(p),
                )
            )

    # Python source files (limited to orchestrator + letitloop for speed)
    for sub in ["orchestrator", "letitloop"]:
        sub_path = root / sub
        if not sub_path.is_dir():
            continue
        for py in sub_path.rglob("*.py"):
            # Skip caches
            if "__pycache__" in py.parts or ".pyc" in py.suffix:
                continue
            rel = str(py.relative_to(root))
            # Filter by patterns if provided
            if include_patterns:
                if not any(pat in rel for pat in include_patterns):
                    continue
            try:
                components.append(
                    SbomComponent(
                        name=py.name,
                        version="0.0",
                        type="library",
                        path=rel,
                        sha256=_sha256_file(py),
                    )
                )
            except OSError:
                continue

    # Sort for determinism
    components.sort(key=lambda c: c.path)
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "version": 1,
        "components": [c.to_dict() for c in components],
    }
    return bom


def sbom_sha256(sbom: Dict[str, Any]) -> str:
    canonical = json.dumps(sbom, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_sbom(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Diff two SBOMs. Returns {added, removed, changed, old_sha256, new_sha256}."""
    old_map = {c["path"]: c for c in old.get("components", [])}
    new_map = {c["path"]: c for c in new.get("components", [])}

    added = [new_map[p] for p in new_map if p not in old_map]
    removed = [old_map[p] for p in old_map if p not in new_map]
    changed = []
    for p in set(old_map) & set(new_map):
        if old_map[p]["sha256"] != new_map[p]["sha256"]:
            changed.append({"path": p, "old_sha256": old_map[p]["sha256"], "new_sha256": new_map[p]["sha256"]})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "old_sha256": sbom_sha256(old),
        "new_sha256": sbom_sha256(new),
        "summary": f"+{len(added)} -{len(removed)} ~{len(changed)}",
    }
