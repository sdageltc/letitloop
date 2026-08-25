"""
orchestrator/surgical_extractor.py
AST-driven Neighborhood Context Extractor.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class NeighborhoodContext:
    target_node_id: str
    target_name: str
    target_type: str  # 'function' | 'async_function' | 'method'
    enclosing_class: Optional[str]
    start_line: int
    end_line: int
    imports: List[str]
    constants: Dict[str, str]
    class_attributes: List[str]
    target_source: str
    file_path: str
    full_module_loc: int


class SurgicalContextExtractor:
    """Extracts bounded AST neighborhoods to prevent context starvation."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def extract(self, file_path: str, target_symbol: str) -> NeighborhoodContext:
        full_path = (self.workspace_root / file_path).resolve()
        source_code = full_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source_code, filename=str(full_path))
        lines = source_code.splitlines(keepends=True)

        imports = self._extract_imports(tree)
        constants = self._extract_module_constants(tree, source_code)

        target_node, enclosing_class = self._find_target(tree, target_symbol)
        if not target_node:
            raise ValueError(f"Symbol '{target_symbol}' not found in {file_path}")

        class_attrs = []
        if enclosing_class:
            class_attrs = self._extract_class_context(enclosing_class, lines)

        start_line = target_node.lineno
        end_line = target_node.end_lineno
        target_src = "".join(lines[start_line - 1 : end_line])

        return NeighborhoodContext(
            target_node_id=f"{file_path}::{target_symbol}",
            target_name=target_symbol,
            target_type="method"
            if enclosing_class
            else ("async_function" if isinstance(target_node, ast.AsyncFunctionDef) else "function"),
            enclosing_class=enclosing_class.name if enclosing_class else None,
            start_line=start_line,
            end_line=end_line,
            imports=imports,
            constants=constants,
            class_attributes=class_attrs,
            target_source=target_src,
            file_path=file_path,
            full_module_loc=len(lines),
        )

    def _extract_imports(self, tree: ast.AST) -> List[str]:
        imports = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = ", ".join(a.name for a in node.names)
                imports.append(f"from {module} import {names}")
        return imports

    def _extract_module_constants(self, tree: ast.AST, source: str) -> Dict[str, str]:
        constants = {}
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and (target.id.isupper() or target.id.startswith("_")):
                        try:
                            val_src = ast.get_source_segment(source, node.value)
                            if val_src and len(val_src) < 200:
                                constants[target.id] = val_src
                        except Exception:
                            pass
        return constants

    def _find_target(self, tree: ast.AST, symbol_name: str) -> Tuple[Optional[ast.AST], Optional[ast.ClassDef]]:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol_name:
                return node, None
            if isinstance(node, ast.ClassDef):
                for sub in ast.iter_child_nodes(node):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == symbol_name:
                        return sub, node
        return None, None

    def _extract_class_context(self, class_node: ast.ClassDef, lines: List[str]) -> List[str]:
        attrs = []
        for sub in class_node.body:
            if isinstance(sub, (ast.AnnAssign, ast.Assign)):
                s = "".join(lines[sub.lineno - 1 : sub.end_lineno]).strip()
                attrs.append(s)
            elif isinstance(sub, ast.FunctionDef) and sub.name == "__init__":
                s = "".join(lines[sub.lineno - 1 : sub.end_lineno]).strip()
                attrs.append(s)
        return attrs
