"""
orchestrator/ast_splicer.py
AST Signature, Decorator, and Invariant Validator (Pure In-Memory Checker).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class InvariantValidationResult:
    valid: bool
    violations: List[str]


class ASTInvariantValidator:
    """Validates structural invariants between original and candidate AST nodes."""

    @classmethod
    def validate(cls, original_code: str, modified_code: str, target_symbol: str) -> InvariantValidationResult:
        violations = []

        try:
            orig_tree = ast.parse(original_code)
        except SyntaxError as e:
            return InvariantValidationResult(valid=False, violations=[f"Original code syntax error: {e}"])

        try:
            mod_tree = ast.parse(modified_code)
        except SyntaxError as e:
            return InvariantValidationResult(valid=False, violations=[f"Modified code syntax error: {e}"])

        orig_node = cls._find_func(orig_tree, target_symbol)
        mod_node = cls._find_func(mod_tree, target_symbol)

        if not orig_node:
            return InvariantValidationResult(
                valid=False, violations=[f"Target '{target_symbol}' not found in original."]
            )
        if not mod_node:
            return InvariantValidationResult(
                valid=False, violations=[f"Target '{target_symbol}' deleted or missing in patch."]
            )

        # 1. Coroutine Type Invariance
        if isinstance(orig_node, ast.AsyncFunctionDef) != isinstance(mod_node, ast.AsyncFunctionDef):
            violations.append(f"Coroutine mismatch: {target_symbol} changed between async/sync.")

        # 2. Parameter Signature Invariance (Positional, Keyword-Only, VarArgs)
        orig_args = orig_node.args
        mod_args = mod_node.args

        orig_pos = [a.arg for a in getattr(orig_args, "posonlyargs", [])] + [a.arg for a in orig_args.args]
        mod_pos = [a.arg for a in getattr(mod_args, "posonlyargs", [])] + [a.arg for a in mod_args.args]
        if orig_pos != mod_pos:
            violations.append(f"Positional parameters altered: expected {orig_pos}, got {mod_pos}.")

        orig_kwonly = [a.arg for a in orig_args.kwonlyargs]
        mod_kwonly = [a.arg for a in mod_args.kwonlyargs]
        if orig_kwonly != mod_kwonly:
            violations.append(f"Keyword-only parameters altered: expected {orig_kwonly}, got {mod_kwonly}.")

        if bool(orig_args.vararg) != bool(mod_args.vararg):
            violations.append(f"*args signature altered on {target_symbol}.")
        if bool(orig_args.kwarg) != bool(mod_args.kwarg):
            violations.append(f"**kwargs signature altered on {target_symbol}.")

        # 3. Decorator Stack Invariance
        orig_decs = [cls._get_decorator_name(d) for d in orig_node.decorator_list]
        mod_decs = [cls._get_decorator_name(d) for d in mod_node.decorator_list]
        if orig_decs != mod_decs:
            violations.append(f"Decorator stack stripped or altered: expected {orig_decs}, got {mod_decs}.")

        return InvariantValidationResult(valid=len(violations) == 0, violations=violations)

    @classmethod
    def _find_func(cls, tree: ast.AST, name: str) -> Optional[ast.AST]:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        return None

    @classmethod
    def _get_decorator_name(cls, dec: ast.AST) -> str:
        if isinstance(dec, ast.Name):
            return dec.id
        if isinstance(dec, ast.Attribute):
            return dec.attr
        if isinstance(dec, ast.Call):
            return cls._get_decorator_name(dec.func)
        return ast.dump(dec)
