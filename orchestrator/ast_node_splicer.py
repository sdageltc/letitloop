import ast
from typing import Any, Dict


def extract_comprehensive_signature(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> Dict[str, Any]:
    args = func_node.args
    pos_args = [a.arg for a in args.args]
    pos_types = [ast.unparse(a.annotation) if a.annotation else None for a in args.args]
    kwonly_args = [a.arg for a in args.kwonlyargs]
    kwonly_types = [ast.unparse(a.annotation) if a.annotation else None for a in args.kwonlyargs]
    decorators = [ast.unparse(d) for d in func_node.decorator_list]
    returns = ast.unparse(func_node.returns) if func_node.returns else None

    return {
        "pos_args": pos_args,
        "pos_types": pos_types,
        "kwonly_args": kwonly_args,
        "kwonly_types": kwonly_types,
        "num_defaults": len(args.defaults),
        "num_kw_defaults": len([d for d in args.kw_defaults if d is not None]),
        "has_vararg": bool(args.vararg),
        "has_kwarg": bool(args.kwarg),
        "decorators": decorators,
        "returns": returns,
        "is_async": isinstance(func_node, ast.AsyncFunctionDef),
    }


def _iter_functions_with_qualnames(tree: ast.AST):
    """Yield (qualname, node) for every function, tracking class nesting."""
    stack = [("", tree)]
    while stack:
        parent_q, node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{parent_q}.{child.name}" if parent_q else child.name
                yield q, child
                stack.append((q, child))
            elif isinstance(child, ast.ClassDef):
                q = f"{parent_q}.{child.name}" if parent_q else child.name
                yield q, child
                stack.append((q, child))
            else:
                stack.append((parent_q, child))


def splice_ast_function(
    source_code: str, target_name: str, replacement_code: str, enforce_strict_signature: bool = True
) -> str:
    source_tree = ast.parse(source_code)
    replacement_tree = ast.parse(replacement_code)

    all_functions = list(_iter_functions_with_qualnames(source_tree))
    if "." in target_name:
        matches = [(q, n) for q, n in all_functions if q == target_name]
        if not matches:
            raise ValueError(f"Target function '{target_name}' was not found in source AST.")
        bare_target = target_name.rsplit(".", 1)[-1]
    else:
        matches = [(q, n) for q, n in all_functions if q == target_name or q.endswith("." + target_name)]
        if len(matches) > 1:
            quals = sorted(q for q, _ in matches)
            raise ValueError(
                f"ambiguous target: {len(matches)} functions named '{target_name}' {quals}; "
                f"qualify the target (e.g. '{quals[0]}') or rename"
            )
        if not matches:
            raise ValueError(f"Target function '{target_name}' was not found in source AST.")
        bare_target = target_name

    # 1. Locate replacement node (by bare name in the replacement snippet)
    rep_node = None
    for node in ast.walk(replacement_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == bare_target:
            rep_node = node
            break

    if not rep_node:
        raise ValueError(f"Replacement code does not contain function definition for '{bare_target}'")

    # 2. Original node from the resolved match
    orig_node = matches[0][1]

    # 3. Validate signature invariance
    if enforce_strict_signature:
        orig_sig = extract_comprehensive_signature(orig_node)
        rep_sig = extract_comprehensive_signature(rep_node)
        if orig_sig != rep_sig:
            raise ValueError(f"Signature drift detected for '{target_name}': expected {orig_sig}, got {rep_sig}")

    # 4. Calculate source span including decorator stack
    if orig_node.decorator_list:
        start_lineno = min([d.lineno for d in orig_node.decorator_list])
    else:
        start_lineno = orig_node.lineno

    source_lines = source_code.splitlines(keepends=True)
    start_line = start_lineno - 1
    end_line = orig_node.end_lineno

    # Calculate original indentation prefix
    orig_start_line = source_lines[start_line]
    indent_len = len(orig_start_line) - len(orig_start_line.lstrip())
    indent_prefix = orig_start_line[:indent_len]

    # Format replacement code with matching base indentation
    rep_lines = replacement_code.strip().splitlines()
    if rep_lines:
        rep_base_indent = len(rep_lines[0]) - len(rep_lines[0].lstrip())
        formatted_lines = []
        for line in rep_lines:
            if line.strip():
                # If replacement line is less indented than base, align it
                stripped = line[rep_base_indent:] if line.startswith(" " * rep_base_indent) else line.lstrip()
                formatted_lines.append(indent_prefix + stripped + "\n")
            else:
                formatted_lines.append("\n")
        rep_formatted = "".join(formatted_lines)
    else:
        rep_formatted = "\n"

    before = source_lines[:start_line]
    after = source_lines[end_line:]

    return "".join(before) + rep_formatted + "".join(after)
