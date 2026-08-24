"""
orchestrator/live_evolution_engine.py
Unified Sensory & Surgical Self-Evolution Engine.
"""

from __future__ import annotations
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.llm import call_llm
from orchestrator.patch_applier import PatchApplier, SearchReplacePatchParser
from orchestrator.surgical_extractor import SurgicalContextExtractor
from orchestrator.ast_splicer import ASTInvariantValidator
from orchestrator.elasticity_governor import DynamicElasticityGovernor
from orchestrator.codebase_introspector import (
    NormalizedExplorationEngine,
    ModuleProfile,
)
from orchestrator.fast_sandbox import ZeroCopyFastSandbox
from orchestrator.anti_ouroboros import AntiOuroborosGate
from orchestrator.micro_epoch import MicroEpochManager


class LiveEvolutionEngine:
    """Executes closed-loop self-evolution with precision surgical deltas and 5-pillar verification."""

    def __init__(self, workspace_root: Path, model_name: str = "cli:agy"):
        self.workspace_root = Path(workspace_root)
        self.model_name = model_name
        self.sandbox = ZeroCopyFastSandbox(self.workspace_root)
        self.epoch_mgr = MicroEpochManager(
            self.workspace_root / "scratch/evolution_state"
        )
        self.introspector = NormalizedExplorationEngine()

    def execute_live_optimization_cycle(
        self,
        module_path: str,
        optimization_goal: str,
        target_function: Optional[str] = None,
        test_file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        full_path = self.workspace_root / module_path
        if not full_path.exists():
            return {
                "is_success": False,
                "status": "MODULE_NOT_FOUND",
                "violations": [f"File {module_path} not found."],
            }

        existing_code = full_path.read_text(encoding="utf-8")

        # 1. Surgical Neighborhood Context Extraction
        extractor = SurgicalContextExtractor(self.workspace_root)
        if target_function:
            try:
                ctx = extractor.extract(module_path, target_function)
                context_snippet = ctx.target_node_source
                enclosing_class_context = (
                    f"\nEnclosing Class: {ctx.enclosing_class}\nAttributes: {ctx.class_attributes}"
                    if ctx.enclosing_class
                    else ""
                )
            except Exception:
                context_snippet = existing_code
                enclosing_class_context = ""
        else:
            context_snippet = existing_code
            enclosing_class_context = ""

        # 2. Dynamic Elasticity Allocation
        complexity_score = DynamicElasticityGovernor.calculate_complexity(
            context_snippet
        )
        budget = DynamicElasticityGovernor.allocate(complexity_score)

        # 3. Prompt Synthesis (Requesting Aider Search/Replace Delta)
        prompt = f"""You are the Lead Systems Engineer for LetItLoop.
Synthesize an optimized Python implementation for `{target_function or module_path}`.
Goal: {optimization_goal}
McCabe Complexity Target: <= 10

Surgical Context:
```python
{context_snippet}
```
{enclosing_class_context}

Return ONLY an exact Search/Replace block matching this schema:
<<<<<<< SEARCH
[exact lines to replace]
=======
[optimized replacement lines]
>>>>>>> REPLACE
"""

        # 4. LLM Generation & 3-Turn Repair Loop
        current_code = existing_code
        violations: List[str] = []

        for attempt in range(1, 4):
            try:
                raw_response = call_llm(
                    model=self.model_name,
                    prompt=prompt,
                    thinking_budget=budget.thinking_tokens,
                )
                patch_res = PatchApplier.apply_patch(
                    existing_code, raw_response, fuzzy_whitespace=True
                )
                if not patch_res.success:
                    violations = [
                        patch_res.error_message or "Search block not found."
                    ]
                    continue

                candidate_code = patch_res.modified_content

                # 5-Pillar Verification Firewall
                # 5a. AST Invariant Validation
                if target_function:
                    inv_res = ASTInvariantValidator.validate(
                        existing_code, candidate_code, target_function
                    )
                    if not inv_res.valid:
                        violations = inv_res.violations
                        continue

                # 5b. Anti-Ouroboros Gate
                ouro_res = AntiOuroborosGate.evaluate_mutation(
                    existing_code, candidate_code
                )
                if not ouro_res.is_approved:
                    violations = [
                        f"Anti-Ouroboros rejection: {ouro_res.reason}"
                    ]
                    continue

                # 5c. Zero-Copy Fast Sandbox (In-Memory Overlay)
                sandbox_res = self.sandbox.evaluate_in_memory_overlay(
                    target_relative_path=module_path,
                    candidate_code=candidate_code,
                    test_file_path=test_file_path,
                    timeout_sec=budget.timeout_sec,
                )
                if not sandbox_res.passed:
                    violations = [
                        sandbox_res.error_message
                        or "Sandbox evaluation failed."
                    ]
                    continue

                # Verified Fix -> Physical Disk Write-Back
                full_path.write_text(candidate_code, encoding="utf-8")
                diff_summary = f"Mutated {target_function or module_path} (Attempt {attempt}): {patch_res.chunks_applied} chunks applied."
                self.epoch_mgr.record_task_completion(
                    task_id=f"{module_path}:{target_function}",
                    diff_summary=diff_summary,
                )

                return {
                    "is_success": True,
                    "status": "VERIFIED_FIX",
                    "attempts_used": attempt,
                    "diff_summary": diff_summary,
                    "complexity_score": complexity_score,
                    "thinking_tokens_used": budget.thinking_tokens,
                }
            except Exception as e:
                violations = [str(e)]

        return {
            "is_success": False,
            "status": "ATTEMPTS_EXHAUSTED",
            "violations": violations,
        }
