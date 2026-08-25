---
id: DEC-2026-08-25-LETITLOOP-FOUNDER-PITCH
type: decision
scope: global
title: LetItLoop Ecosystem Founder Pitch & Core Strategic Narrative
status: active
created: 2026-08-25
updated: 2026-08-25
tags:
  - letitloop
  - founder-narrative
  - sigkill
  - durability
  - agent-durability-bench
  - letitloop-action
  - wal
aliases:
  - letitloop-pitch
  - founder-story
  - sigkill-value-prop
triggers:
  - how to explain letitloop
  - what is letitloop pitch
  - why did we build letitloop
  - letitloop founder narrative
  - what is unique about letitloop
retrieval:
  priority: normal
  grep_terms:
    - letitloop pitch
    - founder narrative
    - sigkill problem
    - agent durability bench
    - letitloop-action fallback
---

# LetItLoop: Canonical Founder Narrative & Pitch

## User's Verbatim Statement (Ground Truth)

> *"first the idea was an agentic loop harness that can utilize the model to its best capabilities with anti-trust due to LLM's blackbox reality so the system was hybrid and ran with deterministic python features. later it was reduced to mvp features, letitloop stayed as the plumbing but while building the architecture the real genuine gap in current ai ground came to light, that was partially understanding and benchmarking sigkill problem, so letitloop divided into three, one as letitloop-action to allow recovery from agent work that was mid-crash so both time and money spent is partially preserved as a fallback. second, agent durability bench that does not focus on agent outputting the goal perfectly but focuses on the process and in case things go wrong."*

---

## Core Value Structure

1. **The Origin**:
   - Building a hybrid agentic loop harness that acknowledges LLMs are non-deterministic black boxes.
   - Using deterministic Python verification (AST analysis, exit codes, contract scopes) to maintain zero-trust boundaries.

2. **The Discovered Gap (`SIGKILL` / Process Durability)**:
   - While building the plumbing, we realized the acute, unaddressed gap in the AI landscape: **what happens when processes crash or receive `SIGKILL` mid-execution?**
   - Existing tools lose state, re-bill duplicate tokens from step 1, or corrupt repository files.

3. **The 3-System Division**:
   - **`letitloop`**: The underlying engine plumbing (source-span AST splicer + SQLite/JSONL Write-Ahead Log).
   - **`letitloop-action`**: The fallback CI gate that recovers mid-crash agent work, preserving time and money spent while blocking destructive AST regressions.
   - **`agent-durability-bench`**: The process-centric benchmark that evaluates how agents handle failure and recovery, rather than just grading output in an uninterrupted sandbox.
