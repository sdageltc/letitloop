# ADR-0007: Benchmark-First Credibility

**Date**: 2026-08-24
**Status**: `proposed`
**Deciders**: `sdageltc`

---

## Context

Industry-standard status in 2026 requires a published external benchmark number. Evidence:

- mini-SWE-agent (100 LOC) is taken seriously because of >74% SWE-bench Verified - not because of its architecture.
- The comparables sweep found no funded or major player without a benchmark story; sub-74% scores are described as "invisible."
- letitloop has 1,403 passing internal tests, which prove engineering hygiene but say nothing about task-completion capability. Internal tests are table stakes; external benchmarks are marketing.
- The "Resume Contract" paper (arXiv 2608.03836) machine-checked five major frameworks' durability claims and found violations in LangGraph and CrewAI. Provable semantics is an open differentiator - but only if someone runs the proof and publishes it.

## Decision

1. Adopt **SWE-bench Lite (or Verified subset)** and **Terminal-Bench** as the two public numbers, run through letitloop's own harness (Claude Code / Codex as workers) - the harness IS the product demo.
2. Publish the score honestly on the README and landing page before any marketing push, whatever it is. A mediocre published number beats a claimed one; the Resume Contract paper proves empirical conformance testing itself earns attention.
3. Additionally publish a **durability conformance test**: kill -9 mid-run on every framework checkpoint and show letitloop resumes exactly-once while competitors do not. This weaponizes our WAL differentiator into a public artifact.
4. Target: first published numbers within 60 days of ADR acceptance.

## Consequences

**Positive**: converts "1121 tests" (inward-facing) into outward-facing credibility; benchmark runs double as reproducible demos; the conformance-test angle is unclaimed territory none of the six harness-cluster repos have done.

**Negative**: benchmark infrastructure is real work; the first score may be unflattering and must be published anyway (honesty is the strategy).

## Alternatives rejected

- Skip benchmarks and lead with features: rejected - that is the exact profile of the sub-500-star harness cluster.
- Wait for a bigger engine: rejected - mini-SWE-agent proved the opposite wins.
