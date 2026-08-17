"""Lens configurations for the quality plane.

Extracts hardcoded branching from qc_review.py into data-driven configs.
Phase 2: no behavioral change — output must be byte-identical for
architecture_audit and code_correctness lenses.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class LensDimension:
    """A single evaluation dimension for a QC lens."""

    def __init__(self, name: str, description: str, weight: float = 1.0):
        self.name = name
        self.description = description
        self.weight = weight


class LensConfig:
    """Configuration for a QC evaluation lens.

    Contains dimension templates, evaluation criteria sections,
    and minimum count defaults for adversarial-style audits.
    """

    def __init__(
        self,
        name: str,
        dimensions: List[LensDimension],
        criteria_sections: List[str],
        min_counts: Optional[Dict[str, int]] = None,
        strict_reject_rules: Optional[List[str]] = None,
    ):
        self.name = name
        self.dimensions = dimensions
        self.criteria_sections = criteria_sections
        self.min_counts = min_counts or {}
        self.strict_reject_rules = strict_reject_rules or []

    def dim_scores_json(self) -> str:
        """Render dimension_scores JSON template string."""
        parts = {d.name: "0.0-1.0" for d in self.dimensions}
        return json.dumps(parts)

    def dim_reasoning_json(self) -> str:
        """Render dimension_reasoning JSON template string."""
        parts = {d.name: "..." for d in self.dimensions}
        return json.dumps(parts)

    def render_evaluation_section(self, quality_spec: Optional[Dict[str, Any]] = None) -> str:
        """Render the full evaluation criteria section for prompt building."""
        lines: List[str] = []
        qs = quality_spec or {}

        if self.name == "architecture_audit":
            min_counts = qs.get("minimum_counts", {}) if qs else {}
            min_contradictions = min_counts.get("contradictions", 5)
            min_edge_cases = min_counts.get("edge_cases", 20)
            min_schemas = min_counts.get("schemas", 3)
            min_alternatives = min_counts.get("radical_alternatives", 1)

            lines.append("")
            lines.append("=== ADVERSARIAL ARCHITECTURE AUDIT EVALUATION ===")
            lines.append("This is an architecture audit. Apply STRICTER criteria:")
            lines.append("")
            for section in self.criteria_sections:
                filled = section.format(
                    min_contradictions=min_contradictions,
                    min_edge_cases=min_edge_cases,
                    min_schemas=min_schemas,
                    min_alternatives=min_alternatives,
                )
                lines.append(filled)
            lines.append("")
            for rule in self.strict_reject_rules:
                lines.append(rule)
            lines.append("Score each dimension 0.0-1.0 and include per-dimension reasoning.")
            lines.append("Hard failures from quality spec are non-negotiable — if present, REJECT.")
        else:
            lines.append("")
            lines.append("=== EVALUATION DIMENSIONS ===")
            lines.append(f"Evaluate the output across these {len(self.dimensions)} dimensions:")
            for i, dim in enumerate(self.dimensions, 1):
                lines.append(f"{i}. {dim.name.upper()}: {dim.description}")
            lines.append("")
            for section in self.criteria_sections:
                lines.append(section)

        return "\n".join(lines)


# ── Lens registry ──────────────────────────────────────────────────────────

LENSES: Dict[str, LensConfig] = {}


def register(lens: LensConfig) -> None:
    LENSES[lens.name] = lens


def get_lens(name: str) -> LensConfig:
    if name in LENSES:
        return LENSES[name]
    return LENSES["code_correctness"]


# ── Architecture audit lens ────────────────────────────────────────────────

register(
    LensConfig(
        name="architecture_audit",
        dimensions=[
            LensDimension("originality", "Does the output mostly restate the source material?"),
            LensDimension("contradiction_resolution", "Does it identify and resolve internal contradictions?"),
            LensDimension("concrete_artifacts", "Does it produce implementation-level artifacts?"),
            LensDimension("edge_case_coverage", "Are specific failure scenarios enumerated?"),
            LensDimension("intellectual_courage", "Does it challenge assumptions?"),
            LensDimension("actionability", "Are recommendations implementable?"),
            LensDimension("source_fidelity", "Does every claim cite the source?"),
        ],
        criteria_sections=[
            "1. ORIGINALITY: Does the output mostly restate the source material?\n   REJECT if it is a well-structured summary with no original analysis.",
            "2. CONTRADICTION RESOLUTION: Does it identify and resolve internal contradictions?\n   Target at least {min_contradictions} contradictions identified with concrete resolution proposals.",
            "3. CONCRETE ARTIFACTS: Does it produce implementation-level artifacts (JSON schemas,\n   risk tables, test plans, deployment checklists) that do not exist in the source?\n   Target at least {min_schemas} schemas or equivalent structured artifacts.",
            "4. EDGE CASES: Are specific failure scenarios enumerated (not just categories)?\n   Target at least {min_edge_cases} specific edge cases with per-scenario mitigations.",
            "5. INTELLECTUAL COURAGE: Does it challenge the system's assumptions and propose\n   uncomfortable truths? REJECT if it reads like a polite consulting deck.",
            "6. ACTIONABILITY: Are recommendations implementable — targeting specific\n   files/modules with concrete implementation shapes?",
            "7. SOURCE FIDELITY: Does every claim cite the specific source file and section?",
            "8. ALTERNATIVE ARCHITECTURES: Does it propose at least {min_alternatives} radical\n   alternative architecture that challenges the current design?",
        ],
        min_counts={
            "contradictions": 5,
            "edge_cases": 20,
            "schemas": 3,
            "radical_alternatives": 1,
        },
        strict_reject_rules=[
            "STRICT REJECT if output is primarily a summary with no original contribution.",
            "STRICT REJECT if it has no uncomfortable truths or radical alternatives.",
        ],
    )
)


# ── Code correctness lens (default) ────────────────────────────────────────

register(
    LensConfig(
        name="code_correctness",
        dimensions=[
            LensDimension(
                "contract_adherence",
                "Did the worker produce exactly the declared outputs?\n   Check output count, output paths, no undeclared files.",
            ),
            LensDimension(
                "structural_compliance",
                "Does the output contain the required sections,\n   headings, and structural elements from the quality spec?",
            ),
            LensDimension(
                "substantive_quality",
                "Source coverage, citation adequacy, factual correctness,\n   reasoning depth, and board usefulness.",
            ),
            LensDimension(
                "scope_discipline",
                "Did the worker stay within the contracted step?\n   (e.g., step 1 should not produce final audit output)",
            ),
        ],
        criteria_sections=[
            "Evaluate: Does the output FULLY satisfy the objective, quality spec, and all acceptance criteria?",
            "Be strict. If anything is missing, vague, incorrect, or out-of-scope, REJECT with specific reasons.",
            "If you cannot determine quality because evidence is insufficient, return INSUFFICIENT_EVIDENCE.",
        ],
    )
)


# ── Plan correctness lens ──────────────────────────────────────────────────

register(
    LensConfig(
        name="plan_correctness",
        dimensions=[
            LensDimension("goal_alignment", "Does the plan align with the goal?"),
            LensDimension("dependency_correctness", "Are task dependencies correct and complete?"),
            LensDimension("scope_appropriateness", "Is each contract scoped to a single responsibility?"),
            LensDimension("risk_assessment", "Are risk tiers correctly assigned?"),
        ],
        criteria_sections=[
            "Evaluate: Does the plan represent a valid, executable decomposition of the goal?",
            "Reject if any contract has unrealistic scope, missing dependencies, or inappropriate risk tier.",
        ],
    )
)


# ── Config safety lens ─────────────────────────────────────────────────────

register(
    LensConfig(
        name="config_safety",
        dimensions=[
            LensDimension("secret_exposure", "Does the config expose secrets or credentials?"),
            LensDimension("permission_correctness", "Are workspace permissions appropriate?"),
            LensDimension("destructive_actions", "Are there dangerous commands or destructive operations?"),
            LensDimension("validation_quality", "Are there sufficient safety validations?"),
        ],
        criteria_sections=[
            "Evaluate: Is this configuration safe to execute in the workspace?",
            "Any exposed credentials, unsafe permissions, or destructive commands are automatic REJECT.",
        ],
    )
)


# ── Research quality lens ──────────────────────────────────────────────────

register(
    LensConfig(
        name="research_quality",
        dimensions=[
            LensDimension("citation_quality", "Are all claims supported by citations?"),
            LensDimension("source_fidelity", "Do citations accurately represent source content?"),
            LensDimension("coverage", "Does the research cover the required scope?"),
            LensDimension("reasoning_depth", "Is there evidence of critical analysis vs summary?"),
        ],
        criteria_sections=[
            "Evaluate: Is the research thorough, accurate, and properly cited?",
            "Unsupported claims, citation hallucination, or superficial analysis should REJECT.",
        ],
    )
)


# ── Strategic review lens ──────────────────────────────────────────────────

register(
    LensConfig(
        name="strategic_review",
        dimensions=[
            LensDimension("problem_fit", "Does the work solve the actual problem?"),
            LensDimension("opportunity_cost", "Are there better approaches not considered?"),
            LensDimension("wrong_abstraction_risk", "Is the proposed abstraction level appropriate?"),
            LensDimension("simplicity", "Could a simpler approach work?"),
        ],
        criteria_sections=[
            "Evaluate: Is this the right thing to build, at the right level of abstraction?",
            "Challenge the goal, not just the implementation. Identify opportunity cost.",
            "If the objective is solving the wrong problem, say so directly.",
        ],
    )
)


# ── Migration safety lens ──────────────────────────────────────────────────

register(
    LensConfig(
        name="migration_safety",
        dimensions=[
            LensDimension("backward_compatibility", "Are there breaking changes without migration path?"),
            LensDimension("sequencing", "Is the migration order correct and safe?"),
            LensDimension("rollback_readiness", "Can the migration be rolled back?"),
            LensDimension("testing_coverage", "Are migration paths tested?"),
        ],
        criteria_sections=[
            "Evaluate: Is this migration safe to execute?",
            "Any breaking change without documented migration path is a REJECT.",
            "Missing rollback plan for high-risk migrations is a REJECT.",
        ],
    )
)


# ── Content quality lens ──────────────────────────────────────────────────

register(
    LensConfig(
        name="content_quality",
        dimensions=[
            LensDimension("accuracy", "Are factual claims correct and verifiable?"),
            LensDimension("completeness", "Does the content cover the contracted scope fully?"),
            LensDimension("structure", "Is the content organized for its intended use?"),
            LensDimension("tone_appropriateness", "Does the tone match the audience and purpose?"),
        ],
        criteria_sections=[
            "Evaluate: Is the content accurate, complete, and fit for purpose?",
            "Factual errors, unsupported claims, or material omissions should REJECT.",
            "If you cannot verify key claims against the provided files, return INSUFFICIENT_EVIDENCE.",
        ],
    )
)


# ── Document quality lens ─────────────────────────────────────────────────

register(
    LensConfig(
        name="document_quality",
        dimensions=[
            LensDimension("structure", "Are required sections and headings present?"),
            LensDimension("clarity", "Is the document understandable without prior context?"),
            LensDimension("consistency", "Are terms, formats, and references consistent?"),
            LensDimension("completeness", "Are all contracted sections substantively filled?"),
        ],
        criteria_sections=[
            "Evaluate: Is this document well-structured, clear, and complete?",
            "Missing required sections, placeholder content, or internal contradictions should REJECT.",
        ],
    )
)
