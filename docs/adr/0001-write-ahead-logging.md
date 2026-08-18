# ADR-0001: Write-Ahead Logging (WAL) & Zero-State Recovery

**Date**: 2026-08-15  
**Status**: `accepted`  
**Deciders**: Lead Architecture Team (`sdageltc`)  

---

## Context

Autonomous multi-agent execution loops frequently suffer from catastrophic context loss or corrupted state when processes are interrupted (e.g. process termination, machine reboot, network drops, or timeout boundaries). In-memory state tracking causes tasks to restart from zero or silently duplicate work, leading to rate limit exhaustion and inconsistent file states.

---

## Decision

We enforce atomic **Write-Ahead Logging (WAL)** for all supervisor state mutations across the lifecycle:
1. Every state transition (`DRAFTED` -> `PREFLIGHT` -> `WORKING` -> `VERIFYING` -> `QC` -> `COMPLETE`) is serialized to an append-only transaction ledger (`state.json` and `checkpoint_*.json`) before actions execute.
2. File updates use atomic rename semantics (`.tmp` write followed by `os.replace`) to eliminate partial JSON corruption.
3. Process interruption recovery (`_recover_graph_from_state_files()`) automatically reconstructs the DAG dependency graph upon restart without re-executing completed contracts.

---

## Alternatives Considered

### Alternative 1: SQLite / Embedded Database
- **Pros**: Robust ACID properties, SQL queryability.
- **Cons**: Requires binary C extensions or heavier dependencies, harder for users to inspect or git-version raw state in scratch folders.
- **Why Rejected**: `letitloop` maintains a strict zero-dependency philosophy (`pyyaml` only). Human-readable JSON WAL files are inspectable via standard terminal tools.

### Alternative 2: Pure In-Memory Graph with Checkpoint Dumps
- **Pros**: Fast in-memory state manipulation.
- **Cons**: Process crashes between checkpoints result in lost progress and orphaned filesystem changes.
- **Why Rejected**: Fails the zero-data-loss invariant.

---

## Consequences

### Positive
- Interrupted goals resume seamlessly with `lil supervise-resume <goal_id>` or `lil run-approved <goal_id>`.
- Full auditability: complete event timestamps, transitions, and evidence paths recorded in `state.json`.

### Negative & Trade-offs
- Slight filesystem IO overhead on each transition (mitigated via atomic JSON buffering).
- Orphaned `.lock` files on abnormal process termination require stale-lease detection or `--force` override.
