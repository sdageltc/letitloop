"""
Unit tests for AST mutant injector and mutation testing engine.
Verifies mutant injection behaviors, operator transformations, and syntax preservation.
"""

from __future__ import annotations

import ast
import unittest

from orchestrator.experimental.mutation_tester import (
    ASTMutantInjector,
    MutationOperator,
    OperatorType,
    apply_mutations,
    generate_mutants,
    inject_mutants,
    mutate_ast,
)

# ============================================================================
# 1. Required Test Section: test_binary_operator_mutation
# ============================================================================


def test_binary_operator_mutation():
    """Verify binary operator mutation behaviors and operator replacements (AOR)."""
    source = "def compute(a, b):\n    return a + b\n"
    mutants = generate_mutants(source)

    # Verify mutants generated for '+'
    binop_mutants = [m for m in mutants if m.location.operator_type == OperatorType.AOR]
    assert len(binop_mutants) >= 2

    replacements = {m.location.replacement_op for m in binop_mutants}
    assert "Sub" in replacements
    assert "Mult" in replacements

    for mutant in binop_mutants:
        assert mutant.location.node_type == "BinOp"
        assert mutant.location.original_op == "Add"
        assert mutant.location.lineno == 2
        assert "Replace Add with" in mutant.location.description
        assert "def compute(a, b):" in mutant.mutated_code

    # Test subtraction mutation
    sub_source = "def sub(x, y):\n    return x - y\n"
    sub_mutants = [m for m in generate_mutants(sub_source) if m.location.operator_type == OperatorType.AOR]
    sub_replacements = {m.location.replacement_op for m in sub_mutants}
    assert "Add" in sub_replacements
    assert "Mult" in sub_replacements

    # Test multiplication and division mutations
    mult_source = "def mul_div(x, y):\n    return x * y + x / y\n"
    mult_mutants = [m for m in generate_mutants(mult_source) if m.location.operator_type == OperatorType.AOR]
    assert len(mult_mutants) >= 4

    # Test bitwise operators
    bit_source = "def bitwise(a, b):\n    return a & b | (a ^ b)\n"
    bit_mutants = [m for m in generate_mutants(bit_source) if m.location.operator_type == OperatorType.AOR]
    assert len(bit_mutants) >= 6


# ============================================================================
# 2. Required Test Section: test_comparison_operator_mutation
# ============================================================================


def test_comparison_operator_mutation():
    """Verify comparison operator mutation behaviors and operator replacements (ROR)."""
    source = "def check(x):\n    return x > 0\n"
    mutants = generate_mutants(source)

    cmp_mutants = [m for m in mutants if m.location.operator_type == OperatorType.ROR]
    assert len(cmp_mutants) == 3

    replacements = {m.location.replacement_op for m in cmp_mutants}
    assert replacements == {"GtE", "Lt", "Eq"}

    for mutant in cmp_mutants:
        assert mutant.location.node_type == "Compare"
        assert mutant.location.original_op == "Gt"
        assert "Replace comparison Gt with" in mutant.location.description

    # Test equality and inequality comparisons
    eq_source = "def is_same(a, b):\n    return a == b and a != b\n"
    eq_mutants = [m for m in generate_mutants(eq_source) if m.location.operator_type == OperatorType.ROR]
    assert any(m.location.original_op == "Eq" for m in eq_mutants)
    assert any(m.location.original_op == "NotEq" for m in eq_mutants)

    # Test identity and containment comparisons
    id_source = "def check_is_in(item, container, other):\n    return item is other or item in container\n"
    id_mutants = [m for m in generate_mutants(id_source) if m.location.operator_type == OperatorType.ROR]
    id_origs = {m.location.original_op for m in id_mutants}
    assert "Is" in id_origs
    assert "In" in id_origs
    id_repls = {m.location.replacement_op for m in id_mutants}
    assert "IsNot" in id_repls
    assert "NotIn" in id_repls


# ============================================================================
# 3. Required Test Section: test_mutant_execution
# ============================================================================


def test_mutant_execution():
    """Verify mutant execution, evaluation differences, and syntax preservation."""
    source = "def calculate(a, b):\n    return a + b\n"
    mutants = generate_mutants(source)

    # Verify original code behavior
    orig_scope = {}
    exec(source, orig_scope)
    assert orig_scope["calculate"](5, 3) == 8

    # Verify mutated code execution and behavior changes
    observed_results = set()
    for mutant in mutants:
        compiled = compile(mutant.mutated_code, f"<mutant_{mutant.mutant_id}>", "exec")
        mutant_scope = {}
        exec(compiled, mutant_scope)
        res = mutant_scope["calculate"](5, 3)
        observed_results.add(res)

    # Original is 8, Sub is 2, Mult is 15
    assert 2 in observed_results
    assert 15 in observed_results

    # Test comparison mutant execution
    cmp_source = "def is_positive(x):\n    return x > 0\n"
    cmp_mutants = generate_mutants(cmp_source)
    cmp_results = set()
    for mutant in cmp_mutants:
        compiled = compile(mutant.mutated_code, "<test>", "exec")
        scope = {}
        exec(compiled, scope)
        cmp_results.add(scope["is_positive"](-5))

    # Original is False for -5, but mutated 'x < 0' or similar will yield True
    assert True in cmp_results


# ============================================================================
# 4. Additional Comprehensive Tests: Logical, Unary, and Constant Mutations
# ============================================================================


def test_boolean_operator_mutation():
    """Verify boolean operator replacement (LOR: and <-> or)."""
    source = "def logic(a, b):\n    return a and b\n"
    mutants = [m for m in generate_mutants(source) if m.location.operator_type == OperatorType.LOR]
    assert len(mutants) == 1
    assert mutants[0].location.original_op == "And"
    assert mutants[0].location.replacement_op == "Or"
    assert "or" in mutants[0].mutated_code

    or_source = "def logic(a, b):\n    return a or b\n"
    or_mutants = [m for m in generate_mutants(or_source) if m.location.operator_type == OperatorType.LOR]
    assert len(or_mutants) == 1
    assert or_mutants[0].location.original_op == "Or"
    assert or_mutants[0].location.replacement_op == "And"


def test_unary_operator_mutation():
    """Verify unary operator replacement (UOR: not, +, -, ~)."""
    source = "def negate(x):\n    return not x\n"
    mutants = [m for m in generate_mutants(source) if m.location.operator_type == OperatorType.UOR]
    assert len(mutants) == 1
    assert mutants[0].location.original_op == "Not"
    assert mutants[0].location.replacement_op == "UAdd"

    usub_source = "def minus(x):\n    return -x\n"
    usub_mutants = [m for m in generate_mutants(usub_source) if m.location.operator_type == OperatorType.UOR]
    assert len(usub_mutants) == 1
    assert usub_mutants[0].location.original_op == "USub"
    assert usub_mutants[0].location.replacement_op == "UAdd"


def test_constant_literal_mutation():
    """Verify constant / literal replacement (LCR: bool, int, float, str)."""
    source = "flag = True\ncount = 10\nname = 'antigravity'\n"
    mutants = [m for m in generate_mutants(source) if m.location.operator_type == OperatorType.LCR]
    assert len(mutants) > 0

    # Check bool mutation
    bool_mutants = [m for m in mutants if m.location.original_op == repr(True)]
    assert len(bool_mutants) == 1
    assert bool_mutants[0].location.replacement_op == repr(False)

    # Check integer mutation (10 -> 11, 9, 0)
    int_mutants = [m for m in mutants if m.location.original_op == repr(10)]
    assert len(int_mutants) == 3
    repls = {m.location.replacement_op for m in int_mutants}
    assert repls == {repr(11), repr(9), repr(0)}

    # Check string mutation
    str_mutants = [m for m in mutants if m.location.original_op == repr("antigravity")]
    assert len(str_mutants) == 1
    assert str_mutants[0].location.replacement_op == repr("")


# ============================================================================
# 5. Class Methods, API Interfaces, and AST Transformations
# ============================================================================


def test_mutation_operator_class_methods():
    """Verify MutationOperator helper methods for operator and constant replacements."""
    # BinOp replacements
    add_repls = MutationOperator.get_binop_replacements(ast.Add())
    assert any(isinstance(r, ast.Sub) for r in add_repls)
    assert any(isinstance(r, ast.Mult) for r in add_repls)

    # Compare replacements
    cmp_repls = MutationOperator.get_cmpop_replacements(ast.Eq())
    assert any(isinstance(r, ast.NotEq) for r in cmp_repls)

    # BoolOp replacements
    bool_repls = MutationOperator.get_boolop_replacements(ast.And())
    assert any(isinstance(r, ast.Or) for r in bool_repls)

    # UnaryOp replacements
    unary_repls = MutationOperator.get_unaryop_replacements(ast.USub())
    assert any(isinstance(r, ast.UAdd) for r in unary_repls)

    # Constant replacements
    assert MutationOperator.get_constant_replacements(True) == [False]
    assert MutationOperator.get_constant_replacements(0) == [1, -1]
    assert MutationOperator.get_constant_replacements(1) == [0, 2]
    assert MutationOperator.get_constant_replacements(5) == [6, 4, 0]
    assert MutationOperator.get_constant_replacements("text") == [""]
    assert MutationOperator.get_constant_replacements("") == ["mutated"]
    assert MutationOperator.get_constant_replacements([]) == []


def test_mutate_ast_and_custom_locations():
    """Verify mutate_ast and apply_mutations with custom location filtering."""
    source = "def foo(x, y):\n    return x + y\n"
    tree = ast.parse(source)

    injector = ASTMutantInjector()
    locations = injector.collect_mutation_points(tree)
    assert len(locations) > 0

    # Pick single location
    target_loc = locations[0]
    mutated_tree = mutate_ast(tree, target_loc)
    assert isinstance(mutated_tree, ast.AST)

    # Apply only to subset of locations
    filtered_mutants = apply_mutations(source, locations=[target_loc])
    assert len(filtered_mutants) == 1
    assert filtered_mutants[0].mutant_id == 1
    assert filtered_mutants[0].location == target_loc

    # Test inject_mutants wrapper
    injected = inject_mutants(source)
    assert len(injected) == len(locations)


def test_syntax_preservation_and_compilation():
    """Verify that generated mutants preserve syntax and can be parsed/compiled."""
    complex_source = "def complex_fn(a, b, c=10):\n    if a > 0 and b <= 20:\n        val = (a + b) * c\n        return not (val == 0)\n    return False\n"
    mutants = generate_mutants(complex_source)
    assert len(mutants) > 0

    for mutant in mutants:
        parsed = ast.parse(mutant.mutated_code)
        assert parsed is not None
        code_obj = compile(mutant.mutated_code, f"<test_mutant_{mutant.mutant_id}>", "exec")
        assert code_obj is not None


def test_empty_and_edge_case_code():
    """Verify handling of empty strings, comments, and non-mutatable source code."""
    assert generate_mutants("") == []
    assert generate_mutants("# Just a comment\n") == []
    assert generate_mutants("pass\n") == []


class TestASTMutantInjectorUnitTest(unittest.TestCase):
    """Unittest TestCase wrapper ensuring compatibility with unittest test runner."""

    def test_binary_operator_mutation_unittest(self):
        test_binary_operator_mutation()

    def test_comparison_operator_mutation_unittest(self):
        test_comparison_operator_mutation()

    def test_mutant_execution_unittest(self):
        test_mutant_execution()

    def test_boolean_operator_mutation_unittest(self):
        test_boolean_operator_mutation()

    def test_unary_operator_mutation_unittest(self):
        test_unary_operator_mutation()

    def test_constant_literal_mutation_unittest(self):
        test_constant_literal_mutation()


if __name__ == "__main__":
    unittest.main()
