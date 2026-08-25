"""Engine batch 5 — materialize revival + containment, consistency guards, lil goals."""

import json
import os
import tempfile


class TestMaterialize:
    def _contract(self):
        from orchestrator.contract import Contract

        return Contract(
            {
                "task_id": "t",
                "title": "t",
                "status": "drafted",
                "risk_tier": "auto",
                "workspace_scope": {"allow": ["src/"], "deny": []},
                "objective": "x",
                "worker": {"model": "m", "max_attempts": 1},
                "outputs": [{"path": "src/a.py"}, {"path": "src/b.py"}],
                "acceptance_checks": [],
                "qc": {"required": False, "lens": "code_correctness"},
            }
        )

    def test_structured_multi_file_artifacts_materialize(self, tmp_path):
        """Regression: the structured path was dead (import of nonexistent
        .hybrid_parser) so multi-file JSON payloads were dumped as a raw blob."""
        from orchestrator.worker import _materialize_outputs

        ws = str(tmp_path)
        stdout = json.dumps(
            [
                {"path": "src/a.py", "content": "A = 1\n"},
                {"path": "src/b.py", "content": "B = 2\n"},
            ]
        )
        written = _materialize_outputs(self._contract(), ws, stdout)
        assert sorted(os.path.basename(p) for p in written) == ["a.py", "b.py"]
        assert open(os.path.join(ws, "src", "a.py")).read() == "A = 1\n"
        assert open(os.path.join(ws, "src", "b.py")).read() == "B = 2\n"

    def test_structured_escape_paths_are_rejected(self, tmp_path):
        from orchestrator.worker import _materialize_outputs

        ws = str(tmp_path)
        stdout = json.dumps(
            [
                {"path": "../escape.py", "content": "pwned"},
                {"path": "/tmp/evil.py", "content": "pwned"},
            ]
        )
        written = _materialize_outputs(self._contract(), ws, stdout)
        ws_real = os.path.realpath(ws)
        for p in written:
            assert os.path.commonpath([os.path.realpath(p), ws_real]) == ws_real, p
        assert not os.path.isfile(os.path.join(os.path.dirname(ws_real), "escape.py"))
        # Declared outputs still materialize via fallback.
        assert os.path.isfile(os.path.join(ws, "src", "a.py"))

    def test_materialize_containment_defense_in_depth(self, tmp_path, monkeypatch):
        """Even if the parser ever returns an out-of-workspace path, the write
        is refused (realpath+commonpath containment) and the fallback runs."""
        import orchestrator.worker as worker_mod
        from orchestrator.parsing import ParsedArtifact, ParseResult

        ws = str(tmp_path)
        malicious = ParsedArtifact(
            path=os.path.join(tempfile.gettempdir(), "evil.py"),
            content="pwned",
            language="python",
            parser_tier="T1_JSON",
        )

        def fake_parse(raw_output, expected_paths):
            return ParseResult(ok=True, artifacts=[malicious], raw_length=len(raw_output))

        import orchestrator.parsing as parsing_mod

        monkeypatch.setattr(parsing_mod, "parse_llm_artifacts", fake_parse)
        worker_mod._materialize_outputs(self._contract(), ws, "[{}]")

        outside = os.path.join(tempfile.gettempdir(), "evil.py")
        if os.path.isfile(outside):
            os.remove(outside)  # must never have been created
            raise AssertionError("out-of-workspace artifact was written")
        # Malicious artifact skipped; fallback materialized the declared outputs.
        assert os.path.isfile(os.path.join(ws, "src", "a.py"))
        assert os.path.isfile(os.path.join(ws, "src", "b.py"))
        with open(os.path.join(ws, "src", "a.py"), encoding="utf-8") as f:
            assert "pwned" not in f.read()

    def test_goals_command_lists_goals(self, tmp_path, monkeypatch, capsys):
        from orchestrator import cli

        run_dir = os.path.join(str(tmp_path), "runs")
        for gid, status in (("g-alpha", "COMPLETE"), ("g-beta", "EXECUTING")):
            d = os.path.join(run_dir, gid)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "goal.json"), "w", encoding="utf-8") as f:
                json.dump({"goal_id": gid, "title": gid, "status": status}, f)
        monkeypatch.setattr(cli, "DEFAULT_RUN_DIR", run_dir)
        cli.cmd_goals()
        out = capsys.readouterr().out
        assert "g-alpha" in out and "COMPLETE" in out
        assert "g-beta" in out and "EXECUTING" in out

    def test_goals_command_empty_run_dir(self, tmp_path, monkeypatch, capsys):
        from orchestrator import cli

        monkeypatch.setattr(cli, "DEFAULT_RUN_DIR", os.path.join(str(tmp_path), "empty"))
        cli.cmd_goals()
        out = capsys.readouterr().out.lower()
        assert "no goals" in out or "0 goals" in out or "none" in out


class TestConsistencyGuards:
    def test_handoff_transitions_single_source(self):
        """handoff must reuse state.LEGAL_TRANSITIONS (no drift copy)."""
        from orchestrator import handoff, state

        assert handoff.LEGAL_TRANSITIONS is state.LEGAL_TRANSITIONS

    def test_memory_bridge_survives_corrupt_tail(self, tmp_path):
        from orchestrator.memory_bridge import MemoryBridge

        path = os.path.join(str(tmp_path), "memory_bridge.jsonl")
        bridge = MemoryBridge(path)
        bridge.append if hasattr(bridge, "append") else None
        with open(path, "a", encoding="utf-8") as f:
            f.write("{corrupt\n")
        bridge2 = MemoryBridge(path)
        bridge2.read()  # must not raise
