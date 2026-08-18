---
name: debugging-failures
description: Diagnostic and debugging workflow for analyzing letitloop task failures, three-strike impossibility artifacts, WAL journal recovery, and state machine transition errors.
metadata:
  author: Oguzhan Kayan (@sdageltc)
  version: "1.0.0"
compatibility: Cross-platform (CLI diagnostic suite)
---

# Debugging Task Failures in letitloop

When a contract attempt fails or hits a three-strike impossibility escalation in `letitloop`, use this diagnostic playbook to locate root causes and recover state.

## 1. Diagnostic CLI Commands

1. **Diagnose Task State**:
   ```bash
   lil doctor <task_id>
   ```
   Inspects attempt counts, verification exit codes, error logs, and whether the task is terminal.

2. **View Full Task State**:
   ```bash
   lil status <task_id>
   ```
   Dumps the JSON state including history of tried approaches and output hashes.

3. **Inspect Crash Recovery & Checkpoints**:
   ```bash
   lil checkpoint-recover <goal_id>
   ```
   Scans the Write-Ahead Log (WAL) journal and reconstructs valid in-memory state.

## 2. Common Failure Modes & Remedies

| Symptom | Root Cause | Fix / Resolution |
|---|---|---|
| **Three-Strike Impossibility** | Worker repeated non-divergent approaches 3 times without passing verification. | Check `scratch/orchestrator_runs/tasks/<task_id>/impossibility_report.json`. Adjust verification command or prompt instructions. |
| **Out-of-Bounds Scope Violation** | Worker wrote files outside `workspace_scope.allow`. | Check `scratch/orchestrator_runs/tasks/<task_id>/scope_violation.log`. Add needed directories to contract `allow` list. |
| **Budget Exceeded (`BudgetExceededError`)** | Token burn or cost ceiling exceeded `BudgetGuard` limit. | Increase budget in contract metadata or split the task into smaller subtasks via `lil replan`. |
| **Orphan Lock Contention** | Previous crash left an active `.lock` file. | Check process start tokens via `lock.py`; lock auto-expires if owner PID is dead. |
