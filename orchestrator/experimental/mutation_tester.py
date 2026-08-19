"""
Mutation Tester Module
Provides AST-based mutation operators and mutant injection logic for Python code.

Strategy:
1. Identify AST nodes suitable for mutation (binary operators, comparisons, boolean operators, unary operators, constants).
2. Use AST Visitor / NodeTransformer pattern (ASTMutantInjector) to traverse and selectively mutate targets.
3. Provide high-level generation and injection functions: generate_mutants, inject_mutants, apply_mutations, mutate_ast.
"""

import ast
import copy
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Type


class OperatorType(Enum):
    """Types of mutation operators."""

    AOR = auto()  # Arithmetic Operator Replacement
    ROR = auto()  # Relational / Comparison Operator Replacement
    LOR = auto()  # Logical / Boolean Operator Replacement
    UOR = auto()  # Unary Operator Replacement
    LCR = auto()  # Literal / Constant Replacement
    SDR = auto()  # Statement Deletion Replacement


@dataclass
class MutationLocation:
    """Location and metadata for a potential mutation."""

    lineno: int
    col_offset: int
    node_type: str
    operator_type: OperatorType
    original_op: str
    replacement_op: str
    description: str


@dataclass
class Mutant:
    """Represents an individual mutant created from source code."""

    mutant_id: int
    original_code: str
    mutated_code: str
    location: MutationLocation
    ast_tree: ast.AST


class MutationOperator:
    """
    Base class and registry for AST mutation operators.
    Defines transformation rules for AST nodes.
    """

    # Binary Arithmetic Operators
    BINOP_REPLACEMENTS: Dict[Type[ast.operator], List[Type[ast.operator]]] = {
        ast.Add: [ast.Sub, ast.Mult],
        ast.Sub: [ast.Add, ast.Mult],
        ast.Mult: [ast.Div, ast.Add],
        ast.Div: [ast.Mult, ast.FloorDiv],
        ast.FloorDiv: [ast.Div, ast.Mult],
        ast.Mod: [ast.Mult, ast.Div],
        ast.Pow: [ast.Mult, ast.Add],
        ast.BitAnd: [ast.BitOr, ast.BitXor],
        ast.BitOr: [ast.BitAnd, ast.BitXor],
        ast.BitXor: [ast.BitAnd, ast.BitOr],
        ast.LShift: [ast.RShift],
        ast.RShift: [ast.LShift],
    }

    # Comparison / Relational Operators
    CMPOP_REPLACEMENTS: Dict[Type[ast.cmpop], List[Type[ast.cmpop]]] = {
        ast.Eq: [ast.NotEq, ast.Lt, ast.Gt],
        ast.NotEq: [ast.Eq],
        ast.Lt: [ast.LtE, ast.Gt, ast.Eq],
        ast.LtE: [ast.Lt, ast.GtE, ast.Eq],
        ast.Gt: [ast.GtE, ast.Lt, ast.Eq],
        ast.GtE: [ast.Gt, ast.LtE, ast.Eq],
        ast.Is: [ast.IsNot],
        ast.IsNot: [ast.Is],
        ast.In: [ast.NotIn],
        ast.NotIn: [ast.In],
    }

    # Logical / Boolean Operators
    BOOLOP_REPLACEMENTS: Dict[Type[ast.boolop], List[Type[ast.boolop]]] = {
        ast.And: [ast.Or],
        ast.Or: [ast.And],
    }

    # Unary Operators
    UNARYOP_REPLACEMENTS: Dict[Type[ast.unaryop], List[Type[ast.unaryop]]] = {
        ast.Not: [ast.UAdd],
        ast.UAdd: [ast.USub],
        ast.USub: [ast.UAdd],
        ast.Invert: [ast.UAdd],
    }

    @classmethod
    def get_binop_replacements(cls, op: ast.operator) -> List[ast.operator]:
        """Return replacement operator instances for a given binary operator."""
        op_cls = type(op)
        return [repl_cls() for repl_cls in cls.BINOP_REPLACEMENTS.get(op_cls, [])]

    @classmethod
    def get_cmpop_replacements(cls, op: ast.cmpop) -> List[ast.cmpop]:
        """Return replacement operator instances for a given comparison operator."""
        op_cls = type(op)
        return [repl_cls() for repl_cls in cls.CMPOP_REPLACEMENTS.get(op_cls, [])]

    @classmethod
    def get_boolop_replacements(cls, op: ast.boolop) -> List[ast.boolop]:
        """Return replacement operator instances for a given boolean operator."""
        op_cls = type(op)
        return [repl_cls() for repl_cls in cls.BOOLOP_REPLACEMENTS.get(op_cls, [])]

    @classmethod
    def get_unaryop_replacements(cls, op: ast.unaryop) -> List[ast.unaryop]:
        """Return replacement operator instances for a given unary operator."""
        op_cls = type(op)
        return [repl_cls() for repl_cls in cls.UNARYOP_REPLACEMENTS.get(op_cls, [])]

    @classmethod
    def get_constant_replacements(cls, val: Any) -> List[Any]:
        """Return replacement values for constant/literal nodes."""
        if isinstance(val, bool):
            return [not val]
        elif isinstance(val, (int, float)):
            if val == 0:
                return [1, -1]
            elif val == 1:
                return [0, 2]
            else:
                return [val + 1, val - 1, 0]
        elif isinstance(val, str):
            if val == "":
                return ["mutated"]
            return [""]
        return []


class ASTMutantInjector(ast.NodeTransformer):
    """
    AST visitor and transformer that identifies mutation points and injects
    mutations into the AST.
    """

    def __init__(self, target_location: Optional[MutationLocation] = None, replacement: Any = None):
        super().__init__()
        self.target_location = target_location
        self.replacement = replacement
        self.found_locations: List[MutationLocation] = []
        self.current_mutation_applied = False

    def collect_mutation_points(self, tree: ast.AST) -> List[MutationLocation]:
        """Scan AST and collect all possible mutation points."""
        self.found_locations = []
        self.visit(tree)
        return list(self.found_locations)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        """Visit binary operations (e.g., +, -, *, /)."""
        op_cls = type(node.op)
        if op_cls in MutationOperator.BINOP_REPLACEMENTS:
            for repl_cls in MutationOperator.BINOP_REPLACEMENTS[op_cls]:
                loc = MutationLocation(
                    lineno=getattr(node, "lineno", 0),
                    col_offset=getattr(node, "col_offset", 0),
                    node_type="BinOp",
                    operator_type=OperatorType.AOR,
                    original_op=op_cls.__name__,
                    replacement_op=repl_cls.__name__,
                    description=f"Replace {op_cls.__name__} with {repl_cls.__name__}",
                )
                self.found_locations.append(loc)

                if self._is_target(loc):
                    new_node = copy.deepcopy(node)
                    new_node.op = repl_cls()
                    self.current_mutation_applied = True
                    return new_node

        self.generic_visit(node)
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        """Visit comparison operations (e.g., ==, !=, <, <=, >, >=)."""
        for idx, op in enumerate(node.ops):
            op_cls = type(op)
            if op_cls in MutationOperator.CMPOP_REPLACEMENTS:
                for repl_cls in MutationOperator.CMPOP_REPLACEMENTS[op_cls]:
                    loc = MutationLocation(
                        lineno=getattr(node, "lineno", 0),
                        col_offset=getattr(node, "col_offset", 0),
                        node_type="Compare",
                        operator_type=OperatorType.ROR,
                        original_op=op_cls.__name__,
                        replacement_op=repl_cls.__name__,
                        description=f"Replace comparison {op_cls.__name__} with {repl_cls.__name__}",
                    )
                    self.found_locations.append(loc)

                    if self._is_target(loc):
                        new_node = copy.deepcopy(node)
                        new_node.ops[idx] = repl_cls()
                        self.current_mutation_applied = True
                        return new_node

        self.generic_visit(node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        """Visit boolean operations (e.g., and, or)."""
        op_cls = type(node.op)
        if op_cls in MutationOperator.BOOLOP_REPLACEMENTS:
            for repl_cls in MutationOperator.BOOLOP_REPLACEMENTS[op_cls]:
                loc = MutationLocation(
                    lineno=getattr(node, "lineno", 0),
                    col_offset=getattr(node, "col_offset", 0),
                    node_type="BoolOp",
                    operator_type=OperatorType.LOR,
                    original_op=op_cls.__name__,
                    replacement_op=repl_cls.__name__,
                    description=f"Replace boolean op {op_cls.__name__} with {repl_cls.__name__}",
                )
                self.found_locations.append(loc)

                if self._is_target(loc):
                    new_node = copy.deepcopy(node)
                    new_node.op = repl_cls()
                    self.current_mutation_applied = True
                    return new_node

        self.generic_visit(node)
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        """Visit unary operations (e.g., not, -x, +x, ~x)."""
        op_cls = type(node.op)
        if op_cls in MutationOperator.UNARYOP_REPLACEMENTS:
            for repl_cls in MutationOperator.UNARYOP_REPLACEMENTS[op_cls]:
                loc = MutationLocation(
                    lineno=getattr(node, "lineno", 0),
                    col_offset=getattr(node, "col_offset", 0),
                    node_type="UnaryOp",
                    operator_type=OperatorType.UOR,
                    original_op=op_cls.__name__,
                    replacement_op=repl_cls.__name__,
                    description=f"Replace unary op {op_cls.__name__} with {repl_cls.__name__}",
                )
                self.found_locations.append(loc)

                if self._is_target(loc):
                    new_node = copy.deepcopy(node)
                    new_node.op = repl_cls()
                    self.current_mutation_applied = True
                    return new_node

        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        """Visit constant literal nodes."""
        replacements = MutationOperator.get_constant_replacements(node.value)
        for repl_val in replacements:
            loc = MutationLocation(
                lineno=getattr(node, "lineno", 0),
                col_offset=getattr(node, "col_offset", 0),
                node_type="Constant",
                operator_type=OperatorType.LCR,
                original_op=repr(node.value),
                replacement_op=repr(repl_val),
                description=f"Replace constant {repr(node.value)} with {repr(repl_val)}",
            )
            self.found_locations.append(loc)

            if self._is_target(loc):
                new_node = copy.deepcopy(node)
                new_node.value = repl_val
                self.current_mutation_applied = True
                return new_node

        self.generic_visit(node)
        return node

    def _is_target(self, location: MutationLocation) -> bool:
        """Check if the current location matches target location for mutation."""
        if not self.target_location:
            return False
        return (
            not self.current_mutation_applied
            and self.target_location.lineno == location.lineno
            and self.target_location.col_offset == location.col_offset
            and self.target_location.original_op == location.original_op
            and self.target_location.replacement_op == location.replacement_op
        )


def mutate_ast(tree: ast.AST, location: MutationLocation) -> ast.AST:
    """
    Apply a specific mutation to an AST at the specified location.

    Args:
        tree: The original AST.
        location: The mutation location specification.

    Returns:
        A new mutated AST.
    """
    copied_tree = copy.deepcopy(tree)
    injector = ASTMutantInjector(target_location=location)
    mutated_tree = injector.visit(copied_tree)
    ast.fix_missing_locations(mutated_tree)
    return mutated_tree


def apply_mutations(source_code: str, locations: Optional[List[MutationLocation]] = None) -> List[Mutant]:
    """
    Parse source code and apply mutations for given or all discovered mutation locations.

    Args:
        source_code: The Python source code as string.
        locations: Optional list of mutation locations. If None, all possible locations are used.

    Returns:
        A list of Mutant objects.
    """
    tree = ast.parse(source_code)
    if locations is None:
        collector = ASTMutantInjector()
        locations = collector.collect_mutation_points(tree)

    mutants: List[Mutant] = []
    for idx, loc in enumerate(locations, start=1):
        mutated_tree = mutate_ast(tree, loc)
        try:
            mutated_code = ast.unparse(mutated_tree)
        except AttributeError:
            import astor  # type: ignore

            mutated_code = astor.to_source(mutated_tree)

        mutants.append(
            Mutant(
                mutant_id=idx,
                original_code=source_code,
                mutated_code=mutated_code,
                location=loc,
                ast_tree=mutated_tree,
            )
        )
    return mutants


def inject_mutants(source_code: str) -> List[Mutant]:
    """
    Alias/wrapper to generate and inject all possible mutants for a source code string.

    Args:
        source_code: The Python source code to mutate.

    Returns:
        List of generated Mutant instances.
    """
    return apply_mutations(source_code)


def generate_mutants(source_code: str) -> List[Mutant]:
    """
    Generate all mutants from Python source code.

    Args:
        source_code: Python source code string.

    Returns:
        List of Mutant objects containing mutated code and ASTs.
    """
    return apply_mutations(source_code)
