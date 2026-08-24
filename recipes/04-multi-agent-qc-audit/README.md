# Recipe 04 - Multi-Agent QC Audit

Configure the multi-lens quality plane when deterministic checks are not
enough: a panel of reviewer personas evaluates the artifact, disagreements go
to arbitration, and hard quality floors gate sign-off.

## The Lens Registry

`qc.lens` must be one of the exact strings from
`orchestrator/quality_plan.py` (`VALID_LENSES`):

| Lens | Reviews for |
|------|-------------|
| `code_correctness` | Implementation correctness, testability |
| `plan_correctness` | Whether the plan itself is sound and operable |
| `config_safety` | Destructive actions, secrets, permissions, unsafe commands |
| `content_quality` | Substance of generated content |
| `document_quality` | Structure and fidelity of documents |
| `architecture_audit` | Contradictions, overengineering, failure modes |
| `research_quality` | Citation quality, unsupported claims |
| `strategic_review` | Whether the work solves the actual problem |
| `migration_safety` | Backward compatibility, sequencing, rollback |

## Panel Modes

When no explicit `quality_plan` is provided, the engine derives one from
`risk_tier` + lens:

- `auto` tier: single reviewer by default; `architecture_audit`,
  `strategic_review`, and `migration_safety` lenses escalate to a panel.
- `qc_required` tier: single reviewer by default (deterministic gates carry
  most of the load).
- `human_required` tier: panel by default - humans asked for extra scrutiny.

A panel runs several reviewer personas (e.g. architecture_audit uses
`systems_architect`, `minimalist`, `product_owner`), then synthesizes their
findings. Synthesis preserves dissent rather than averaging it away, and any
P0 finding rejects the output outright.

## Arbitration And QC Overrule

- **Arbitration**: enabled automatically for the `human_required` tier and for
  strategic/architecture lenses. It triggers on `p0_disagreement` or
  `low_confidence` between reviewers; a designated arbiter model casts the
  deciding vote.
- **QC overrule**: the supervisor's QC step can overrule a worker's claimed
  success - a verifier pass plus a failed quality review means the task does
  not ship as complete. Conversely, recorded QC decisions feed retry strategy,
  so an overrule is actionable, not just a verdict.

## Budgets And Graceful Degradation

Every plan carries a budget: `max_llm_calls` (default 8), `max_wall_clock_sec`
(900), reviewer/component caps. If the estimated call count exceeds the
budget, the plan degrades along a fixed ladder - drop arbitration first, then
trim reviewers, then fall back from component-panel to panel to single review -
recording what it dropped and why. You always know how much scrutiny you
actually got.

## quality_spec Fields

`quality_spec` is required whenever `qc.required` is true and outputs are not
scratch files. Recognized fields:

| Field | Type | Meaning |
|-------|------|---------|
| `required_sections` | list | Headers/keys that must appear in the artifact |
| `hard_failures` | list | Findings that reject the artifact regardless of score |
| `minimum_score` | float 0..1 | Overall quality floor |
| `minimum_counts` | dict | Minimum occurrence counts (e.g. audits per section) |
| `quality_dimensions` | dict | Per-dimension scoring weights |

## The Worked Example

[`qc-goal.json`](qc-goal.json) audits a payment-service design doc:

- `risk_tier: "qc_required"` with `qc.required: true` and
  `lens: "architecture_audit"`.
- A full `quality_plan`: explicit panel mode, three named personas,
  synthesis with dissent preserved and P0 rejection, arbitration enabled on
  `p0_disagreement`/`low_confidence`, and the default budget spelled out so you
  can tune it.
- Deterministic gates still run first: `file_exists`, `min_size`, and
  `required_sections` prove shape before any model opinion is spent.
- `quality_spec.hard_failures` names the two failure modes that auto-reject,
  independent of score.

## Run It

```bash
lil goal-create architecture-audit-payment-service
lil plan architecture-audit-payment-service
lil supervise architecture-audit-payment-service
```

## Variations

- **Cheapest useful QC**: keep `qc.required: true` with only
  `"quality_spec": {"minimum_score": 0.7}` and no explicit `quality_plan`;
  the derived single-reviewer plan handles it.
- **Compliance-grade**: set `risk_tier: "human_required"` so the panel default
  and arbitration engage without hand-writing the whole plan.
