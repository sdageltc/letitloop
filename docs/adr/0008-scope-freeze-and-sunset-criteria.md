# ADR-0008: Scope Freeze and Sunset Criteria

**Date**: 2026-08-24
**Status**: `proposed`
**Deciders**: `sdageltc`

---

## Context

The 13-issue batch (#7-#20) delivered breadth before demand: 45+ CLI commands, 10+ worker adapters, TUI, webhooks/SSE, MCP client+server, Docker sandbox, worktree sandboxing - all with zero external users. AutoGPT's post-mortem and the Plandex/Sweep deaths both show that breadth without adoption is negative-value: it multiplies maintenance surface (GPT-Pilot's 33.8k-star repo harbored a credential-stealer worm for 10 months *because it was unmaintained*) and delays the focused story that converts.

## Decision

1. **Scope freeze** until both exit criteria are met: (a) first published benchmark number (ADR-0007) AND (b) first external contributor or user-reported run. During the freeze: no new worker adapters, no new CLI surfaces, no new integrations. Bug fixes, docs, and beachhead work (ADR-0006) are exempt.
2. **Interop over surface**: AGENTS.md/SKILL.md and MCP are the only integration investments allowed - they are the universal adapter layer the 2026 market assumes.
3. **Sunset criteria** (review at 6 months from ADR acceptance). Reassess kill/merge/pivot if ANY of:
   - fewer than 3 external PRs or issues from non-author accounts,
   - no benchmark number published (execution failure),
   - a funded player occupies the proof-carrying-remediation niche (Goose recipes absorbing it, or Embabel shipping the Python port),
   - maintainer velocity drops below sustainable for the beachhead.
4. On sunset trigger, preferred order: (1) narrow further to consulting/tooling around the beachhead, (2) merge the proof-semantics work into the harness-cluster coalition (rigger/harnesswright) as a joint standard, (3) archive with a clean final post-mortem. Do not zombie-maintain - GPT-Pilot is the cautionary tale.

## Consequences

**Positive**: protects the beachhead focus; bounded downside; pre-committed exit prevents sunk-cost drift; supply-chain hygiene becomes a feature (signed releases, SBOM - which is also the beachhead product).

**Negative**: closing the door on general-purpose contributions during the freeze may cost some community goodwill; mitigated by the good-first-issue ladder (#23-#33) which remains open within scope.