"""Tests for deterministic verifier."""

import json
import os
import tempfile

import pytest

from orchestrator.contract import Contract
from orchestrator.verifier import VerifierResult, run_checks, run_verification

pytestmark = pytest.mark.fast


def _make_contract(checks):
    base = {
        "task_id": "verifier-test",
        "title": "Verifier test",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/test/"], "deny": []},
        "objective": "test",
        "worker": {"model": "m", "max_attempts": 3},
        "inputs": [],
        "outputs": [{"path": "scratch/test/output.txt"}],
        "acceptance_checks": checks,
        "qc": {"required": False, "lens": "code_correctness"},
    }
    return Contract(base)


class TestCommandCheck:
    def test_command_exit_zero_passes(self):
        results = run_checks(
            [{"id": "c1", "kind": "command", "command": "python --version", "expected": 0}], workspace_root="."
        )
        assert len(results) == 1
        assert results[0].passed, f"python --version should pass: {results[0].message}"
        assert results[0].exit_code == 0

    def test_command_exit_nonzero_fails(self):
        results = run_checks(
            [{"id": "c2", "kind": "command", "command": "python -c 'exit(1)'", "expected": 0}], workspace_root="."
        )
        assert not results[0].passed

    def test_command_nested_quotes_win(self):
        """AUT-005: nested quotes inside -c must survive on Windows (verified
        empirically: shell=True is the only form that passes)."""
        cmd = "python -c \"import sys; print('verification_ok')\""
        results = run_checks([{"id": "c3", "kind": "command", "command": cmd, "expected": 0}], workspace_root=".")
        assert results[0].passed, f"nested-quote command failed: {results[0].stderr}"
        assert "verification_ok" in results[0].stdout

    def test_command_records_stdout_stderr(self):
        results = run_checks(
            [{"id": "c3", "kind": "command", "command": "python -c 'print(\"hello\")'", "expected": 0}],
            workspace_root=".",
        )
        assert "hello" in results[0].stdout


class TestFileExistsCheck:
    def test_file_exists_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "test.txt")
            with open(fpath, "w") as f:
                f.write("content")
            results = run_checks(
                [{"id": "f1", "kind": "file_exists", "path": "test.txt", "expected": "nonempty"}], workspace_root=td
            )
            assert results[0].passed

    def test_file_missing_fails(self):
        with tempfile.TemporaryDirectory() as td:
            results = run_checks(
                [{"id": "f2", "kind": "file_exists", "path": "nonexistent.txt", "expected": False}], workspace_root=td
            )
            assert not results[0].passed
            assert "not found" in results[0].message

    def test_empty_file_fails_nonempty(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "empty.txt")
            with open(fpath, "w"):
                pass
            results = run_checks(
                [{"id": "f3", "kind": "file_exists", "path": "empty.txt", "expected": "nonempty"}], workspace_root=td
            )
            assert not results[0].passed
            assert "empty" in results[0].message.lower()


class TestJsonSchemaCheck:
    def test_valid_json_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "data.json")
            with open(fpath, "w") as f:
                json.dump({"key": "value"}, f)
            results = run_checks(
                [{"id": "j1", "kind": "json_schema", "path": "data.json", "expected": {"required": ["key"]}}],
                workspace_root=td,
            )
            assert results[0].passed

    def test_invalid_json_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "bad.json")
            with open(fpath, "w") as f:
                f.write("{invalid}")
            results = run_checks([{"id": "j2", "kind": "json_schema", "path": "bad.json"}], workspace_root=td)
            assert not results[0].passed
            assert "parse" in results[0].message.lower()

    def test_missing_required_key_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "data.json")
            with open(fpath, "w") as f:
                json.dump({"foo": 1}, f)
            results = run_checks(
                [{"id": "j3", "kind": "json_schema", "path": "data.json", "expected": {"required": ["missing_key"]}}],
                workspace_root=td,
            )
            assert not results[0].passed


class TestContentCheck:
    def test_content_exact_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "hello.py")
            with open(fpath, "w") as f:
                f.write("def greet():\n    pass\n")
            results = run_checks(
                [{"id": "ce1", "kind": "content_exact", "path": "hello.py", "expected": "def greet():\n    pass\n"}],
                workspace_root=td,
            )
            assert results[0].passed

    def test_content_exact_fails_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "hello.py")
            with open(fpath, "w") as f:
                f.write("x = 1")
            results = run_checks(
                [{"id": "ce2", "kind": "content_exact", "path": "hello.py", "expected": "y = 2"}], workspace_root=td
            )
            assert not results[0].passed

    def test_content_regex_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "code.py")
            with open(fpath, "w") as f:
                f.write("def greet(name):\n    return f'Hello {name}'")
            results = run_checks(
                [{"id": "cr1", "kind": "content_regex", "path": "code.py", "expected": r"def greet\("}],
                workspace_root=td,
            )
            assert results[0].passed

    def test_content_regex_no_match_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "code.py")
            with open(fpath, "w") as f:
                f.write("x = 1")
            results = run_checks(
                [{"id": "cr2", "kind": "content_regex", "path": "code.py", "expected": r"def greet"}], workspace_root=td
            )
            assert not results[0].passed

    def test_missing_file_fails_content_check(self):
        with tempfile.TemporaryDirectory() as td:
            results = run_checks(
                [{"id": "cr3", "kind": "content_regex", "path": "nonexistent.py", "expected": "foo"}], workspace_root=td
            )
            assert not results[0].passed
            assert "not found" in results[0].message


class TestRunVerification:
    def test_all_checks_passed(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "test.txt")
            with open(fpath, "w") as f:
                f.write("hello")
            contract = _make_contract(
                [
                    {"id": "v1", "kind": "file_exists", "path": "test.txt", "expected": True},
                    {"id": "v2", "kind": "content_exact", "path": "test.txt", "expected": "hello"},
                ]
            )
            all_passed, results, evidence_path = run_verification(contract, td, td)
            assert all_passed

    def test_any_failure_causes_not_all_passed(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "test.txt")
            with open(fpath, "w") as f:
                f.write("hello")
            contract = _make_contract(
                [
                    {"id": "v1", "kind": "file_exists", "path": "test.txt", "expected": True},
                    {"id": "v2", "kind": "file_exists", "path": "missing.txt", "expected": True},
                ]
            )
            all_passed, results, _ = run_verification(contract, td, td)
            assert not all_passed

    def test_evidence_file_written(self):
        with tempfile.TemporaryDirectory() as td:
            contract = _make_contract([])
            _, _, evidence_path = run_verification(contract, td, td)
            assert evidence_path is not None
            assert os.path.isfile(evidence_path)
            with open(evidence_path) as f:
                evidence = json.load(f)
            assert "verification_results" in evidence
            assert "all_passed" in evidence


class TestSyntaxCheck:
    def test_python_syntax_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "code.py")
            with open(fpath, "w") as f:
                f.write("def foo():\n    return 42\n")
            results = run_checks(
                [{"id": "s1", "kind": "syntax", "path": "code.py", "expected": "python"}], workspace_root=td
            )
            assert results[0].passed

    def test_python_syntax_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "bad.py")
            with open(fpath, "w") as f:
                f.write("def foo(:\n    pass\n")
            results = run_checks(
                [{"id": "s2", "kind": "syntax", "path": "bad.py", "expected": "python"}], workspace_root=td
            )
            assert not results[0].passed
            assert "SyntaxError" in results[0].message

    def test_json_syntax_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "data.json")
            with open(fpath, "w") as f:
                json.dump({"key": "val"}, f)
            results = run_checks(
                [{"id": "s3", "kind": "syntax", "path": "data.json", "expected": "json"}], workspace_root=td
            )
            assert results[0].passed

    def test_json_syntax_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "bad.json")
            with open(fpath, "w") as f:
                f.write("{invalid}")
            results = run_checks(
                [{"id": "s4", "kind": "syntax", "path": "bad.json", "expected": "json"}], workspace_root=td
            )
            assert not results[0].passed

    def test_unsupported_lang_fails_closed(self):
        """Fail-closed: an unsupported-language syntax check is an
        unsatisfiable required check — it must FAIL, not pass."""
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "file.xyz")
            with open(fpath, "w") as f:
                f.write("stuff")
            results = run_checks(
                [{"id": "s5", "kind": "syntax", "path": "file.xyz", "expected": "xyz"}], workspace_root=td
            )
            assert not results[0].passed
            assert "skipped" in results[0].message

    def test_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as td:
            results = run_checks(
                [{"id": "s6", "kind": "syntax", "path": "nope.py", "expected": "python"}], workspace_root=td
            )
            assert not results[0].passed


class TestHygieneCheck:
    def test_clean_file_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "main.py")
            with open(fpath, "w") as f:
                f.write("def foo():\n    return 42\n")
            results = run_checks([{"id": "h1", "kind": "hygiene", "path": "main.py"}], workspace_root=td)
            assert results[0].passed

    def test_markdown_fence_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.py")
            with open(fpath, "w") as f:
                f.write("```python\nx=1\n```")
            results = run_checks([{"id": "h2", "kind": "hygiene", "path": "out.py"}], workspace_root=td)
            assert not results[0].passed

    def test_placeholder_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.py")
            with open(fpath, "w") as f:
                f.write("TODO: implement this")
            results = run_checks([{"id": "h3", "kind": "hygiene", "path": "out.py"}], workspace_root=td)
            assert not results[0].passed

    def test_conversational_leak_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.py")
            with open(fpath, "w") as f:
                f.write("Sure! Here is the code:\ndef foo():\n    pass")
            results = run_checks([{"id": "h4", "kind": "hygiene", "path": "out.py"}], workspace_root=td)
            assert not results[0].passed

    def test_not_implemented_error_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.py")
            with open(fpath, "w") as f:
                f.write("def foo():\n    raise NotImplementedError")
            results = run_checks([{"id": "h5", "kind": "hygiene", "path": "out.py"}], workspace_root=td)
            assert not results[0].passed

    def test_breakpoint_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.py")
            with open(fpath, "w") as f:
                f.write("breakpoint()\nx=1")
            results = run_checks([{"id": "h6", "kind": "hygiene", "path": "out.py"}], workspace_root=td)
            assert not results[0].passed

    def test_secret_aws_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.py")
            with open(fpath, "w") as f:
                f.write("aws_key = 'AKIA0123456789ABCDEF'")
            results = run_checks([{"id": "h7", "kind": "hygiene", "path": "out.py"}], workspace_root=td)
            assert not results[0].passed

    def test_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as td:
            results = run_checks([{"id": "h8", "kind": "hygiene", "path": "nope.py"}], workspace_root=td)
            assert not results[0].passed


class TestMinSizeCheck:
    def test_large_enough_passes(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.txt")
            with open(fpath, "w") as f:
                f.write("x" * 100)
            results = run_checks(
                [{"id": "m1", "kind": "min_size", "path": "out.txt", "expected": 50}], workspace_root=td
            )
            assert results[0].passed

    def test_too_small_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "out.txt")
            with open(fpath, "w") as f:
                f.write("short")
            results = run_checks(
                [{"id": "m2", "kind": "min_size", "path": "out.txt", "expected": 100}], workspace_root=td
            )
            assert not results[0].passed

    def test_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as td:
            results = run_checks(
                [{"id": "m3", "kind": "min_size", "path": "nope.txt", "expected": 1}], workspace_root=td
            )
            assert not results[0].passed


class TestVerifierResult:
    def test_to_dict(self):
        vr = VerifierResult(
            check_id="c1", kind="command", passed=True, message="exit code 0", stdout="ok", stderr="", exit_code=0
        )
        d = vr.to_dict()
        assert d["check_id"] == "c1"
        assert d["passed"] is True
        assert d["exit_code"] == 0

    def test_repr(self):
        vr = VerifierResult("c1", "command", True)
        assert "PASS" in repr(vr)
        vr2 = VerifierResult("c2", "command", False)
        assert "FAIL" in repr(vr2)


class TestRequiredSectionsCheck:
    """required_sections must be JSON-aware: match top-level JSON keys, not
    only markdown headings (self-audit discovery 2026-07-31)."""

    def test_json_output_top_level_keys_pass(self, tmp_path):
        p = tmp_path / "out.json"
        p.write_text(
            json.dumps(
                {
                    "subsystems": [],
                    "file_mappings": {},
                    "entry_points": [],
                }
            ),
            encoding="utf-8",
        )
        results = run_checks(
            [
                {
                    "id": "rs",
                    "kind": "required_sections",
                    "path": str(p),
                    "expected": ["subsystems", "file_mappings", "entry_points"],
                },
            ],
            workspace_root=".",
        )
        assert results[0].passed, results[0].message

    def test_json_output_missing_key_fails(self, tmp_path):
        p = tmp_path / "out.json"
        p.write_text(json.dumps({"subsystems": []}), encoding="utf-8")
        results = run_checks(
            [
                {"id": "rs", "kind": "required_sections", "path": str(p), "expected": ["subsystems", "entry_points"]},
            ],
            workspace_root=".",
        )
        assert not results[0].passed
        assert "entry_points" in results[0].message

    def test_json_case_insensitive_match(self, tmp_path):
        p = tmp_path / "out.json"
        p.write_text(json.dumps({"SubSystems": []}), encoding="utf-8")
        results = run_checks(
            [
                {"id": "rs", "kind": "required_sections", "path": str(p), "expected": ["subsystems"]},
            ],
            workspace_root=".",
        )
        assert results[0].passed, results[0].message

    def test_markdown_heading_detection_still_works(self, tmp_path):
        p = tmp_path / "out.md"
        p.write_text("# Executive Verdict\n\n## System Map\n", encoding="utf-8")
        results = run_checks(
            [
                {
                    "id": "rs",
                    "kind": "required_sections",
                    "path": str(p),
                    "expected": ["Executive Verdict", "System Map"],
                },
            ],
            workspace_root=".",
        )
        assert results[0].passed, results[0].message


class TestCommandCheckEnhancements:
    def test_env_scrub(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "secret-token-12345")
        cmd = "python -c \"import os; print(os.environ.get('OPENAI_API_KEY'))\""
        results = run_checks([{"id": "es1", "kind": "command", "command": cmd, "expected": 0}], workspace_root=".")
        assert results[0].passed
        assert results[0].stdout.strip() == "None"

    def test_output_cap(self):
        cmd = "python -c \"print('A' * 150000)\""
        results = run_checks([{"id": "oc1", "kind": "command", "command": cmd, "expected": 0}], workspace_root=".")
        assert results[0].passed
        assert len(results[0].stdout) <= 100016
        assert "...[truncated]" in results[0].stdout

    def test_output_cap_monkeypatched(self, monkeypatch):
        import orchestrator.verifier as v

        monkeypatch.setattr(v, "_MAX_OUTPUT_CHARS", 100)
        cmd = "python -c \"print('A' * 500)\""
        results = run_checks([{"id": "oc2", "kind": "command", "command": cmd, "expected": 0}], workspace_root=".")
        assert results[0].passed
        assert len(results[0].stdout) <= 120
        assert "...[truncated]" in results[0].stdout

    @pytest.mark.skipif(os.name == "nt", reason="Tree-kill test is POSIX only")
    def test_tree_kill_posix(self, tmp_path):
        pid_file = str(tmp_path / "child.pid").replace("\\", "/")
        script = (
            "import subprocess, sys, time; "
            f"p = subprocess.Popen([sys.executable, '-c', 'import time, os; open(\"{pid_file}\", \"w\").write(str(os.getpid())); time.sleep(30)']); "
            "time.sleep(30)"
        )
        cmd = f"python -c '{script}'"
        results = run_checks(
            [{"id": "tk1", "kind": "command", "command": cmd, "expected": 0, "timeout_sec": 1}],
            workspace_root=str(tmp_path),
        )
        assert not results[0].passed
        assert "timed out" in results[0].message

        import time

        time.sleep(0.5)
        if os.path.exists(pid_file):
            with open(pid_file, "r") as f:
                child_pid = int(f.read().strip())
            with pytest.raises(OSError):
                os.kill(child_pid, 0)

    def test_env_scrub_pattern_keys(self, monkeypatch):
        """QC 2026-08-02 (P1-1): pattern-based scrub catches repo secrets that
        are not in the exact-name denylist."""
        secret_names = (
            "NVIDIA_API_KEY",
            "CUSTOM_SERVICE_KEY",
            "LLM_API_KEY",
            "BACKEND_API_KEY",
            "HF_TOKEN",
            "WORKER_TOKEN",
            "TOKEN_GATE_CAP",
            "DB_PASSWORD",
            "SOME_CREDENTIAL",
            "MY_TOKENS",
            "DB_SECRETS",
            "SITE_PASSWORDS",
            "USER_CREDENTIALS",
        )
        for name in secret_names:
            monkeypatch.setenv(name, "should-be-scrubbed")
        # Child prints the VALUE of each candidate key; the scrub must leave
        # them unset so os.environ.get returns None.
        probe = "import os; print('|'.join(str(os.environ.get(k)) for k in %r))" % (list(secret_names),)
        cmd = f'python -c "{probe}"'
        results = run_checks([{"id": "ps1", "kind": "command", "command": cmd, "expected": 0}], workspace_root=".")
        assert results[0].passed
        values = results[0].stdout.strip().split("|")
        assert len(values) == len(secret_names)
        assert all(v == "None" for v in values), f"secrets leaked to child env: {results[0].stdout.strip()}"

    def test_malformed_timeout_sec_does_not_crash(self):
        """QC 2026-08-02 (P1-2): malformed timeout_sec from an LLM-generated
        contract must fall back to the default, not blow up the task."""
        for bad in ("10s", 30.5, None, [], {}):
            results = run_checks(
                [{"id": "mt1", "kind": "command", "command": "python --version", "expected": 0, "timeout_sec": bad}],
                workspace_root=".",
            )
            assert results[0].passed, f"timeout_sec={bad!r} caused failure"

    def test_quote_preservation_for_data_tokens(self):
        """QC 2026-08-02 (P1-3): on Windows, double quotes are a CRT quoting
        delimiter (stripped); single quotes are literal (preserved) unless used
        for code-arg grouping."""
        import orchestrator.verifier as v

        assert v._normalize_win_args(["echo", "'foo'"]) == ["echo", "'foo'"]
        assert v._normalize_win_args(["echo", '"bar"']) == ["echo", "bar"]

    def test_quote_strip_for_code_flag_and_whitespace(self):
        """QC 2026-08-02 (P1-3): outer quotes are stripped for code-flag args
        and whitespace-grouped tokens."""
        import orchestrator.verifier as v

        assert v._normalize_win_args(["python", "-c", "'exit(1)'"]) == ["python", "-c", "exit(1)"]
        assert v._normalize_win_args(["python", "-c", '"import sys; print(1)"']) == [
            "python",
            "-c",
            "import sys; print(1)",
        ]
        assert v._normalize_win_args(["echo", '"hello world"']) == ["echo", "hello world"]

    def test_empty_command_guard(self):
        """QC 2026-08-02 (P2): empty command string fails cleanly instead of
        raising ValueError from Popen([])."""
        results = run_checks([{"id": "ec1", "kind": "command", "command": "", "expected": 0}], workspace_root=".")
        assert not results[0].passed
        assert "empty command" in results[0].message
