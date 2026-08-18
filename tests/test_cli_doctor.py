from unittest import mock

import pytest

from orchestrator.cli import main
from orchestrator.env_doctor import _probe_endpoint, run_env_doctor


@pytest.fixture(autouse=True)
def _isolate_run_dir(monkeypatch, tmp_path):
    """Ensure all doctor tests run in an isolated scratch directory without host pollution."""
    monkeypatch.setenv("LIL_RUN_DIR", str(tmp_path / "orchestrator_runs"))


def test_cli_doctor_no_args_routes_to_env_doctor():
    with mock.patch("orchestrator.cli.sys.argv", ["lil", "doctor"]):
        with mock.patch("orchestrator.env_doctor.run_env_doctor", return_value=0) as mock_env_doctor:
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0
            mock_env_doctor.assert_called_once_with(check_connectivity=False)


def test_cli_doctor_probe_flag_passed():
    with mock.patch("orchestrator.cli.sys.argv", ["lil", "doctor", "--probe"]):
        with mock.patch("orchestrator.env_doctor.run_env_doctor", return_value=0) as mock_env_doctor:
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0
            mock_env_doctor.assert_called_once_with(check_connectivity=True)


def test_env_doctor_pass(capsys, tmp_path):
    target_dir = str(tmp_path / "custom_runs")
    with mock.patch("orchestrator.env_doctor.sys.version_info", (3, 11)):
        with mock.patch("orchestrator.env_doctor.os.access", return_value=True):
            with mock.patch("orchestrator.env_doctor.shutil.disk_usage", return_value=(100, 50, 50 * (2**30))):
                result = run_env_doctor(run_dir=target_dir)
                assert result == 0
                captured = capsys.readouterr()
                assert "Python" in captured.out
                assert "(OK)" in captured.out
                assert target_dir in captured.out
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


def test_probe_endpoint_success():
    with mock.patch("orchestrator.env_doctor.urllib.request.urlopen") as mock_open:
        mock_resp = mock.MagicMock()
        mock_resp.status = 200
        mock_open.return_value.__enter__.return_value = mock_resp
        assert _probe_endpoint("https://api.openai.com/v1/models") is True


def test_probe_endpoint_http_401_reachable():
    import urllib.error

    with mock.patch("orchestrator.env_doctor.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError(
            url="https://api.openai.com/v1/models", code=401, msg="Unauthorized", hdrs={}, fp=None
        )
        assert _probe_endpoint("https://api.openai.com/v1/models") is True


def test_probe_endpoint_unreachable_network_error():
    import urllib.error

    with mock.patch("orchestrator.env_doctor.urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.URLError(reason="Connection refused")
        assert _probe_endpoint("https://invalid-host-name.local") is False


def test_env_doctor_with_connectivity_check(capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    with mock.patch("orchestrator.env_doctor._probe_endpoint", return_value=True):
        result = run_env_doctor(check_connectivity=True)
        assert result == 0
        captured = capsys.readouterr()
        assert "OPENAI_API_KEY (configured, reachable)" in captured.out
