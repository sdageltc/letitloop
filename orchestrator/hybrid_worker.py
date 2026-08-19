"""Bounded hybrid worker: Implementer -> Critic -> Verifier loop.

Two modes:
- Profile mode (hybrid_profile set): deterministic topology test path.
- LLM mode (no hybrid_profile): real model calls with budget/parser/verifier.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .contract import check_path_allowed
from .llm import LLMError, call_llm
from .token_gate import TokenGateError, check_usage_authoritative, preflight

HYBRID_DEFAULT_MAX_TURNS = 3
HYBRID_ROLES = ("Implementer", "Critic", "Verifier")

# Paid-model routing guard: paid/premium models must NEVER be invoked with a
# bare prompt through the hybrid worker path — the guard lives in the
# orchestrator routing layer, not the transport.
PAID_MODELS = {
    "kimi-k2",
    "kimi-k3",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-3-opus",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.6-cyber",
    "o1",
    "o3",
    "o3-mini",
    "o4-mini",
    "deepseek-v4-pro",
    "deepseek-reasoner",
    "gemini-3.1-pro",
    "gemini-2.5-pro",
    "gemini-1.5-pro",
}

_MASTER_CONTEXT_MARKER = "MASTER_CONTEXT:"
_COMPLETE_MARKER = "[CONTEXT_COMPLETE]"
_MASTER_CONTEXT_MIN_CHARS = 12_000
_MAX_PAID_CONTEXT_CHARS = 200_000


def _guard_paid_model(model: str, prompt: str) -> None:
    """Fail closed when a paid route is given an unprepared prompt.

    Prefer an explicit completeness marker over a length heuristic — a
    truncated context can still be long. The length floor remains as a
    fallback so existing prepared payloads keep working. Even a MARKED prompt
    is capped — an enormous context still burns tokens.
    """
    # Canonicalize model name by matching known paid model substrings
    resolved_model = model.strip().lower()
    is_paid = any(paid_id in resolved_model for paid_id in PAID_MODELS)
    if not is_paid:
        return
    if len(prompt) > _MAX_PAID_CONTEXT_CHARS:
        raise RuntimeError(
            f"Paid model route '{resolved_model}' prompt exceeds hard cap "
            f"{_MAX_PAID_CONTEXT_CHARS} chars ({len(prompt)}); refusing."
        )
    has_marker = _MASTER_CONTEXT_MARKER in prompt or _COMPLETE_MARKER in prompt
    allow_legacy = os.environ.get("ORCH_ALLOW_PAID_LENGTH_FALLBACK") == "1"
    has_legacy_length = allow_legacy and len(prompt) >= _MASTER_CONTEXT_MIN_CHARS
    if not (has_marker or has_legacy_length):
        raise RuntimeError(
            f"Paid model route '{resolved_model}' requires a prepared master-context "
            f"payload ({_COMPLETE_MARKER} marker or {_MASTER_CONTEXT_MARKER} marker); refusing bare prompt."
        )


@dataclass
class HybridStep:
    turn: int
    role: str
    action: str
    status: str
    message: str
    parser_tier: str = ""
    budget: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "turn": self.turn,
            "role": self.role,
            "action": self.action,
            "status": self.status,
            "message": self.message,
        }
        if self.parser_tier:
            d["parser_tier"] = self.parser_tier
        if self.budget:
            d["budget"] = self.budget
        return d


def _output_path(workspace_root: str, path: str) -> str:
    return os.path.join(workspace_root, path) if not os.path.isabs(path) else path


def _write_outputs(contract, workspace_root: str, turn: int, repaired: bool = False) -> List[str]:
    artifact_paths: List[str] = []
    for out in contract.outputs:
        rel_path = out["path"]
        full_path = _output_path(workspace_root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        content = (
            f"HYBRID_OUTPUT\n"
            f"task_id={contract.task_id}\n"
            f"title={contract.title}\n"
            f"objective={contract.objective}\n"
            f"turn={turn}\n"
            f"repaired={str(repaired).lower()}\n"
        )
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        artifact_paths.append(full_path)
    return artifact_paths


def _write_llm_outputs(
    contract,
    workspace_root: str,
    artifacts: List[Any],
) -> List[str]:
    artifact_paths: List[str] = []
    for art in artifacts:
        rel_path = art.path
        full_path = _output_path(workspace_root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(art.content)
        artifact_paths.append(full_path)
    return artifact_paths


def _validate_outputs(contract, workspace_root: str) -> Tuple[bool, str]:
    allowed = contract.allowed_paths()
    denied = contract.denied_paths()
    for out in contract.outputs:
        path = out["path"]
        ok, err = check_path_allowed(path, allowed, denied, workspace_root)
        if not ok:
            return False, f"scope_violation: {err}"
        full_path = _output_path(workspace_root, path)
        if not os.path.isfile(full_path):
            return False, f"missing_output: {path}"
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                data = f.read().strip()
        except OSError as e:
            return False, f"read_error: {path}: {e}"
        if not data:
            return False, f"empty_output: {path}"
    return True, "ok"


def _write_trace(run_dir: str, trace: List[HybridStep]) -> str:
    path = os.path.join(run_dir, "hybrid_trace.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([step.to_dict() for step in trace], f, indent=2, ensure_ascii=False)
    return path


def _call_llm(
    role: str,
    model: str,
    prompt: str,
    workspace_root: str,
    timeout_sec: int,
) -> Dict[str, Any]:
    """Call an LLM via the generic transport and return structured result.

    Supports every provider prefix from ``orchestrator.llm`` (openai,
    anthropic, gemini, deepseek, any). The token gate runs pre-flight and
    post-hoc (authoritative usage check).

    Returns dict with ok, raw, stderr, exit_code, prompt_tokens, completion_tokens.
    """
    _guard_paid_model(model, prompt)
    estimated_prompt = max(1, len(prompt) // 4)

    # TOKEN GATE: pre-flight refusal before any bytes leave.
    try:
        preflight(len(prompt), None, caller=f"hybrid_worker:{model}")
    except TokenGateError as e:
        return {
            "ok": False,
            "raw": "",
            "stderr": str(e),
            "exit_code": 3,
            "prompt_tokens": estimated_prompt,
            "completion_tokens": 0,
        }

    try:
        from .models import ThinkingBudget

        tb = ThinkingBudget.budget_for(role)
        resp = call_llm(prompt, model, thinking_budget=tb, timeout_s=timeout_sec)
    except LLMError as e:
        from .worker_adapters import WorkerRegistry

        for cli_name in ("agy", "antigravity", "claude", "opencode", "hermes", "cline"):
            adapter = WorkerRegistry.get(cli_name)
            if adapter:
                try:
                    res = adapter.execute(prompt, workspace_root, f"hybrid_{role.lower()}", timeout=timeout_sec)
                    if res.get("exit_code") == 0 and res.get("stdout"):
                        return {
                            "ok": True,
                            "raw": res["stdout"],
                            "stderr": "",
                            "exit_code": 0,
                            "prompt_tokens": estimated_prompt,
                            "completion_tokens": max(1, len(res["stdout"]) // 4),
                        }
                except Exception:
                    continue
        return {
            "ok": False,
            "raw": "",
            "stderr": str(e),
            "exit_code": -1,
            "prompt_tokens": estimated_prompt,
            "completion_tokens": 0,
        }

    raw = resp["text"]
    # TOKEN GATE authoritative: the API reported actual usage over the cap.
    over_cap = check_usage_authoritative(
        resp.get("usage", {}) or {},
        estimated_prompt,
        caller=f"hybrid_worker:{model}",
        model=model,
    )
    if over_cap:
        return {
            "ok": False,
            "raw": raw,
            "stderr": (
                "TOKEN GATE HARD STOP: provider-reported usage over cap. See scratch/token_gate_violations.jsonl."
            ),
            "exit_code": 3,
            "prompt_tokens": estimated_prompt,
            "completion_tokens": max(1, len(raw) // 4),
        }

    return {
        "ok": True,
        "raw": raw,
        "stderr": "",
        "exit_code": 0,
        "prompt_tokens": estimated_prompt,
        "completion_tokens": max(1, len(raw) // 4),
    }


# ---------------------------------------------------------------
# Profile-based deterministic path (existing tests depend on this)
# ---------------------------------------------------------------


def _run_deterministic_loop(
    contract,
    workspace_root: str,
    run_dir: str,
    profile: str,
    max_turns: int,
    repair_budget: int,
    timeout_sec: int,
    brief_text: str,
) -> Dict[str, Any]:
    os.makedirs(run_dir, exist_ok=True)
    start = time.time()
    artifact_paths: List[str] = []
    trace: List[HybridStep] = []
    repaired = False
    final_status = "failure"
    stderr_parts: List[str] = []

    def _fail(exit_code: int, reason: str) -> Dict[str, Any]:
        trace_path = _write_trace(run_dir, trace)
        output_log_path = os.path.join(run_dir, "worker_output.log")
        with open(output_log_path, "w", encoding="utf-8") as f:
            f.write("HYBRID MODE (deterministic)\n")
            f.write(f"BRIEF\n{brief_text}\n\n")
            for step in trace:
                f.write(json.dumps(step.to_dict(), ensure_ascii=False) + "\n")
            f.write(f"FINAL: {reason}\n")
        elapsed = time.time() - start
        return {
            "success": False,
            "stdout": "",
            "stderr": reason,
            "exit_code": exit_code,
            "elapsed_sec": elapsed,
            "artifact_paths": artifact_paths + [trace_path, output_log_path],
            "hybrid_trace_path": trace_path,
            "turns": len(trace),
        }

    for turn in range(1, max_turns + 1):
        if profile == "budget_exhausted" and turn == max_turns:
            trace.append(HybridStep(turn, "Implementer", "write_outputs", "pass", "budget pressure mode"))
            return _fail(2, "hybrid budget exhausted before completion")

        if profile == "repair_then_success" and not repaired and turn == 1:
            artifact_paths = _write_outputs(contract, workspace_root, turn, repaired=False)
            if artifact_paths:
                with open(artifact_paths[0], "w", encoding="utf-8") as f:
                    f.write("")
            trace.append(HybridStep(turn, "Implementer", "write_outputs", "pass", "initial artifact produced"))
        else:
            artifact_paths = _write_outputs(contract, workspace_root, turn, repaired=repaired)
            trace.append(HybridStep(turn, "Implementer", "write_outputs", "pass", "artifact set written"))

        ok, reason = _validate_outputs(contract, workspace_root)
        if profile == "repair_then_success" and not repaired and turn == 1:
            ok = False
            reason = "missing_output: forced first-pass repair path"

        if not ok:
            trace.append(HybridStep(turn, "Critic", "validate_outputs", "fail", reason))
            stderr_parts.append(reason)
            if repair_budget <= 0:
                return _fail(3, "hybrid repair budget exhausted")
            repair_budget -= 1
            repaired = True
            trace.append(HybridStep(turn, "Verifier", "repair_outputs", "pass", "bounded repair applied"))
            artifact_paths = _write_outputs(contract, workspace_root, turn, repaired=True)
            continue

        trace.append(HybridStep(turn, "Critic", "validate_outputs", "pass", "artifact set accepted"))
        verifier_ok, verifier_reason = _validate_outputs(contract, workspace_root)
        trace.append(
            HybridStep(turn, "Verifier", "check_artifacts", "pass" if verifier_ok else "fail", verifier_reason)
        )
        if verifier_ok:
            final_status = "success"
            break

    if final_status != "success":
        return _fail(2, "hybrid turn budget exhausted")

    trace_path = _write_trace(run_dir, trace)
    output_log_path = os.path.join(run_dir, "worker_output.log")
    with open(output_log_path, "w", encoding="utf-8") as f:
        f.write("HYBRID MODE (deterministic)\n")
        f.write(f"BRIEF\n{brief_text}\n\n")
        for step in trace:
            f.write(json.dumps(step.to_dict(), ensure_ascii=False) + "\n")
        f.write("FINAL: success\n")
    elapsed = time.time() - start
    return {
        "success": True,
        "stdout": "hybrid worker completed",
        "stderr": "\n".join(stderr_parts),
        "exit_code": 0,
        "elapsed_sec": elapsed,
        "artifact_paths": artifact_paths + [trace_path, output_log_path],
        "hybrid_trace_path": trace_path,
        "turns": len(trace),
    }


# ---------------------------------------------------------------
# Real LLM loop path (production)
# ---------------------------------------------------------------


def _run_llm_loop(
    contract,
    workspace_root: str,
    run_dir: str,
    inner_model: str,
    max_turns: int,
    repair_budget: int,
    timeout_sec: int,
    brief_text: str,
    supervisor_attempt: int = 1,
) -> Dict[str, Any]:
    from .budget import BudgetExhaustedError, BudgetGuard, LoopDetector, UsageLedger
    from .parsing import parse_llm_artifacts
    from .prompts import build_critic_prompt, build_implementer_prompt, summarize_acceptance, summarize_verifier_results
    from .verifier import run_checks

    os.makedirs(run_dir, exist_ok=True)
    start = time.time()

    max_tokens = int(contract.worker.get("hybrid_max_tokens", 100_000))
    max_cost_usd = float(contract.worker.get("hybrid_max_cost_usd", 0.50))
    critic_model = contract.worker.get("hybrid_critic_model", inner_model)
    if critic_model.startswith("hybrid:"):
        critic_model = critic_model[7:]

    ledger = UsageLedger()
    budget_guard = BudgetGuard(max_tokens=max_tokens, max_cost_usd=max_cost_usd, ledger=ledger)
    loop_detector = LoopDetector(
        max_identical_outputs=int(contract.worker.get("hybrid_max_identical_outputs", 2)),
        max_identical_failures=int(contract.worker.get("hybrid_max_identical_failures", 2)),
        max_identical_verdicts=int(contract.worker.get("hybrid_max_identical_verdicts", 3)),
    )
    acceptance_summary = summarize_acceptance(contract.acceptance_checks)
    expected_paths = [out["path"] for out in contract.outputs]

    artifact_paths: List[str] = []
    trace: List[HybridStep] = []
    final_status = "failure"
    stderr_parts: List[str] = []
    prior_failures: List[Dict[str, Any]] = []
    critic_feedback: Optional[str] = None
    verifier_feedback: Optional[str] = None
    parse_failure_count: int = 0
    max_parse_failures: int = 2

    def _budget_snapshot() -> Dict[str, Any]:
        return {
            "total_tokens": ledger.total_tokens,
            "total_cost_usd": round(ledger.total_cost_usd, 6),
            "calls": ledger.call_count,
        }

    def _fail(exit_code: int, reason: str) -> Dict[str, Any]:
        trace_path = _write_trace(run_dir, trace)
        output_log_path = os.path.join(run_dir, "worker_output.log")
        with open(output_log_path, "w", encoding="utf-8") as f:
            f.write("HYBRID MODE (llm)\n")
            f.write(f"BRIEF\n{brief_text}\n\n")
            for step in trace:
                f.write(json.dumps(step.to_dict(), ensure_ascii=False) + "\n")
            f.write(f"FINAL: {reason}\n")
        elapsed = time.time() - start
        return {
            "success": False,
            "stdout": "",
            "stderr": reason,
            "exit_code": exit_code,
            "elapsed_sec": elapsed,
            "artifact_paths": artifact_paths + [trace_path, output_log_path],
            "hybrid_trace_path": trace_path,
            "turns": len(trace),
        }

    for turn in range(1, max_turns + 1):
        # Budget pre-flight check
        try:
            budget_guard.check_before_call(estimated_prompt_tokens=5000, estimated_completion_tokens=2000)
        except BudgetExhaustedError as e:
            trace.append(HybridStep(turn, "BudgetGuard", "preflight", "fail", str(e), budget=_budget_snapshot()))
            return _fail(5, f"budget exhausted: {e}")

        # --- Implementer ---
        quality_spec = getattr(contract, "quality_spec", {})
        implementer_prompt = build_implementer_prompt(
            title=contract.title,
            objective=contract.objective,
            output_paths=expected_paths,
            acceptance_summary=acceptance_summary,
            prior_failures=prior_failures if prior_failures else None,
            critic_feedback=critic_feedback,
            verifier_feedback=verifier_feedback,
            max_turns=max_turns,
            current_turn=turn,
            quality_spec=quality_spec,
            supervisor_attempt=supervisor_attempt,
            strategy_fingerprint=str(contract.worker.get("_strategy_fingerprint", "") or ""),
            prior_fingerprint=str(contract.worker.get("_prior_fingerprint", "") or ""),
        )

        llm_result = _call_llm("Implementer", inner_model, implementer_prompt, workspace_root, timeout_sec)
        ledger.record("Implementer", inner_model, llm_result["prompt_tokens"], llm_result["completion_tokens"])

        if not llm_result["ok"]:
            msg = f"LLM call failed (exit={llm_result['exit_code']}): {llm_result['stderr'][:200]}"
            trace.append(HybridStep(turn, "Implementer", "llm_call", "fail", msg, budget=_budget_snapshot()))
            stderr_parts.append(msg)
            prior_failures.append({"message": msg})
            if loop_detector.record_failure(msg):
                return _fail(6, f"stuck loop: {msg}")
            continue

        trace.append(
            HybridStep(
                turn, "Implementer", "llm_call", "pass", "implementer produced output", budget=_budget_snapshot()
            )
        )

        # --- Parse ---
        parse_result = parse_llm_artifacts(llm_result["raw"], expected_paths)
        if not parse_result.ok:
            parse_failure_count += 1
            msg = f"parse failed: {parse_result.error}"
            trace.append(
                HybridStep(
                    turn, "Implementer", "parse_output", "fail", msg, parser_tier="none", budget=_budget_snapshot()
                )
            )
            stderr_parts.append(msg)
            prior_failures.append({"message": msg})
            if parse_failure_count >= max_parse_failures:
                return _fail(7, f"parse failure budget exhausted: {msg}")
            if loop_detector.record_failure(f"parse:{parse_result.error}"):
                return _fail(6, "stuck loop: repeated parse failure")
            continue

        parser_tier = parse_result.artifacts[0].parser_tier if parse_result.artifacts else "?"
        trace.append(
            HybridStep(
                turn,
                "Parser",
                "parse_output",
                "pass",
                f"parsed via {parser_tier}",
                parser_tier=parser_tier,
                budget=_budget_snapshot(),
            )
        )

        # --- Write artifacts ---
        current_artifacts = _write_llm_outputs(contract, workspace_root, parse_result.artifacts)
        for ap in current_artifacts:
            if ap not in artifact_paths:
                artifact_paths.append(ap)

        # --- Scope validation ---
        scope_ok, scope_reason = _validate_outputs(contract, workspace_root)
        if not scope_ok:
            msg = f"scope violation: {scope_reason}"
            trace.append(
                HybridStep(
                    turn, "Critic", "validate_outputs", "fail", msg, parser_tier=parser_tier, budget=_budget_snapshot()
                )
            )
            return _fail(4, msg)

        # --- Loop detection ---
        content_hashes = [a.content for a in parse_result.artifacts]
        stuck = loop_detector.record_outputs(content_hashes)
        if stuck:
            msg = f"stuck loop: {stuck}"
            trace.append(
                HybridStep(
                    turn, "LoopDetector", "check", "fail", msg, parser_tier=parser_tier, budget=_budget_snapshot()
                )
            )
            return _fail(6, msg)

        # --- Deterministic precheck ---
        precheck_results = run_checks(contract.acceptance_checks, workspace_root)
        precheck_passed = all(r.passed for r in precheck_results)
        if not precheck_passed:
            verifier_results_dict = [r.to_dict() for r in precheck_results]
            verifier_feedback = summarize_verifier_results(verifier_results_dict)
            msg = "deterministic precheck failed"
            trace.append(
                HybridStep(
                    turn, "Verifier", "precheck", "fail", msg, parser_tier=parser_tier, budget=_budget_snapshot()
                )
            )
            stderr_parts.append(msg)
            prior_failures.append({"message": msg, "verifier": verifier_results_dict})
            if loop_detector.record_failure(f"precheck:{msg}"):
                return _fail(6, "stuck loop: repeated precheck failure")
            continue

        trace.append(
            HybridStep(
                turn,
                "Verifier",
                "precheck",
                "pass",
                "deterministic checks passed",
                parser_tier=parser_tier,
                budget=_budget_snapshot(),
            )
        )

        # --- Critic ---
        artifact_summaries = [{"path": a.path, "content": a.content} for a in parse_result.artifacts]
        verifier_results_dict = [r.to_dict() for r in precheck_results]

        quality_spec = getattr(contract, "quality_spec", {})
        critic_prompt = build_critic_prompt(
            title=contract.title,
            objective=contract.objective,
            output_paths=expected_paths,
            acceptance_summary=acceptance_summary,
            artifact_summaries=artifact_summaries,
            verifier_results=verifier_results_dict,
            quality_spec=quality_spec,
        )

        critic_result = _call_llm("Critic", critic_model, critic_prompt, workspace_root, timeout_sec)
        ledger.record("Critic", critic_model, critic_result["prompt_tokens"], critic_result["completion_tokens"])

        critic_verdict = ""
        if critic_result["ok"]:
            try:
                parsed = json.loads(critic_result["raw"])
                if isinstance(parsed, dict):
                    critic_verdict = parsed.get("status", "FAIL")
                    critic_feedback = parsed.get("implementer_guidance", "no guidance provided")
                else:
                    critic_verdict = "FAIL"
                    critic_feedback = "critic output was not a JSON object"
            except (json.JSONDecodeError, ValueError):
                critic_verdict = "FAIL"
                critic_feedback = "critic output was not valid JSON"
        else:
            critic_verdict = "FAIL"
            critic_feedback = critic_result["stderr"][:300] or "critic LLM call failed"

        stuck_verdict = loop_detector.record_critic_verdict(critic_verdict)
        if stuck_verdict:
            trace.append(
                HybridStep(
                    turn,
                    "Critic",
                    "evaluate",
                    "fail",
                    stuck_verdict,
                    parser_tier=parser_tier,
                    budget=_budget_snapshot(),
                )
            )
            return _fail(6, stuck_verdict)

        if critic_verdict == "PASS":
            trace.append(
                HybridStep(
                    turn,
                    "Critic",
                    "evaluate",
                    "pass",
                    "critic approved",
                    parser_tier=parser_tier,
                    budget=_budget_snapshot(),
                )
            )
            final_status = "success"
            break
        else:
            trace.append(
                HybridStep(
                    turn,
                    "Critic",
                    "evaluate",
                    "fail",
                    critic_feedback[:200],
                    parser_tier=parser_tier,
                    budget=_budget_snapshot(),
                )
            )
            stderr_parts.append(f"critic: {critic_feedback[:200]}")
            prior_failures.append({"message": f"critic: {critic_feedback[:200]}"})
            if repair_budget <= 0:
                return _fail(3, "hybrid repair budget exhausted")
            repair_budget -= 1
            continue

    if final_status != "success":
        return _fail(2, "hybrid turn budget exhausted")

    # --- Final verification (full) ---
    from .verifier import run_verification

    final_passed, final_results, _ = run_verification(contract, workspace_root, run_dir)
    if not final_passed:
        msg = "final verification failed"
        trace.append(
            HybridStep(
                turn=turn, role="Verifier", action="final_check", status="fail", message=msg, budget=_budget_snapshot()
            )
        )
        return _fail(1, msg)

    trace_path = _write_trace(run_dir, trace)
    output_log_path = os.path.join(run_dir, "worker_output.log")
    with open(output_log_path, "w", encoding="utf-8") as f:
        f.write("HYBRID MODE (llm)\n")
        f.write(f"BRIEF\n{brief_text}\n\n")
        for step in trace:
            f.write(json.dumps(step.to_dict(), ensure_ascii=False) + "\n")
        f.write("FINAL: success\n")
    elapsed = time.time() - start
    return {
        "success": True,
        "stdout": "hybrid worker completed",
        "stderr": "\n".join(stderr_parts),
        "exit_code": 0,
        "elapsed_sec": elapsed,
        "artifact_paths": artifact_paths + [trace_path, output_log_path],
        "hybrid_trace_path": trace_path,
        "turns": len(trace),
    }


# ---------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------


def run_hybrid_worker(
    contract,
    workspace_root: str,
    run_dir: str,
    previous_failures=None,
    changed_approach=None,
    timeout_sec: int = 300,
    brief_text: str = "",
    supervisor_attempt: int = 1,
) -> Dict[str, Any]:
    """Run the bounded hybrid worker loop.

    Two modes:
    - If contract.worker has 'hybrid_profile': deterministic profile path
    - Otherwise: real LLM loop with budget/parser/verifier
    """
    os.makedirs(run_dir, exist_ok=True)
    max_turns = int(contract.worker.get("hybrid_max_turns", HYBRID_DEFAULT_MAX_TURNS))
    repair_budget = int(contract.worker.get("hybrid_repair_budget", 1))

    if "hybrid_profile" in contract.worker:
        profile = str(contract.worker["hybrid_profile"])
        return _run_deterministic_loop(
            contract,
            workspace_root,
            run_dir,
            profile=profile,
            max_turns=max_turns,
            repair_budget=repair_budget,
            timeout_sec=timeout_sec,
            brief_text=brief_text,
        )

    inner_model_raw = contract.worker.get("model", "")
    inner_model = inner_model_raw.replace("hybrid:", "", 1)

    return _run_llm_loop(
        contract,
        workspace_root,
        run_dir,
        inner_model=inner_model,
        max_turns=max_turns,
        repair_budget=repair_budget,
        timeout_sec=timeout_sec,
        brief_text=brief_text,
        supervisor_attempt=supervisor_attempt,
    )
