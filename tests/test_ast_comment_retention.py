import pytest
from orchestrator.ast_node_splicer import splice_ast_function


def test_comment_and_docstring_retention():
    source_code = '''# Header license comment
# Author: sdageltc

"""Module level docstring with instructions."""

GLOBAL_CONSTANT = 42 # inline comment

# Pre-function comment
@pytest.mark.skip(reason="not ready")
def calculate_metrics(a: int, b: int = 10) -> int:
    """Calculates metrics."""
    # Internal step comment
    result = a + b
    return result

# Post-function trailing comment
'''

    replacement_code = '''@pytest.mark.skip(reason="not ready")
def calculate_metrics(a: int, b: int = 10) -> int:
    """Calculates metrics with optimization."""
    # Optimized internal step comment
    result = (a * 2) + (b - a)
    return result
'''

    spliced = splice_ast_function(source_code, "calculate_metrics", replacement_code, enforce_strict_signature=True)

    # Assert header comments remain
    assert "# Header license comment" in spliced
    assert "# Author: sdageltc" in spliced
    assert '"""Module level docstring with instructions."""' in spliced
    assert "GLOBAL_CONSTANT = 42 # inline comment" in spliced
    assert "# Pre-function comment" in spliced
    assert "# Post-function trailing comment" in spliced
    assert "result = (a * 2) + (b - a)" in spliced
    assert '@pytest.mark.skip(reason="not ready")' in spliced


def test_signature_drift_rejection():
    source_code = """
def target_func(x: int, y: str = "default") -> bool:
    return True
"""
    # 1. Changing type annotation
    bad_replacement = """
def target_func(x: int, y: int = 1) -> bool:
    return False
"""
    with pytest.raises(ValueError, match="Signature drift detected"):
        splice_ast_function(source_code, "target_func", bad_replacement, enforce_strict_signature=True)

    # 2. Changing parameter name
    bad_param_replacement = """
def target_func(x: int, z: str = "default") -> bool:
    return False
"""
    with pytest.raises(ValueError, match="Signature drift detected"):
        splice_ast_function(source_code, "target_func", bad_param_replacement, enforce_strict_signature=True)
