"""Tests for configuration module."""

import json
import os

from orchestrator.config import (
    OrchestratorConfig,
)


class TestConfigDefaults:
    def test_default_workspace_root_exists(self):
        cfg = OrchestratorConfig()
        assert cfg.workspace_root
        assert os.path.isabs(cfg.workspace_root)

    def test_default_run_dir(self):
        cfg = OrchestratorConfig()
        assert "scratch" in cfg.run_dir

    def test_default_worker(self):
        cfg = OrchestratorConfig()
        assert cfg.worker.model == "gemini:gemini-3.6-flash"
        assert cfg.worker.max_attempts == 3
        assert cfg.worker.timeout_sec == 300

    def test_default_pool_disabled(self):
        cfg = OrchestratorConfig()
        assert cfg.pool.enabled is False
        assert cfg.pool.max_workers == 3

    def test_default_scope(self):
        cfg = OrchestratorConfig()
        assert "scratch/" in cfg.scope.allow
        assert "AGENTS.md" in cfg.scope.deny


class TestConfigRoundtrip:
    def test_to_dict(self):
        cfg = OrchestratorConfig()
        d = cfg.to_dict()
        assert d["worker"]["model"] == "gemini:gemini-3.6-flash"

    def test_from_dict(self):
        cfg = OrchestratorConfig.from_dict(
            {
                "worker": {"model": "custom-model", "max_attempts": 5},
                "pool": {"enabled": True, "max_workers": 4},
            }
        )
        assert cfg.worker.model == "custom-model"
        assert cfg.worker.max_attempts == 5
        assert cfg.pool.enabled is True
        assert cfg.pool.max_workers == 4

    def test_from_dict_empty(self):
        cfg = OrchestratorConfig.from_dict({})
        assert cfg.worker.model == "gemini:gemini-3.6-flash"

    def test_load_from_json_file(self, tmp_path):
        config_path = os.path.join(str(tmp_path), "config.json")
        data = {
            "worker": {"model": "file-model", "max_attempts": 2},
            "pool": {"enabled": True},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        cfg = OrchestratorConfig.load(config_path)
        assert cfg.worker.model == "file-model"
        assert cfg.worker.max_attempts == 2
        assert cfg.pool.enabled is True

    def test_load_from_nonexistent_file(self):
        cfg = OrchestratorConfig.load("/nonexistent/config.json")
        assert cfg.worker.model == "gemini:gemini-3.6-flash"

    def test_load_from_bad_json(self, tmp_path):
        config_path = os.path.join(str(tmp_path), "bad.json")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{invalid")
        cfg = OrchestratorConfig.load(config_path)
        assert cfg.worker.model == "gemini:gemini-3.6-flash"


class TestConfigEnvOverride:
    def test_env_run_dir(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_RUN_DIR", "/custom/run")
        cfg = OrchestratorConfig.load()
        assert cfg.run_dir == "/custom/run"

    def test_env_model(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_MODEL", "env-model")
        cfg = OrchestratorConfig.load()
        assert cfg.worker.model == "env-model"

    def test_env_max_attempts(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_MAX_ATTEMPTS", "7")
        cfg = OrchestratorConfig.load()
        assert cfg.worker.max_attempts == 7

    def test_env_max_attempts_invalid(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_MAX_ATTEMPTS", "not-a-number")
        cfg = OrchestratorConfig.load()
        assert cfg.worker.max_attempts == 3

    def test_env_parallel(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_PARALLEL", "true")
        cfg = OrchestratorConfig.load()
        assert cfg.pool.enabled is True

    def test_env_parallel_false(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_PARALLEL", "0")
        cfg = OrchestratorConfig.load()
        assert cfg.pool.enabled is False

    def test_env_max_workers(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_MAX_WORKERS", "6")
        cfg = OrchestratorConfig.load()
        assert cfg.pool.max_workers == 6

    def test_env_timeout(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_TIMEOUT", "600")
        cfg = OrchestratorConfig.load()
        assert cfg.worker.timeout_sec == 600

    def test_env_overrides_file(self, tmp_path, monkeypatch):
        config_path = os.path.join(str(tmp_path), "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"worker": {"model": "file-model"}}, f)
        monkeypatch.setenv("ORCHESTRATOR_MODEL", "env-override")
        cfg = OrchestratorConfig.load(config_path)
        assert cfg.worker.model == "env-override"


class TestConfigDisplay:
    def test_display_includes_worker(self):
        cfg = OrchestratorConfig()
        out = cfg.display()
        assert "Worker:" in out
        assert cfg.worker.model in out

    def test_display_includes_pool(self):
        cfg = OrchestratorConfig()
        out = cfg.display()
        assert "Pool:" in out

    def test_display_includes_scope(self):
        cfg = OrchestratorConfig()
        out = cfg.display()
        assert "Scope:" in out
        assert "scratch/" in out
