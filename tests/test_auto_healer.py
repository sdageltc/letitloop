"""Unit tests for AutoHealer and lil heal CLI command."""

import subprocess
import sys
from pathlib import Path

from orchestrator.auto_healer import AutoHealer


def test_auto_healer_clean_workspace(tmp_path):
    """Clean workspace returns success with 0 iterations."""
    test_file = tmp_path / "app.py"
    test_file.write_text("def hello() -> str:\n    return 'world'\n", encoding="utf-8")

    healer = AutoHealer(workspace_dir=tmp_path, max_iterations=2, run_ruff=True, run_pytest=False)
    result = healer.heal()

    assert result.success is True
    assert result.iterations == 0
    assert "No errors detected" in result.fixes_applied[0]


def test_auto_healer_fixes_unused_import(tmp_path):
    """AutoHealer automatically detects and fixes ruff lint unused imports."""
    test_file = tmp_path / "module.py"
    # Unsorted import and unused import
    test_file.write_text("import time\nimport sys\ndef compute():\n    return 42\n", encoding="utf-8")

    healer = AutoHealer(workspace_dir=tmp_path, max_iterations=3, run_ruff=True, run_pytest=False)
    result = healer.heal()

    assert result.success is True
    assert result.iterations == 1
    assert len(result.fixes_applied) >= 1
    # Check that unused imports were scrubbed
    content = test_file.read_text(encoding="utf-8")
    assert "import time" not in content
    assert "import sys" not in content
    assert "def compute():" in content


def test_auto_healer_unresolvable_syntax_error(tmp_path):
    """AutoHealer flags unresolvable syntax errors if loop exhausts."""
    test_file = tmp_path / "broken.py"
    test_file.write_text("def broken(\n", encoding="utf-8")

    healer = AutoHealer(workspace_dir=tmp_path, max_iterations=2, run_ruff=True, run_pytest=False)
    result = healer.heal()

    assert result.success is False
    assert result.iterations == 2
    assert len(result.remaining_errors) > 0


def test_cli_heal_command(tmp_path):
    """lil heal CLI executes cleanly on workspace."""
    test_file = tmp_path / "calc.py"
    test_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    cmd = [sys.executable, "-m", "orchestrator.cli", "heal", "--dir", str(tmp_path), "--no-pytest", "--json"]
    res = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)
    assert res.returncode == 0
    assert '"success": true' in res.stdout
