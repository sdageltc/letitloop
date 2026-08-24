"""Validation tests for the recipes/ cookbook.

Fast and fully offline: parses every recipes/**/*.json document, validates
goal structure, runs every embedded contract dict through the real
orchestrator.contract.validate_contract schema validator, and enforces that
each recipe directory documents itself with a README.md.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.contract import validate_contract  # noqa: E402 - path bootstrap above

pytestmark = pytest.mark.fast

RECIPES_DIR = REPO_ROOT / "recipes"


def _json_files():
    return sorted(RECIPES_DIR.rglob("*.json"))


def _walk_contract_dicts(node):
    """Yield every dict anywhere in the structure that looks like a contract."""
    if isinstance(node, dict):
        if "task_id" in node and "acceptance_checks" in node:
            yield node
        for value in node.values():
            yield from _walk_contract_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_contract_dicts(item)


def _goal_docs():
    """Yield (path, data) for top-level documents carrying goal_id."""
    for path in _json_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "goal_id" in data:
            yield path, data


def _rel(path):
    return str(Path(path).relative_to(RECIPES_DIR))


def test_recipes_tree_exists_with_content():
    assert RECIPES_DIR.is_dir(), "recipes/ directory must exist"
    assert any(RECIPES_DIR.iterdir()), "recipes/ must contain recipe directories"


def test_recipe_index_readme_exists():
    index = RECIPES_DIR / "README.md"
    assert index.is_file()
    assert len(index.read_text(encoding="utf-8").strip()) >= 200


@pytest.mark.parametrize("path", sorted(RECIPES_DIR.rglob("*.md")), ids=_rel)
def test_recipe_readme_substantive(path):
    assert len(path.read_text(encoding="utf-8").strip()) >= 200


@pytest.mark.parametrize("path", _json_files(), ids=_rel)
def test_recipe_json_parses(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


_GOAL_PARAMS = [pytest.param(path, data, id=_rel(path)) for path, data in _goal_docs()]


@pytest.mark.parametrize("path,data", _GOAL_PARAMS)
def test_goal_document_structure(path, data):
    goal_id = data["goal_id"]
    assert isinstance(goal_id, str) and goal_id.strip()

    contracts = data.get("contracts")
    assert isinstance(contracts, list), f"{_rel(path)}: goal documents should carry a contracts list"

    task_ids = {c.get("task_id") for c in contracts if isinstance(c, dict)}
    for entry in contracts:
        assert isinstance(entry, dict), f"{_rel(path)}: plan entries must be dicts"
        tid = entry.get("task_id")
        assert isinstance(tid, str) and tid.strip(), f"{_rel(path)}: plan entry missing task_id"
        deps = entry.get("depends_on", [])
        assert isinstance(deps, list), f"{_rel(path)}: depends_on must be a list on the plan entry ({tid})"
        unknown = [d for d in deps if d not in task_ids]
        assert not unknown, f"{_rel(path)}: {tid} depends on unknown tasks: {unknown}"


def test_embedded_contracts_validate_against_schema():
    total = 0
    for path, data in _goal_docs():
        for index, contract in enumerate(_walk_contract_dicts(data)):
            errors = validate_contract(contract)
            assert errors == [], f"{_rel(path)} contract[{index}] ({contract.get('task_id')}) invalid: {errors}"
            total += 1
    assert total >= 5, f"expected recipe goal docs to embed >= 5 contracts, found {total}"
