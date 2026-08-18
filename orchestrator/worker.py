"""Worker adapter: invokes a generic LLM with contract-derived brief."""

import json
import os
import sys
import time

from .llm import LLMError, call_llm
from .models import ModelRegistry
from .token_gate import TokenGateError, preflight


def _safe_stderr(msg):
    try:
        print(msg, file=sys.stderr, flush=True)
    except OSError:
        pass


DEFAULT_MODEL = ModelRegistry.FALLBACK

MAX_OUTPUT_SIZE = 512 * 1024  # 512KB cap on captured output


def _build_brief(contract, previous_failures=None, changed_approach=None):
    """Build a zero-context bounded brief from contract and optional failure context.

    Returns a string ready to pass to the model as the worker prompt.
    """
    allow_paths = "; ".join(sorted(contract.allowed_paths()))
    deny_paths = "; ".join(sorted(contract.denied_paths()))
    scratch_dir = contract.workspace_scope.get("scratch_dir", "")

    lines = [
        f"Task: {contract.title}",
        f"Objective: {contract.objective}",
        "",
        "Allowed paths (write scope):",
        f"  {allow_paths}" if allow_paths else "  (none)",
        "",
        "Forbidden paths:",
        f"  {deny_paths}" if deny_paths else "  (none)",
        "",
    ]
    if scratch_dir:
        lines.extend(
            [
                "Scratch/temp directory (write helpers here, not in production paths):",
                f"  {scratch_dir}",
                "",
            ]
        )
    lines.append("Required outputs:")
    for out in contract.outputs:
        lines.append(f"  - {out['path']}")
    lines.append("")

    quality_spec = getattr(contract, "quality_spec", {})
    if quality_spec:
        lines.append("QUALITY SPECIFICATION (you will be judged on these):")
        if quality_spec.get("required_sections"):
            lines.append(f"  Required sections (exact spelling): {quality_spec['required_sections']}")
        if quality_spec.get("quality_dimensions"):
            lines.append(f"  Quality dimensions: {json.dumps(quality_spec['quality_dimensions'])}")
        if quality_spec.get("hard_failures"):
            lines.append(f"  Hard failures (must avoid): {quality_spec['hard_failures']}")
        if quality_spec.get("minimum_score"):
            lines.append(f"  Minimum quality score: {quality_spec['minimum_score']}")
        if quality_spec.get("minimum_counts"):
            lines.append(f"  Minimum content counts: {json.dumps(quality_spec['minimum_counts'])}")
        lines.append("  Every claim must include specific source file citations (file name + section).")
        lines.append("")

    quality_profile = contract.worker.get("quality_profile", "")
    if quality_profile == "adversarial_architecture_audit":
        lines.append("ADVERSARIAL ARCHITECTURE AUDIT MODE:")
        lines.append("- You are a SENIOR SYSTEMS ARCHITECT. Do NOT write a consulting-style summary.")
        lines.append(
            "- Your job is to CHALLENGE the system. Find contradictions, hidden assumptions, and over-engineering."
        )
        lines.append("- Disagree with the source where it is wrong, overstated, or internally inconsistent.")
        lines.append(
            "- Generate concrete implementation artifacts not present in the source (JSON schemas, risk tables, test plans)."
        )
        lines.append(
            "- Enumerate specific edge case scenarios with mitigation strategies — not just categories, actual scenarios."
        )
        lines.append(
            "- Propose at least one radically simpler alternative architecture that challenges the current design."
        )
        lines.append(
            "- Produce implementable recommendations: each must target a specific file/module with an implementation shape."
        )
        lines.append("- Include a 90-day hardening plan with clear definitions of done per phase.")
        lines.append("- If the source claims something the reviewer does not have evidence for, say so directly.")
        lines.append("- 'Uncomfortable Truths' is a required section — write it honestly, do not soften the analysis.")
        lines.append("")

    lines.append("Acceptance criteria:")
    for check in contract.acceptance_checks:
        lines.append(f"  - [{check['kind']}] {check.get('command', check.get('path', check['id']))}")
        if "expected" in check:
            lines.append(f"    expected: {check['expected']}")
    lines.append("")
    lines.append("CONSTRAINTS:")
    lines.append("- Only modify files under the allowed paths above.")
    lines.append("- Do NOT modify AGENTS.md, memory/, .opencode/, or global configs.")
    lines.append("- Do NOT run git commands, deploy, publish, or access external APIs.")
    lines.append("- Do NOT print secrets or credentials.")
    lines.append("- Write all outputs to the paths listed above.")
    lines.append("- Do NOT claim completion without producing all output files.")
    lines.append("- Do NOT create any files beyond the Required outputs listed above.")
    lines.append("- Do NOT produce self-verification, self-review, or self-assessment reports.")

    quality_profile = contract.worker.get("quality_profile", "")
    if quality_profile == "adversarial_architecture_audit":
        input_paths = getattr(contract, "inputs", [])
        if input_paths:
            lines.append("")
            lines.append("INPUT FILES TO ANALYZE (read these via your MCP tools):")
            for inp in input_paths:
                if isinstance(inp, dict):
                    path = inp.get("path", "")
                else:
                    path = str(inp)
                if path:
                    full = os.path.abspath(path) if not os.path.isabs(path) else path
                    if os.path.isfile(full):
                        try:
                            with open(full, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                            lines.append(f"  {path} ({len(content)} bytes)")
                            lines.append("  First 3000 chars preview:")
                            lines.append(content[:3000])
                            if len(content) > 3000:
                                lines.append("  [... truncated, read the full file with MCP tools ...]")
                        except OSError as e:
                            lines.append(f"  {path} (read error: {e})")

    if previous_failures:
        lines.append("")
        lines.append("PREVIOUS ATTEMPT FAILED.")
        lines.append("Failure evidence:")
        for fail in previous_failures:
            lines.append(f"  - {fail.get('message', '')}")
    if changed_approach:
        lines.append("")
        lines.append("REQUIRED: You MUST use a structurally different approach.")
        lines.append(f"Changed approach required: {changed_approach}")
        lines.append("The previous approach is not acceptable. Do something different.")

    return "\n".join(lines)


def _cap_output(text, max_size=MAX_OUTPUT_SIZE):
    if text and len(text) > max_size:
        head_size = max_size // 2
        tail_size = max_size - head_size
        truncated_count = len(text) - (head_size + tail_size)
        return text[:head_size] + f"\n... [{truncated_count} chars truncated] ...\n" + text[-tail_size:]
    return text or ""


def _strip_code_fences(text):
    """Strip a single markdown code fence block around an artifact."""
    text = (text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _materialize_outputs(contract, workspace_root, stdout):
    """Persist the worker's text response into the contract's declared output
    paths so chat-completion workers can produce artifacts like CLI workers."""
    written = []
    for out in contract.outputs:
        out_path = out["path"]
        full_path = os.path.join(workspace_root, out_path) if not os.path.isabs(out_path) else out_path
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(_strip_code_fences(stdout))
            written.append(full_path)
        except OSError as e:
            _safe_stderr(f"[worker] could not materialize output {out_path}: {e}")
    return written


def _run_llm_worker(
    contract, workspace_root, run_dir, model, previous_failures=None, changed_approach=None, timeout_sec=900
):
    """Invoke the generic LLM transport with a contract-derived brief."""
    task_id = contract.task_id
    _safe_stderr(f"[worker] task={task_id} starting (model={model})")

    brief = _build_brief(contract, previous_failures, changed_approach)

    os.makedirs(run_dir, exist_ok=True)
    brief_path = os.path.join(run_dir, "worker_brief.txt")
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(brief)

    start = time.time()
    try:
        preflight(len(brief), None, caller=f"worker:{task_id}")
        response = call_llm(brief, model, timeout_s=timeout_sec)
        stdout = _cap_output(response["text"])
        stderr = ""
        exit_code = 0
    except TokenGateError as e:
        stdout = ""
        stderr = str(e)
        exit_code = 1
    except LLMError as e:
        elapsed = time.time() - start
        _safe_stderr(f"[worker] task={task_id} model error: {e}")
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "elapsed_sec": elapsed,
            "artifact_paths": [brief_path],
        }
    except Exception as e:
        elapsed = time.time() - start
        _safe_stderr(f"[worker] task={task_id} error: {e}")
        return {
            "success": False,
            "stdout": "",
            "stderr": f"worker invocation error: {e}",
            "exit_code": -1,
            "elapsed_sec": elapsed,
            "artifact_paths": [brief_path],
        }

    elapsed = time.time() - start

    materialized = []
    if exit_code == 0 and contract.outputs:
        materialized = _materialize_outputs(contract, workspace_root, stdout)

    output_log_path = os.path.join(run_dir, "worker_output.log")
    with open(output_log_path, "w", encoding="utf-8") as f:
        f.write(f"EXIT CODE: {exit_code}\n")
        f.write(f"ELAPSED: {elapsed:.2f}s\n")
        if materialized:
            f.write("MATERIALIZED OUTPUTS:\n")
            for m in materialized:
                f.write(f"  {m}\n")
        f.write("--- STDOUT ---\n")
        f.write(stdout)
        f.write("\n--- STDERR ---\n")
        f.write(stderr)
        f.write("\n")

    _safe_stderr(f"[worker] task={task_id} finished (exit={exit_code}, elapsed={elapsed:.1f}s)")

    return {
        "success": exit_code == 0,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "elapsed_sec": elapsed,
        "artifact_paths": [brief_path, output_log_path] + materialized,
    }


def _fake_worker(contract, workspace_root, run_dir, previous_failures=None):
    task_id = contract.task_id
    _safe_stderr(f"[worker] task={task_id} starting (model=fake)")
    fake_mode = os.environ.get("FAKE_WORKER", "")
    content = "FAKE_WORKER_OUTPUT"
    success = True
    exit_code = 0

    if fake_mode == "FAIL":
        content = ""
        success = False
        exit_code = 1
    elif fake_mode == "RETRY":
        if previous_failures:
            content = "FAKE_WORKER_OUTPUT"
            success = True
            exit_code = 0
        else:
            content = ""
            success = False
            exit_code = 1

    os.makedirs(run_dir, exist_ok=True)
    artifact_paths = []
    for out in contract.outputs:
        out_path = out["path"]
        full_path = os.path.join(workspace_root, out_path) if not os.path.isabs(out_path) else out_path
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        artifact_paths.append(full_path)

    _safe_stderr(f"[worker] task={task_id} finished (exit={exit_code}, elapsed=0.01s)")
    return {
        "success": success,
        "stdout": "fake worker output",
        "stderr": "",
        "exit_code": exit_code,
        "elapsed_sec": 0.01,
        "artifact_paths": artifact_paths,
    }


def _dispatch_worker(
    contract, workspace_root, run_dir, model, previous_failures=None, changed_approach=None, timeout_sec=300
):
    """Run one worker invocation with the given model string (no fallback)."""
    return _run_llm_worker(contract, workspace_root, run_dir, model, previous_failures, changed_approach, timeout_sec)


def _default_backup_model(model):
    """Default provider backup chain: primary <-> fallback, hybrid opt-out."""
    if model.startswith("hybrid:"):
        return None
    if model == ModelRegistry.WORKER_PREFIXED:
        return ModelRegistry.WORKER_FALLBACK
    if model == ModelRegistry.QC_PREFIXED:
        return ModelRegistry.QC_FALLBACK
    if model == ModelRegistry.WORKER_FALLBACK:
        return ModelRegistry.WORKER_PREFIXED
    if model == ModelRegistry.FALLBACK:
        return ModelRegistry.WORKER_PREFIXED
    return ModelRegistry.WORKER_FALLBACK


def _merge_fallback_log(run_dir, primary_model, primary_result, backup_model, backup_result):
    """Append a provider-fallback section to worker_output.log."""
    try:
        log_path = os.path.join(run_dir, "worker_output.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n=== PROVIDER FALLBACK ===\n")
            f.write(f"PRIMARY: {primary_model} exit={primary_result.get('exit_code')}\n")
            f.write(f"BACKUP:  {backup_model} exit={backup_result.get('exit_code')}\n")
    except OSError:
        pass


def run_worker(
    contract,
    workspace_root,
    run_dir,
    previous_failures=None,
    changed_approach=None,
    timeout_sec=300,
    supervisor_attempt=1,
):
    """Invoke a worker with provider fallback.

    Primary provider comes from contract.worker.model (or WORKER_MODEL env).
    On invocation failure (non-zero exit, transport error, timeout) the same
    brief is retried on a backup provider (default FALLBACK). The chain is
    overridable per contract via contract.worker.fallback_model and disableable
    via WORKER_NO_FALLBACK=1.

    Returns dict with keys:
        success: bool
        stdout: str
        stderr: str
        exit_code: int
        elapsed_sec: float
        artifact_paths: list[str]
        provider: str  (model that produced the result)
        fallback_used / fallback_from / fallback_to (only when fallback fired)
    """
    if os.environ.get("FAKE_WORKER", "") in ("1", "FAIL", "RETRY"):
        return _fake_worker(contract, workspace_root, run_dir, previous_failures)

    model = contract.worker.get("model") or ModelRegistry.default_worker()
    task_id = contract.task_id

    if model.startswith("hybrid:"):
        from .hybrid_worker import run_hybrid_worker

        return run_hybrid_worker(
            contract,
            workspace_root,
            run_dir,
            previous_failures=previous_failures,
            changed_approach=changed_approach,
            timeout_sec=timeout_sec,
            supervisor_attempt=supervisor_attempt,
        )

    result = _dispatch_worker(
        contract, workspace_root, run_dir, model, previous_failures, changed_approach, timeout_sec
    )
    result["provider"] = model

    if result.get("exit_code", -1) == 0:
        return result

    if os.environ.get("WORKER_NO_FALLBACK"):
        return result

    backup = contract.worker.get("fallback_model") or _default_backup_model(model)
    if not backup or backup == model:
        return result

    _safe_stderr(
        f"[worker] task={task_id} primary {model} failed (exit={result.get('exit_code')}); falling back to {backup}"
    )
    backup_result = _dispatch_worker(
        contract, workspace_root, run_dir, backup, previous_failures, changed_approach, timeout_sec
    )
    _merge_fallback_log(run_dir, model, result, backup, backup_result)
    backup_result["provider"] = backup
    backup_result["fallback_used"] = True
    backup_result["fallback_from"] = model
    backup_result["fallback_to"] = backup
    backup_result["primary_stderr"] = result.get("stderr", "")
    backup_result["primary_stdout"] = result.get("stdout", "")
    return backup_result
