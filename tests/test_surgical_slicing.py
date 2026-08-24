"""
tests/test_surgical_slicing.py
Unit tests for Neighborhood Context Extractor and AST Invariant Validator.
"""

import tempfile
from pathlib import Path
import pytest
from orchestrator.surgical_extractor import SurgicalContextExtractor
from orchestrator.ast_splicer import ASTInvariantValidator


SAMPLE_MODULE = """
import os
import sys
from typing import List, Optional

MAX_RETRIES = 5
DEFAULT_TIMEOUT = 30.0

@dataclass
class EngineState:
    running: bool = False
    
    @property
    def is_active(self) -> bool:
        return self.running

def standalone_helper(a: int, b: int = 10, *args, **kwargs) -> int:
    \"\"\"Docstring helper.\"\"\"
    return a + b

async def async_fetch_data(url: str) -> str:
    return url
"""


def test_surgical_context_extractor():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.py"
        p.write_text(SAMPLE_MODULE, encoding="utf-8")

        extractor = SurgicalContextExtractor(workspace_root=Path(td))
        ctx = extractor.extract("sample.py", "standalone_helper")

        assert ctx.target_name == "standalone_helper"
        assert ctx.target_type == "function"
        assert ctx.enclosing_class is None
        assert "import os" in ctx.imports
        assert "MAX_RETRIES" in ctx.constants
        assert "def standalone_helper" in ctx.target_source


def test_surgical_context_extractor_method():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.py"
        p.write_text(SAMPLE_MODULE, encoding="utf-8")

        extractor = SurgicalContextExtractor(workspace_root=Path(td))
        ctx = extractor.extract("sample.py", "is_active")

        assert ctx.target_name == "is_active"
        assert ctx.target_type == "method"
        assert ctx.enclosing_class == "EngineState"
        assert any("running: bool" in attr for attr in ctx.class_attributes)


def test_ast_invariant_validator_success():
    original = SAMPLE_MODULE
    modified = SAMPLE_MODULE.replace("return a + b", "total = a + b\n    return total")
    res = ASTInvariantValidator.validate(original, modified, "standalone_helper")
    assert res.valid is True
    assert len(res.violations) == 0


def test_ast_invariant_validator_catches_param_change():
    original = SAMPLE_MODULE
    modified = SAMPLE_MODULE.replace("def standalone_helper(a: int, b: int = 10", "def standalone_helper(a: int, c: int = 10")
    res = ASTInvariantValidator.validate(original, modified, "standalone_helper")
    assert res.valid is False
    assert any("Positional parameters altered" in v for v in res.violations)


def test_ast_invariant_validator_catches_decorator_removal():
    original = SAMPLE_MODULE
    modified = SAMPLE_MODULE.replace("@property\n    def is_active", "def is_active")
    res = ASTInvariantValidator.validate(original, modified, "is_active")
    assert res.valid is False
    assert any("Decorator stack stripped" in v for v in res.violations)


def test_ast_invariant_validator_catches_async_mismatch():
    original = SAMPLE_MODULE
    modified = SAMPLE_MODULE.replace("async def async_fetch_data", "def async_fetch_data")
    res = ASTInvariantValidator.validate(original, modified, "async_fetch_data")
    assert res.valid is False
    assert any("Coroutine mismatch" in v for v in res.violations)
