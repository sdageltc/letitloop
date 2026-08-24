# ADR-0005: Reposition from General Autonomous Engine to Deterministic Verification Harness

**Date**: 2026-08-24
**Status**: `proposed`
**Deciders**: `sdageltc`

---

## Context

Market research (Aug 2026) shows:

1. **The general-autonomy position is dead.** AutoGPT (186k stars, $12M seed, 8-10 staff) abandoned its own unconstrained goal-loop for typed graphs with cost kill-switches and validation-retry loops. Its team's conclusion: "autonomy without boundaries is chaos." The classic loop survives only as an unsupported exhibit.
2. **The exact letitloop thesis is "in the air."** At least six solo repos (rigger-ai/rigger, harnesswright, AgentHarness, claude-harness, celesteanders/harness, agentops-harness) independently shipped the same plan-contract-verify-retry pattern within 12 months, sparked by Anthropic's published Planner-Generator-Evaluator harness research. The idea is validated *and* no longer novel.
3. **Heavy scaffolds are empirically losing.** mini-SWE-agent (100 LOC) scores >74% SWE-bench Verified and beat Claude Code and Codex as a harness. letitloop is a heavy scaffold with zero external benchmark evidence.
4. **letitloop's specific combination remains unoccupied at scale**: machine-verifiable task-level acceptance (AST/exit-code/scope fences) + bounded retries terminating in formal impossibility proofs + WAL crash recovery + multi-lens QC arbitration. Durability is commoditized (Temporal), schema-determinism is commoditized (BAML/Instructor), spec-governance is venture-funded (Tessl, $125M) - none combine task-level acceptance with proof-of-impossibility termination.
5. **Budget flows to verification, not generation**: $1.2B VC into PR-review bots (2024-25), Temporal at $5B, Diffblue at 326% ARR selling "trusted results" to banks, Pramaana ($27M seed) machine-checking LLM outputs.
6. **Solo-maintainer reality**: Plandex (solo, 15.4k stars) capitulated; Sweep AI discontinued while installs grew; distribution and integration-breadth wars are unwinnable alone.

## Decision

Reposition letitloop from **"general autonomous macro-task engine"** to **"the deterministic verification harness for AI coding agents"**:

1. **Embrace existing agents as workers, not competitors.** Claude Code, Codex CLI, Cursor, Aider are the generation layer; letitloop is the trust layer that decides whether their output ships. They are already our worker adapters - make this the headline, not a feature list.
2. **Adopt ONE vertical beachhead** where deterministic verification is legally or economically load-bearing (candidates evaluated in ADR-0006).
3. **Freeze breadth.** No new worker adapters, integrations, or surface features until the beachhead ships and an external benchmark number is published.
4. **Keep the un-occupied differentiators** as the moat story: impossibility proofs, WAL + evidence ledger, scope fences, QC arbitration plane. Retire "10 adapters + 45 CLI commands" from the pitch.

## Consequences

**Positive**: clear one-sentence story; aligns with where budget flows; plays to solo-dev strengths (depth, finished-ness); the convergence cluster validates demand instead of threatening us - none of them have our proof semantics.

**Negative**: abandons the "bold general platform" narrative that motivates broad OSS attention; requires saying no to interesting general-purpose contributions.

**Neutral**: the engine code is unchanged; this is a positioning and roadmap decision, not a rewrite.

## Alternatives considered

- **Kill the project**: rejected - white space verdict is positive, the asset is real, and the wave is rising.
- **Stay general/bold**: rejected - that is the position AutoGPT itself retreated from with $12M and 8 staff; a solo dev cannot win the integration-breadth war against Factory.ai ($220M) and OpenHands.
- **Merge into the harness-cluster coalition**: rejected for now - our proof semantics (impossibility proofs, WAL, QC plane) are ahead of that cluster; revisit if traction fails by the ADR-0008 sunset criteria.
