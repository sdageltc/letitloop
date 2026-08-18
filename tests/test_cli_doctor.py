import sys
from unittest import mock
import pytest
from orchestrator.cli import main
from orchestrator.env_doctor import run_env_doctor

def test_cli_doctor_no_args_routes_to_env_doctor():
    with mock.patch("orchestrator.cli.sys.argv", ["lil", "doctor"]):
        with mock.patch("orchestrator.env_doctor.run_env_doctor", return_value=0) as mock_env_doctor:
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0
            mock_env_doctor.assert_called_once()

def test_env_doctor_pass(capsys):
    with mock.patch("orchestrator.env_doctor.sys.version_info", (3, 11)):
        with mock.patch("orchestrator.env_doctor.os.access", return_value=True):
            with mock.patch("orchestrator.env_doctor.shutil.disk_usage", return_value=(100, 50, 50 * (2**30))):
                result = run_env_doctor()
                assert result == 0
                captured = capsys.readouterr()
                assert "Python" in captured.out
                assert "(OK)" in captured.out
                assert "Storage" in captured.out
                assert "Agent CLIs:" in captured.out
                assert "Everything is ready" in captured.out

def test_env_doctor_fail_python(capsys):
    with mock.patch("orchestrator.env_doctor.sys.version_info", (3, 9)):
        with mock.patch("orchestrator.env_doctor.os.access", return_value=True):
            result = run_env_doctor()
            assert result == 1
            captured = capsys.readouterr()
            assert "Requires >= 3.11" in captured.out
            assert "Prerequisites failed" in captured.out

def test_env_doctor_fail_storage(capsys):
    with mock.patch("orchestrator.env_doctor.sys.version_info", (3, 11)):
        with mock.patch("orchestrator.env_doctor.os.access", return_value=False):
            result = run_env_doctor()
            assert result == 1
            captured = capsys.readouterr()
            assert "Not writable" in captured.out
            assert "Prerequisites failed" in captured.out
