import pytest
from orchestrator.ast_node_splicer import splice_ast_function


def test_splice_function_preserves_surrounding_comments_and_formatting():
    source = """# Global configuration constant
MAX_RETRIES = 3

# Important helper comment
def helper():
    return 42

def target_func(x: int, flag: bool = True) -> int:
    \"\"\"Docstring for target function.\"\"\"
    # Inline comment inside target
    return x + 1

# Trailing file comment
def another_helper():
    pass
"""
    new_func_code = """def target_func(x: int, flag: bool = True) -> int:
    \"\"\"Docstring for target function.\"\"\"
    # Updated inline logic
    return x * 10"""

    spliced = splice_ast_function(source, "target_func", new_func_code, enforce_strict_signature=True)

    assert "return x * 10" in spliced
    assert "# Global configuration constant" in spliced
    assert "# Important helper comment" in spliced
    assert "# Trailing file comment" in spliced


def test_splice_rejects_signature_drift_and_type_mutations():
    source = "def target_func(x: int, flag: bool = True) -> int:\n    return x\n"

    # 1. Parameter name change
    bad_name = "def target_func(y: int, flag: bool = True) -> int:\n    return y\n"
    with pytest.raises(ValueError, match="Signature drift detected"):
        splice_ast_function(source, "target_func", bad_name, enforce_strict_signature=True)

    # 2. Type annotation change
    bad_type = "def target_func(x: str, flag: bool = True) -> int:\n    return 0\n"
    with pytest.raises(ValueError, match="Signature drift detected"):
        splice_ast_function(source, "target_func", bad_type, enforce_strict_signature=True)

    # 3. Default value count change
    bad_default = "def target_func(x: int, flag: bool) -> int:\n    return x\n"
    with pytest.raises(ValueError, match="Signature drift detected"):
        splice_ast_function(source, "target_func", bad_default, enforce_strict_signature=True)


def test_splice_class_method_preserves_class_indentation():
    source = """class MyService:
    def __init__(self):
        self.count = 0

    def compute(self, a: int, b: int) -> int:
        # Original compute
        return a + b
"""
    new_method = """def compute(self, a: int, b: int) -> int:
    # Optimized compute
    return (a + b) * 2"""

    spliced = splice_ast_function(source, "compute", new_method, enforce_strict_signature=True)

    assert "    def compute(self, a: int, b: int) -> int:" in spliced
    assert "        return (a + b) * 2" in spliced
    assert "class MyService:" in spliced


def test_splice_nested_closure_disambiguation():
    """Verify that ambiguous duplicate names raise ValueError and qualname targets splice with precision."""
    import pytest

    source = """class Outer:
    def process_data(self, x: int) -> int:
        # Inner class helper
        return x + 1

def process_data(x: int) -> int:
    # Top-level main function
    return x * 2
"""
    new_method = """def process_data(self, x: int) -> int:
    # Inner class helper updated
    return x + 100
"""

    # Unqualified call with multiple functions raises ValueError
    with pytest.raises(ValueError, match="ambiguous"):
        splice_ast_function(source, "process_data", new_method, enforce_strict_signature=True)

    # Qualified call targets exact method
    spliced = splice_ast_function(source, "Outer.process_data", new_method, enforce_strict_signature=True)
    assert "# Inner class helper updated" in spliced
    assert "return x + 100" in spliced
    assert "# Top-level main function" in spliced
    assert "return x * 2" in spliced
