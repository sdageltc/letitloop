import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from orchestrator.schemas import get_schema

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["contract", "goal", "mcp"])
def test_committed_schema_matches_generator(kind):
    committed = json.loads((ROOT / "schemas" / f"{kind}.schema.json").read_text())
    generated = get_schema(kind)
    jsonschema.validators.validator_for(generated).check_schema(generated)
    assert committed == generated


def test_every_json_fixture_validates_against_its_schema():
    fixture_dir = ROOT / "orchestrator" / "fixtures"
    contract_paths = list(fixture_dir.glob("*_contract.json"))
    goal_paths = list(fixture_dir.glob("*_goal.json"))
    assert contract_paths, "contract fixture discovery must not be empty"
    assert goal_paths, "goal fixture discovery must not be empty"

    for path in contract_paths:
        jsonschema.validate(json.loads(path.read_text()), get_schema("contract"))
    for path in goal_paths:
        jsonschema.validate(json.loads(path.read_text()), get_schema("goal"))


def test_mcp_schema_accepts_paginated_tools_list_request():
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"cursor": "next-page"},
    }

    jsonschema.validate(request, get_schema("mcp"))


def test_schema_command_prints_selected_schema():
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "schema", "--kind", "goal"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == get_schema("goal")
