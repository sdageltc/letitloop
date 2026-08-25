"""Deterministic verifier: command exit, file exists/nonempty, JSON parse, content regex/exact."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from .exceptions import VerifierError as VerifierError


class VerifierResult:
    """Result of a single verification check."""

    def __init__(self, check_id, kind, passed, message="", stdout="", stderr="", exit_code=None):
        self.check_id = check_id
        self.kind = kind
        self.passed = passed
        self.message = message
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    def to_dict(self):
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "passed": self.passed,
            "message": self.message,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"<VerifierResult {self.check_id} {status}>"


_SECRET_ENV_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "API_KEY",
    "SECRET",
    "TOKEN",
)
# QC 2026-08-02 (P1-1): the exact-name denylist missed repo secrets such as
# Scrub any var whose underscore-separated name
# contains a secret-ish segment, plus known prefixes and common composites.
_SECRET_ENV_SEGMENTS = (
    "KEY",
    "KEYS",
    "TOKEN",
    "TOKENS",
    "SECRET",
    "SECRETS",
    "PASSWORD",
    "PASSWORDS",
    "CREDENTIAL",
    "CREDENTIALS",
    "AUTH",
    "DATABASE_URL",
    "MONGODB_URI",
    "REDIS_URL",
    "DSN",
)
_SECRET_ENV_PREFIXES = (
    "NVIDIA_API_KEY",
    "CUSTOM_SERVICE_KEY",
    "LLM_API_KEY",
    "BACKEND_API_KEY",
    "TOKEN_GATE",
    "WORKER_TOKEN",
    "HF_TOKEN",
)
_MAX_OUTPUT_CHARS = 100_000


def _is_secret_env_name(name):
    upper = name.upper()
    if any(upper == exact.upper() for exact in _SECRET_ENV_NAMES):
        return True
    if any(upper.startswith(prefix) for prefix in _SECRET_ENV_PREFIXES):
        return True
    if any(seg in upper.split("_") for seg in _SECRET_ENV_SEGMENTS):
        return True
    no_delim = upper.replace("_", "").replace("-", "")
    for seg in ("PASSWORD", "SECRET", "TOKEN", "APIKEY", "CREDENTIAL"):
        if seg in no_delim:
            return True
    return False


def _get_scrubbed_env():
    env = os.environ.copy()
    for k in list(env.keys()):
        if _is_secret_env_name(k):
            env.pop(k, None)
    return env


_CODE_ARG_FLAGS = {"-c", "-e", "--eval", "-m", "-J", "-E"}


def _normalize_win_args(tokens):
    """Reconcile shlex(posix=False) tokens with Windows CRT argv semantics
    (QC 2026-08-02, P1-3).

    - A token fully wrapped in DOUBLE quotes is a CRT quoting-delimiter pair:
      CommandLineToArgvW strips it, so we strip it unconditionally. This makes
      `grep "ERROR" file` deliver ERROR (not literal `"ERROR"`) to the child.
    - A token fully wrapped in SINGLE quotes is literal on cmd.exe (' is not a
      quote char), so it is preserved UNLESS shlex used it for grouping a code
      argument (whitespace inside, or immediately after a code flag like
      -c/-e/--eval/-m) — in that case the quotes were syntactic, not payload.
    """
    args = []
    prev = None
    for tok in tokens:
        if len(tok) >= 2 and ((tok[0] == "'" and tok[-1] == "'") or (tok[0] == '"' and tok[-1] == '"')):
            inner = tok[1:-1]
            if tok[0] == '"':
                tok = inner
            elif any(c.isspace() for c in inner) or prev in _CODE_ARG_FLAGS:
                tok = inner
        args.append(tok)
        prev = tok
    return args


def _cap_output(text: str, max_chars: Optional[int] = None) -> str:
    if max_chars is None:
        max_chars = _MAX_OUTPUT_CHARS
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated]"
    return text


def _kill_process_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
    else:
        try:
            import signal

            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
        pass


def _run_command_check(command, expected, workspace_root, timeout_sec=60):
    """Run a shell command and check exit code equals expected."""
    if not command:
        return VerifierResult(
            check_id="command", kind="command", passed=False, message="empty command string", exit_code=-1
        )
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": workspace_root,
        "encoding": "utf-8",
        "errors": "replace",
        "env": _get_scrubbed_env(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    try:
        if os.name == "nt":
            # AUT-005 (verified empirically on Windows 2026-07-31): direct
            # exec via shlex.split preserves single/double/nested quotes that
            # `cmd /c` mangles (e.g. python -c "import sys; print('ok')").
            # cmd builtins (echo, dir, ...) are not executables, so fall back
            # to `cmd /c` only when direct exec cannot find the program.
            # CRITICAL: on Windows, posix=False is mandatory to preserve backslashes.
            # Stripping matching outer quotes from args ensures Python receives clean code
            # rather than quoted string literals.
            try:
                if isinstance(command, str):
                    raw = shlex.split(command, posix=False)
                    args = _normalize_win_args(raw)
                else:
                    args = list(command)
                if args and args[0] in ("python", "python3") and sys.executable:
                    args[0] = sys.executable
                proc = subprocess.Popen(args, **kwargs)
            except (FileNotFoundError, OSError):
                proc = subprocess.Popen(["cmd", "/c", command], **kwargs)
        else:
            args = shlex.split(command) if isinstance(command, str) else list(command)
            if args and args[0] in ("python", "python3") and sys.executable:
                args[0] = sys.executable
            proc = subprocess.Popen(args, **kwargs)

        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            return VerifierResult(
                check_id="command",
                kind="command",
                passed=False,
                message=f"command timed out ({timeout_sec}s)",
                exit_code=-1,
            )

        stdout = _cap_output(stdout or "")
        stderr = _cap_output(stderr or "")
        exit_code = proc.returncode
        try:
            expected_code = int(expected)
            passed = exit_code == expected_code
        except (ValueError, TypeError):
            passed = str(exit_code) == str(expected)
        msg = f"exit code {exit_code}, expected {expected}" if not passed else f"exit code {exit_code} matches expected"
        return VerifierResult(
            check_id="command",
            kind="command",
            passed=passed,
            message=msg,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
    except OSError as e:
        return VerifierResult(check_id="command", kind="command", passed=False, message=str(e), exit_code=-1)


def _run_file_exists_check(path, expected_nonempty, workspace_root):
    """Check file exists; optionally non-empty."""
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    exists = os.path.isfile(full_path)
    if not exists:
        return VerifierResult(
            check_id="file_exists", kind="file_exists", passed=False, message=f"file not found: {path}"
        )
    if expected_nonempty:
        size = os.path.getsize(full_path)
        if size == 0:
            return VerifierResult(
                check_id="file_exists", kind="file_exists", passed=False, message=f"file is empty: {path}"
            )
    return VerifierResult(
        check_id="file_exists",
        kind="file_exists",
        passed=True,
        message=f"file exists: {path} ({os.path.getsize(full_path)} bytes)",
    )


def _run_json_schema_check(path, schema, workspace_root):
    """Check file parses as JSON; optionally validate against schema."""
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(
            check_id="json_schema", kind="json_schema", passed=False, message=f"file not found: {path}"
        )
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return VerifierResult(
            check_id="json_schema", kind="json_schema", passed=False, message=f"JSON parse error: {e}"
        )

    if schema is not None:
        if not isinstance(data, dict):
            return VerifierResult(
                check_id="json_schema", kind="json_schema", passed=False, message="JSON root is not a dict"
            )
        if isinstance(schema, dict):
            for key in schema.get("required", []):
                if key not in data:
                    return VerifierResult(
                        check_id="json_schema",
                        kind="json_schema",
                        passed=False,
                        message=f"JSON missing required key: {key}",
                    )

    return VerifierResult(check_id="json_schema", kind="json_schema", passed=True, message="JSON parse OK")


def _run_content_check(path, pattern, kind, workspace_root):
    """Check file content matches exact string or regex."""
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(check_id="content", kind=kind, passed=False, message=f"file not found: {path}")
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="content", kind=kind, passed=False, message=str(e))

    if kind == "content_exact":
        if content == pattern:
            return VerifierResult(check_id="content", kind=kind, passed=True, message="content matches exact")
        else:
            return VerifierResult(check_id="content", kind=kind, passed=False, message="content does not match exact")
    elif kind == "content_regex":
        import concurrent.futures

        def _eval_regex():
            return re.search(pattern, content, re.DOTALL) is not None

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_eval_regex)
                match = future.result(timeout=2.0)
                if match:
                    return VerifierResult(
                        check_id="content", kind=kind, passed=True, message=f"regex matches: {pattern}"
                    )
                else:
                    return VerifierResult(
                        check_id="content", kind=kind, passed=False, message=f"regex no match: {pattern}"
                    )
        except concurrent.futures.TimeoutError:
            return VerifierResult(
                check_id="content",
                kind=kind,
                passed=False,
                message=f"regex timed out (ReDoS protection): {pattern[:50]}",
            )

    return VerifierResult(check_id="content", kind=kind, passed=False, message=f"unknown content kind: {kind}")


_PLACEHOLDER_PATTERN = re.compile(r"(?i)\b(TODO|FIXME|XXX|HACK|TEMP|TBD|PLACEHOLDER|YOUR_CODE_HERE|IMPLEMENT_ME)\b")
_CONVERSATIONAL_PATTERN = re.compile(
    r"(?i)^\s*(sure!|here is the|i cannot|as an ai language model|i hope the)",
    re.MULTILINE,
)
_MARKDOWN_FENCE_START = re.compile(r"^\s*```")
_NOT_IMPLEMENTED_ERROR = re.compile(r"raise\s+NotImplementedError")
_DEBUG_PYTHON = re.compile(r"\b(breakpoint\(\)|pdb\.set_trace\(\))")
_SECRET_AWS = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
_SECRET_KEY_HEADER = re.compile(
    r"-----BEGIN\s+(?:[A-Z0-9_\-]+\s+)?(?:ENCRYPTED\s+)?(?:PRIVATE\s+KEY|(?:RSA|OPENSSH|EC|PGP|DSA)\s+(?:PRIVATE\s+)?KEY)-----",
    re.IGNORECASE,
)


_AST_CACHE: "OrderedDict[str, Tuple[bool, str]]" = OrderedDict()
_AST_CACHE_MAX = 2048


def fast_ast_verify(source_code: str, filename: str = "") -> Tuple[bool, str]:
    """Tier-0 in-memory AST syntax validation with sub-millisecond hash caching (<0.1ms).

    Bounded LRU: the oldest entry is evicted at capacity instead of clearing the
    whole cache, so hot files stay cached across large verification batches.
    """
    h = hashlib.sha256(source_code.encode("utf-8", errors="replace")).hexdigest()
    cached = _AST_CACHE.get(h)
    if cached is not None:
        _AST_CACHE.move_to_end(h)
        return cached
    try:
        ast.parse(source_code, filename=filename or "<memory>")
        res = (True, "Python syntax valid")
    except SyntaxError as e:
        res = (False, f"Python SyntaxError line {e.lineno}: {e.msg}")
    except Exception as e:
        res = (False, f"AST parse failed: {e}")
    while len(_AST_CACHE) >= _AST_CACHE_MAX:
        _AST_CACHE.popitem(last=False)
    _AST_CACHE[h] = res
    return res


_cached_ast_parse = fast_ast_verify


def _run_syntax_check(path, expected_language, workspace_root, optional=False):
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(check_id="syntax", kind="syntax", passed=False, message=f"file not found: {path}")
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="syntax", kind="syntax", passed=False, message=str(e))

    lang = expected_language or _detect_language(path)
    import shutil

    if lang == "python":
        passed, msg = fast_ast_verify(content, filename=full_path)
        return VerifierResult(check_id="syntax", kind="syntax", passed=passed, message=msg)
    elif lang == "json":
        try:
            json.loads(content)
            return VerifierResult(check_id="syntax", kind="syntax", passed=True, message="JSON syntax valid")
        except json.JSONDecodeError as e:
            return VerifierResult(check_id="syntax", kind="syntax", passed=False, message=f"JSON parse error: {e.msg}")
    elif lang in ("javascript", "js"):
        node_bin = shutil.which("node")
        if node_bin:
            try:
                proc = subprocess.run(
                    [node_bin, "--check", full_path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=workspace_root,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode == 0:
                    return VerifierResult(
                        check_id="syntax", kind="syntax", passed=True, message="JavaScript syntax valid (node)"
                    )
                return VerifierResult(
                    check_id="syntax",
                    kind="syntax",
                    passed=False,
                    message=f"JavaScript syntax error: {proc.stderr[:200]}",
                )
            except Exception as e:
                return VerifierResult(
                    check_id="syntax", kind="syntax", passed=False, message=f"JavaScript syntax check failed: {e}"
                )
        if optional:
            return VerifierResult(
                check_id="syntax", kind="syntax", passed=True, message="syntax check skipped: node not found (optional)"
            )
        return VerifierResult(
            check_id="syntax",
            kind="syntax",
            passed=False,
            message="syntax check failed: node unavailable for javascript",
        )
    elif lang in ("typescript", "ts"):
        tsc_bin = shutil.which("tsc") or shutil.which("npx")
        if tsc_bin:
            try:
                cmd = (
                    [tsc_bin, "--noEmit", "--pretty", "false", full_path]
                    if "tsc" in tsc_bin
                    else ["npx", "tsc", "--noEmit", "--pretty", "false", full_path]
                )
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd=workspace_root,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode == 0:
                    return VerifierResult(
                        check_id="syntax", kind="syntax", passed=True, message="TypeScript syntax valid (tsc)"
                    )
                return VerifierResult(
                    check_id="syntax",
                    kind="syntax",
                    passed=False,
                    message=f"TypeScript syntax error: {proc.stdout[:200] or proc.stderr[:200]}",
                )
            except Exception as e:
                return VerifierResult(
                    check_id="syntax", kind="syntax", passed=False, message=f"TypeScript syntax check failed: {e}"
                )
        if optional:
            return VerifierResult(
                check_id="syntax", kind="syntax", passed=True, message="syntax check skipped: tsc not found (optional)"
            )
        return VerifierResult(
            check_id="syntax",
            kind="syntax",
            passed=False,
            message="syntax check failed: tsc unavailable for typescript",
        )
    elif lang == "go":
        go_bin = shutil.which("go")
        if go_bin:
            try:
                proc = subprocess.run(
                    [go_bin, "vet", full_path],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd=workspace_root,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode == 0:
                    return VerifierResult(
                        check_id="syntax", kind="syntax", passed=True, message="Go syntax valid (go vet)"
                    )
                return VerifierResult(
                    check_id="syntax", kind="syntax", passed=False, message=f"Go syntax error: {proc.stderr[:200]}"
                )
            except Exception as e:
                return VerifierResult(
                    check_id="syntax", kind="syntax", passed=False, message=f"Go syntax check failed: {e}"
                )
        if optional:
            return VerifierResult(
                check_id="syntax", kind="syntax", passed=True, message="syntax check skipped: go not found (optional)"
            )
        return VerifierResult(
            check_id="syntax", kind="syntax", passed=False, message="syntax check failed: go unavailable"
        )
    elif lang == "rust":
        cargo_bin = shutil.which("cargo") or shutil.which("rustc")
        if cargo_bin:
            try:
                cmd = [cargo_bin, "check"] if "cargo" in cargo_bin else [cargo_bin, "--parse-only", full_path]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd=workspace_root,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode == 0:
                    return VerifierResult(check_id="syntax", kind="syntax", passed=True, message="Rust syntax valid")
                return VerifierResult(
                    check_id="syntax", kind="syntax", passed=False, message=f"Rust syntax error: {proc.stderr[:200]}"
                )
            except Exception as e:
                return VerifierResult(
                    check_id="syntax", kind="syntax", passed=False, message=f"Rust syntax check failed: {e}"
                )
        if optional:
            return VerifierResult(
                check_id="syntax",
                kind="syntax",
                passed=True,
                message="syntax check skipped: rust tooling not found (optional)",
            )
        return VerifierResult(
            check_id="syntax", kind="syntax", passed=False, message="syntax check failed: rust tooling unavailable"
        )
    else:
        if optional:
            return VerifierResult(
                check_id="syntax",
                kind="syntax",
                passed=True,
                message=f"syntax check skipped: unsupported language {lang} (optional)",
            )
        return VerifierResult(
            check_id="syntax", kind="syntax", passed=False, message=f"syntax check skipped: unsupported language {lang}"
        )


def _detect_language(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".py": "python",
        ".json": "json",
        ".js": "javascript",
        ".ts": "typescript",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".go": "go",
        ".rs": "rust",
    }.get(ext, "unknown")


def _run_hygiene_check(path, expected, workspace_root):
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(check_id="hygiene", kind="hygiene", passed=False, message=f"file not found: {path}")
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="hygiene", kind="hygiene", passed=False, message=str(e))

    content.splitlines()

    if _MARKDOWN_FENCE_START.match(content.strip()):
        return VerifierResult(
            check_id="hygiene", kind="hygiene", passed=False, message="file starts with markdown code fence"
        )

    if _CONVERSATIONAL_PATTERN.search(content):
        return VerifierResult(
            check_id="hygiene", kind="hygiene", passed=False, message="conversational LLM preamble detected"
        )

    if _PLACEHOLDER_PATTERN.search(content):
        return VerifierResult(
            check_id="hygiene", kind="hygiene", passed=False, message="placeholder token detected (TODO/FIXME/etc)"
        )

    if _NOT_IMPLEMENTED_ERROR.search(content):
        return VerifierResult(
            check_id="hygiene", kind="hygiene", passed=False, message="raise NotImplementedError detected"
        )

    lang = _detect_language(path)
    if lang == "python":
        if _DEBUG_PYTHON.search(content):
            return VerifierResult(
                check_id="hygiene", kind="hygiene", passed=False, message="debug statement detected (breakpoint/pdb)"
            )

    if _SECRET_AWS.search(content):
        return VerifierResult(
            check_id="hygiene", kind="hygiene", passed=False, message="AWS credential pattern detected"
        )

    if _SECRET_KEY_HEADER.search(content):
        return VerifierResult(check_id="hygiene", kind="hygiene", passed=False, message="private key header detected")

    return VerifierResult(check_id="hygiene", kind="hygiene", passed=True, message="hygiene checks passed")


def _run_min_size_check(path, min_size, workspace_root):
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(check_id="min_size", kind="min_size", passed=False, message=f"file not found: {path}")
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="min_size", kind="min_size", passed=False, message=str(e))

    size = len(content)
    threshold = int(min_size) if min_size else 1
    if size >= threshold:
        return VerifierResult(
            check_id="min_size", kind="min_size", passed=True, message=f"content length {size} >= {threshold}"
        )
    else:
        return VerifierResult(
            check_id="min_size", kind="min_size", passed=False, message=f"content length {size} < {threshold}"
        )


_SUPPORTED_RENDER_FORMATS = {"markdown", "html"}


def _get_markdown_headings(content: str) -> list:
    """Extract all markdown heading texts (## and ###) from content, excluding code blocks."""
    lines = content.split("\n")
    headings = []
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            heading_text = re.sub(r"^#{1,6}\s+", "", stripped).strip()
            if heading_text:
                headings.append(heading_text)
    return headings


def _run_required_sections_check(path, required_sections, workspace_root):
    """Check file contains all required section headings.

    JSON outputs: required sections are matched against top-level object keys
    (case-insensitive). Markdown/text outputs: matched against markdown
    headings (## / ###). This prevents false failures when required_sections
    is applied to a JSON-output contract (self-audit discovery 2026-07-31).
    """
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(
            check_id="required_sections", kind="required_sections", passed=False, message=f"file not found: {path}"
        )
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="required_sections", kind="required_sections", passed=False, message=str(e))
    if not required_sections:
        return VerifierResult(
            check_id="required_sections", kind="required_sections", passed=True, message="no required sections to check"
        )

    candidates = []
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        candidates = list(data.keys())
        for key, value in data.items():
            if isinstance(value, dict):
                candidates.extend(value.keys())
    else:
        candidates = _get_markdown_headings(content)

    missing = []
    for section in required_sections:
        if not isinstance(section, str):
            continue
        sec_lower = section.lower().strip()
        # Direct match or substring match in any candidate heading
        found = any(sec_lower in c.lower() or c.lower() in sec_lower for c in candidates)
        if not found:
            # Also check if section title keywords appear in candidate headers
            words = [w for w in re.split(r"\W+", sec_lower) if len(w) > 2]
            found = bool(words and any(all(w in c.lower() for w in words) for c in candidates))
        if not found:
            # Check if section title is present as bold or header anywhere in the text
            found = bool(re.search(r"(?i)(?:^#{1,6}\s+|\*\*)[^\n]*" + re.escape(sec_lower), content))
        if not found:
            missing.append(section)
    if missing:
        return VerifierResult(
            check_id="required_sections", kind="required_sections", passed=False, message=f"missing sections: {missing}"
        )
    return VerifierResult(
        check_id="required_sections",
        kind="required_sections",
        passed=True,
        message=f"all {len(required_sections)} required sections found",
    )


def _run_render_check(path, expected_format, workspace_root):
    """Source-level validation for content structure and formatting.

    This is NOT a full renderer — it performs heuristic checks on the source
    content to detect common issues. Supported formats: markdown, html.
    """
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if expected_format not in _SUPPORTED_RENDER_FORMATS:
        return VerifierResult(
            check_id="render",
            kind="render",
            passed=False,
            message=f"unsupported render format: {expected_format!r} (supported: {sorted(_SUPPORTED_RENDER_FORMATS)})",
        )
    if not os.path.isfile(full_path):
        return VerifierResult(check_id="render", kind="render", passed=False, message=f"file not found: {path}")
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="render", kind="render", passed=False, message=str(e))
    if expected_format == "markdown":
        raw_latex = re.findall(r"\$\$(.+?)\$\$|\$([^\$\n]+?)\$|\\\((.*?)\\\)|\\\[(.*?)\\]", content)
        if raw_latex:
            return VerifierResult(
                check_id="render",
                kind="render",
                passed=False,
                message=f"raw LaTeX detected in markdown: {len(raw_latex)} instances",
            )
        unresolved_anchors = re.findall(r"\[([^\]]+)\]\(\)", content)
        if unresolved_anchors:
            return VerifierResult(
                check_id="render", kind="render", passed=False, message=f"unresolved anchors: {unresolved_anchors}"
            )
    elif expected_format == "html":
        if not content.strip().startswith("<"):
            return VerifierResult(
                check_id="render", kind="render", passed=False, message="HTML content does not start with '<'"
            )
    return VerifierResult(
        check_id="render", kind="render", passed=True, message=f"source-level {expected_format} check passed"
    )


def _count_regex_matches(content: str, patterns: list) -> int:
    """Count total regex matches across a list of patterns."""
    total = 0
    for pat in patterns:
        try:
            total += len(re.findall(pat, content))
        except re.error:
            pass
    return total


def _run_contradiction_count_check(path, expected_minimum, workspace_root):
    """Count 'contradiction' or 'contradicts' mentions in file. Must meet minimum."""
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(
            check_id="contradiction_count", kind="contradiction_count", passed=False, message=f"file not found: {path}"
        )
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="contradiction_count", kind="contradiction_count", passed=False, message=str(e))
    count = _count_regex_matches(
        content,
        [
            r"(?i)\bcontradiction\b",
            r"(?i)\bcontrarian\b",
            r"(?i)\btension\s+between\b",
            r"(?i)\bconflicts?\s+with\b",
            r"(?i)\buncomfortable\s+truth\b",
            r"(?i)\binternal(ly)?\s+inconsistent\b",
        ],
    )
    threshold = int(expected_minimum) if expected_minimum else 1
    if count >= threshold:
        return VerifierResult(
            check_id="contradiction_count",
            kind="contradiction_count",
            passed=True,
            message=f"found {count} contradiction mentions >= {threshold}",
        )
    return VerifierResult(
        check_id="contradiction_count",
        kind="contradiction_count",
        passed=False,
        message=f"found {count} contradiction mentions < {threshold}",
    )


def _run_edge_case_count_check(path, expected_minimum, workspace_root):
    """Count edge-case or failure-scenario mentions in file."""
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(
            check_id="edge_case_count", kind="edge_case_count", passed=False, message=f"file not found: {path}"
        )
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="edge_case_count", kind="edge_case_count", passed=False, message=str(e))
    count = _count_regex_matches(
        content,
        [
            r"(?i)\bedge\s+case",
            r"(?i)\bfailure\s+(mode|scenario|case)",
            r"(?i)\bcorner\s+case\b",
            r"(?i)\bif\s+.*\b(fails?|breaks?|crashes|dies?|time.?out)\b",
            r"(?i)\bscenario\s+\d+\b",
        ],
    )
    threshold = int(expected_minimum) if expected_minimum else 1
    if count >= threshold:
        return VerifierResult(
            check_id="edge_case_count",
            kind="edge_case_count",
            passed=True,
            message=f"found {count} edge case mentions >= {threshold}",
        )
    return VerifierResult(
        check_id="edge_case_count",
        kind="edge_case_count",
        passed=False,
        message=f"found {count} edge case mentions < {threshold}",
    )


def _run_schema_count_check(path, expected_minimum, workspace_root):
    """Count JSON schema or YAML frontmatter blocks in file."""
    full_path = os.path.join(workspace_root, path) if not os.path.isabs(path) else path
    if not os.path.isfile(full_path):
        return VerifierResult(
            check_id="schema_count", kind="schema_count", passed=False, message=f"file not found: {path}"
        )
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return VerifierResult(check_id="schema_count", kind="schema_count", passed=False, message=str(e))
    count = _count_regex_matches(
        content,
        [
            r"```json\s*\{",
            r"```yaml",
            r"```yml",
            r"---\n\w+:\s*\w+",
            r"(?i)\bJSON\s+schema\b",
            r"(?i)\b(tier|level|rank|type|category)\s+(table|matrix|classification)\b",
        ],
    )
    threshold = int(expected_minimum) if expected_minimum else 1
    if count >= threshold:
        return VerifierResult(
            check_id="schema_count",
            kind="schema_count",
            passed=True,
            message=f"found {count} schema/structured-artifact indicators >= {threshold}",
        )
    return VerifierResult(
        check_id="schema_count",
        kind="schema_count",
        passed=False,
        message=f"found {count} schema indicators < {threshold}",
    )


def _run_undeclared_outputs_check(declared_outputs, scope_snapshot_path, workspace_root, allowed_paths):
    """Check no files were created outside declared outputs within allowed scope.

    Excludes the supervisor's own runtime files (under run_dir) and sibling tasks'
    declared outputs during parallel execution.
    """
    from .scope import FileBackedScopeRegistry, _walk_matching, is_path_exempt, load_snapshot

    run_dir = os.path.dirname(scope_snapshot_path) if scope_snapshot_path else ""
    before = load_snapshot(run_dir) if scope_snapshot_path else {}
    if scope_snapshot_path and not before:
        return VerifierResult(
            check_id="undeclared_outputs",
            kind="undeclared_outputs",
            passed=False,
            message="Scope snapshot missing or empty: cannot verify output isolation (fail-closed)",
        )
    if not before:
        return VerifierResult(
            check_id="undeclared_outputs",
            kind="undeclared_outputs",
            passed=True,
            message="no scope snapshot specified, skipping",
        )
    declared_abs = set()
    for p in declared_outputs:
        full = os.path.join(workspace_root, p) if not os.path.isabs(p) else p
        declared_abs.add(os.path.normcase(os.path.normpath(full)))

    sibling_outputs = []
    try:
        sibling_outputs = FileBackedScopeRegistry(workspace_root).sibling_declared_outputs()
    except Exception:
        pass

    # Also load plan.json from goal run_dir if available to exempt all sibling declared outputs
    try:
        plan_path = os.path.join(os.path.dirname(run_dir), "plan.json")
        if os.path.isfile(plan_path):
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
                for c in plan_data.get("contracts", []):
                    for o in c.get("outputs", []):
                        if o.get("path"):
                            sibling_outputs.append(o["path"])
    except Exception:
        pass

    after = _walk_matching(workspace_root, allowed_paths)
    run_dir_abs = os.path.normcase(os.path.normpath(os.path.abspath(run_dir)))
    goal_run_dir_abs = os.path.normcase(os.path.normpath(os.path.abspath(os.path.dirname(run_dir)))) if run_dir else ""
    undeclared = []
    for rel_path, _ in after.items():
        if (
            "__pycache__" in rel_path.split(os.sep)
            or "generated" in rel_path.split(os.sep)
            or rel_path.endswith((".pyc", ".pyo", ".pyd"))
        ):
            continue
        abs_path = os.path.normcase(os.path.normpath(os.path.join(workspace_root, rel_path)))
        if abs_path in declared_abs:
            continue
        # Skip supervisor runtime files (under run_dir or parent goal_run_dir)
        if run_dir_abs and abs_path.startswith(run_dir_abs + os.sep):
            continue
        if goal_run_dir_abs and abs_path.startswith(goal_run_dir_abs + os.sep):
            continue
        # Skip sibling tasks' declared outputs
        if sibling_outputs and is_path_exempt(abs_path, sibling_outputs, workspace_root=workspace_root):
            continue
        old_hash = before.get(rel_path)
        if old_hash is None:
            undeclared.append(rel_path)
    if undeclared:
        return VerifierResult(
            check_id="undeclared_outputs",
            kind="undeclared_outputs",
            passed=False,
            message=f"undeclared output files: {undeclared}",
        )
    return VerifierResult(
        check_id="undeclared_outputs", kind="undeclared_outputs", passed=True, message="no undeclared output files"
    )


def run_checks(checks, workspace_root):
    """Run a list of acceptance checks.

    Each check dict must have:
        id, kind, and optionally command/path/expected/schema

    Returns list of VerifierResult.
    """
    results = []
    for check in checks:
        kind = check["kind"]
        check_id = check.get("id", kind)
        if kind == "command":
            command = check.get("command", "")
            expected = check.get("expected", 0)
            # QC 2026-08-01: per-check timeout — check spec overrides the
            # 60s default, clamped to a 300s ceiling so a mis-specified check
            # cannot hang the supervisor indefinitely.
            # QC 2026-08-02 (P1-2): LLM-generated contracts may carry malformed
            # timeout_sec ("10s", 30.5, null) — fall back to the 60s default
            # instead of letting int() crash the whole task into BLOCKED.
            to_sec = check.get("timeout_sec", 60)
            try:
                to_sec = int(to_sec)
            except (ValueError, TypeError):
                to_sec = 60
            to_sec = min(max(to_sec, 1), 300)
            result = _run_command_check(command, expected, workspace_root, timeout_sec=to_sec)
        elif kind == "file_exists":
            path = check.get("path", "")
            exp = check.get("expected", "")
            if isinstance(exp, bool):
                nonempty = exp
            else:
                nonempty = str(exp).lower() in ("true", "nonempty", "1")
            result = _run_file_exists_check(path, nonempty, workspace_root)
        elif kind == "json_schema":
            path = check.get("path", "")
            schema = check.get("expected", {})
            if isinstance(schema, str):
                try:
                    schema = json.loads(schema)
                except json.JSONDecodeError:
                    schema = {}
            result = _run_json_schema_check(path, schema, workspace_root)
        elif kind in ("content_exact", "content_regex"):
            path = check.get("path", "")
            pattern = check.get("expected", "")
            result = _run_content_check(path, pattern, kind, workspace_root)
        elif kind == "syntax":
            path = check.get("path", "")
            lang = check.get("expected", "")
            optional = bool(check.get("optional", False))
            result = _run_syntax_check(path, lang, workspace_root, optional=optional)
        elif kind == "hygiene":
            path = check.get("path", "")
            expected = check.get("expected", True)
            result = _run_hygiene_check(path, expected, workspace_root)
        elif kind == "min_size":
            path = check.get("path", "")
            min_size = check.get("expected", 1)
            result = _run_min_size_check(path, min_size, workspace_root)
        elif kind == "required_sections":
            path = check.get("path", "")
            sections = check.get("expected", [])
            if isinstance(sections, str):
                sections = [s.strip() for s in sections.split(",")]
            result = _run_required_sections_check(path, sections, workspace_root)
        elif kind == "render":
            path = check.get("path", "")
            expected_format = check.get("expected", "markdown")
            result = _run_render_check(path, expected_format, workspace_root)
        elif kind == "undeclared_outputs":
            declared = check.get("declared_outputs", [])
            snapshot = check.get("scope_snapshot_path", "")
            allowed = check.get("allowed_paths", [])
            result = _run_undeclared_outputs_check(declared, snapshot, workspace_root, allowed)
        elif kind == "contradiction_count":
            path = check.get("path", "")
            expected = check.get("expected", 1)
            result = _run_contradiction_count_check(path, expected, workspace_root)
        elif kind == "edge_case_count":
            path = check.get("path", "")
            expected = check.get("expected", 1)
            result = _run_edge_case_count_check(path, expected, workspace_root)
        elif kind == "schema_count":
            path = check.get("path", "")
            expected = check.get("expected", 1)
            result = _run_schema_count_check(path, expected, workspace_root)
        else:
            result = VerifierResult(check_id=check_id, kind=kind, passed=False, message=f"unknown check kind: {kind}")
        result.check_id = check_id
        status = "PASS" if result.passed else "FAIL"
        print(f"[verify] {result.check_id} ({result.kind}): {status}", file=sys.stderr)
        results.append(result)
    return results


class ProofReceipt:
    """Cryptographic proof receipt consumed by letitloop-action and external CI gates."""

    def __init__(self, task_id: str, results: List[VerifierResult], start_time: Optional[float] = None, run_dir: Optional[str] = None):
        self.task_id = task_id
        self.timestamp = time.time()
        start = start_time if start_time is not None else self.timestamp
        self.execution_time_ms = max(0.1, (self.timestamp - start) * 1000.0)
        self.passed = all(r.passed for r in results) if results else True

        # Check AST / Syntax invariants
        syntax_checks = [r for r in results if r.kind == "syntax"]
        self.ast_invariants_valid = all(r.passed for r in syntax_checks) if syntax_checks else True

        # Extract test exit codes and scope violations
        self.test_exit_code = 0 if self.passed else 1
        self.scope_violations: List[str] = []
        for r in results:
            if r.kind == "undeclared_outputs" and not r.passed:
                self.scope_violations.append(r.message)

        # Compute deterministic SHA256 receipt
        raw_payload = f"{self.task_id}:{self.passed}:{self.ast_invariants_valid}:{self.test_exit_code}:{int(self.execution_time_ms)}"
        self.receipt_sha256 = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "passed": self.passed,
            "astInvariantsValid": self.ast_invariants_valid,
            "testExitCode": self.test_exit_code,
            "scopeViolations": self.scope_violations,
            "executionTimeMs": round(self.execution_time_ms, 2),
            "receiptSha256": self.receipt_sha256,
        }

    def write_to_disk(self, run_dir: Optional[str] = None, wal_dir: str = ".bench_wal") -> str:
        payload = self.to_dict()
        # 1. Write to run_dir if provided
        if run_dir:
            os.makedirs(run_dir, exist_ok=True)
            p = os.path.join(run_dir, "proof_receipt.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

        # 2. Write to standard .bench_wal/proof_receipt.json
        wal_path = Path(wal_dir)
        wal_path.mkdir(parents=True, exist_ok=True)
        target = wal_path / "proof_receipt.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(target)


def run_verification(contract, workspace_root, run_dir, start_time: Optional[float] = None):
    """Run all acceptance checks from a contract.

    Returns (all_passed, results, evidence_path).
    """
    checks = contract.acceptance_checks
    print(f"[verify] task={contract.task_id} starting ({len(checks)} checks)", file=sys.stderr)
    results = run_checks(checks, workspace_root)
    all_passed = all(r.passed for r in results)
    passed_count = sum(1 for r in results if r.passed)
    print(f"[verify] task={contract.task_id} finished ({passed_count}/{len(checks)} passed)", file=sys.stderr)

    evidence = {
        "task_id": contract.task_id,
        "verification_results": [r.to_dict() for r in results],
        "all_passed": all_passed,
    }

    # Generate and serialize cryptographic proof receipt
    receipt = ProofReceipt(task_id=contract.task_id, results=results, start_time=start_time, run_dir=run_dir)
    receipt.write_to_disk(run_dir=run_dir, wal_dir=os.path.join(workspace_root or ".", ".bench_wal"))

    evidence_path = None
    if run_dir:
        os.makedirs(run_dir, exist_ok=True)
        evidence_path = os.path.join(run_dir, "verification_evidence.json")
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2, ensure_ascii=False)
        # Tamper-evident receipt: seal the evidence with the run-scoped key.
        try:
            from .receipts import load_or_create_run_key, seal_artifact

            seal_artifact(evidence_path, load_or_create_run_key(run_dir))
        except OSError as seal_err:
            print(f"[verify] receipt seal failed: {seal_err}", file=sys.stderr)

    return all_passed, results, evidence_path
