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
    for path in fixture_dir.glob("*_contract.json"):
        jsonschema.validate(json.loads(path.read_text()), get_schema("contract"))
    for path in fixture_dir.glob("*_goal.json"):
        jsonschema.validate(json.loads(path.read_text()), get_schema("goal"))


def test_schema_command_prints_selected_schema():
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "schema", "--kind", "goal"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == get_schema("goal")
