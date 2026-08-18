# ADR-0003: Zero-API-Key Headless Agent CLI Wrapper Failovers

**Date**: 2026-08-18  
**Status**: `accepted`  
**Deciders**: Lead Architecture Team (`sdageltc`)  

---

## Context

Many users and enterprise environments run AI coding assistants (Google Antigravity `agy`, Claude Code CLI `claude`, OpenCode `opencode`, Hermes `hermes`) using host subscriptions and local authentication rather than exposing raw, unmetered REST API keys (`OPENAI_API_KEY`, `GEMINI_API_KEY`). Requiring raw API keys in environment variables blocks adoption and causes authorization failures.

---

## Decision

`letitloop` implements transparent **Agent CLI Wrapper Failovers**:
1. When REST API keys are absent or return authentication/credit errors (HTTP 401, 402, 403), `call_llm` and `WorkerRegistry` automatically detect installed CLI tools on the host system.
2. Non-interactive headless invocation formats are supported across:
   - Google Antigravity (`agy -p "<prompt>" --dangerously-skip-permissions --add-dir <workspace>`)
   - Claude Code CLI (`claude -p "<prompt>" --dangerously-skip-permissions`)
   - OpenCode (`opencode exec --prompt "<prompt>"`)
   - Hermes Agent (`hermes -q "<prompt>"`)
   - Cline, Aider, Codex CLI
3. Planner decomposition, worker code generation, and hybrid loops operate seamlessly through these local wrappers without requiring API keys.

---

## Alternatives Considered

### Alternative 1: Strictly Require REST API Keys in `.env`
- **Pros**: Direct HTTP control over raw completion parameters.
- **Cons**: Forces users to purchase redundant API keys when they already possess valid coding assistant CLI subscriptions.
- **Why Rejected**: Degrades developer experience and causes immediate out-of-the-box friction.

---

## Consequences

### Positive
- Zero configuration required for users who already have `agy`, `claude`, or `opencode` installed.
- Full end-to-end task decomposition and execution with local auth.

### Negative & Trade-offs
- CLI subprocess calls have slightly higher latency than direct HTTP JSON-RPC (~1-2s process startup).
