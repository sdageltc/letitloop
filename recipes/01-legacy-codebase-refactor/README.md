# Recipe 01 - Legacy Codebase Refactor

Refactor an existing, fragile Python module into a clean package while the
orchestrator proves you did not break anything. This is the classic
"tangled single file" scenario: the worker gets a tightly fenced sandbox,
deterministic gates decide success, and retries are bounded.

## What You Build

- `src/ledger/` package refactored out of a legacy `src/ledger.py`, passing
  `ruff` with zero warnings.
- A pytest regression suite that pins the public API before and after.

The full worked example lives in [`refactor-goal.json`](refactor-goal.json):
one goal, two contracts (`01-refactor-ledger-module` -> `02-regression-tests`)
chained with `depends_on`.

## Step 1: Fence The Workspace Scope

Every contract declares exactly what the worker may write:

```json
"workspace_scope": {
  "allow": ["src/ledger.py", "src/ledger/"],
  "deny": [".env", ".git/", "scratch/orchestrator_runs/"]
}
```

Why fence at all?

- The supervisor snapshots the filesystem and detects *undeclared outputs*;
  any file mutated outside `allow` is a scope violation.
- `deny` is checked first and wins over `allow`, so secrets (`.env`) and VCS
  internals (`.git/`) stay untouchable even if someone adds a broad allow.
- The refactor contract allows both the legacy file and its replacement
  package; the test contract only allows `tests/test_ledger.py` plus read
  access to the new package via `inputs`.
- Declared `outputs` must fall inside `allow` - the validator rejects
  contracts whose outputs escape their own sandbox.

## Step 2: Choose Acceptance Gates That Actually Prove Refactoring

| Check | Kind | Why this gate |
|-------|------|---------------|
| Each new module parses | `syntax` | Catches truncated or malformed Python before anything else runs. |
| `ruff check src/ledger/` exits 0 | `command` (`expected: 0`) | Enforces lint cleanliness as part of "done", not as an afterthought. |
| Public symbol still exists | `content_regex` | Cheap guard that the public API survived the split. |
| Test suite passes | `command` on the tests contract | Behavior-preservation proof, executed only after the refactor lands. |
| Suite is non-trivial | `min_size` | Prevents a hollow one-line test file from "passing". |

`command` checks assert subprocess exit codes (`expected: 0`), so a red pytest
or ruff run fails the contract deterministically - no LLM self-grading.

## Step 3: Chain With depends_on

In the goal document, each plan entry carries `depends_on` referencing earlier
`task_id`s:

```json
{
  "task_id": "02-regression-tests",
  "depends_on": ["01-refactor-ledger-module"],
  "contract": { "...": "..." }
}
```

The dependency graph refuses cycles, and a task only becomes ready once all of
its dependencies are complete. Note that `depends_on` belongs on the plan
wrapper entry - not inside the contract object itself, where it would be an
unknown key and rejected by validation.

## Step 4: Retry Expectations (3-Strike)

- `worker.max_attempts: 3` bounds how many times the supervisor will re-dispatch
  the worker for a failing contract.
- Retries are expected to change strategy between attempts; identical repeated
  failures burn strikes without progress.
- After three consecutive failures the branch halts, the task is escalated out
  of the automatic loop, and an impossibility report is produced instead of the
  loop spinning forever.
- Deterministic gates make strikes meaningful: a failure is always a concrete,
  reproducible verifier result, never a vibe.

Keep `max_attempts` small on refactors - if the third attempt still cannot pass
`ruff`, a human should look at the module rather than a fourth blind retry.

## Run It

```bash
# One-shot from natural language
lil propose "Refactor src/ledger.py into a ruff-clean package with regression tests" --run

# Or granular, using this recipe's goal document as the reference shape
lil goal-create legacy-refactor-ledger
lil plan legacy-refactor-ledger
lil plan-check legacy-refactor-ledger
lil supervise legacy-refactor-ledger
```

## Variations

- **Riskier surgery**: set `risk_tier` to `"qc_required"` on the refactor
  contract. Non-scratch outputs then require a non-empty `quality_spec`
  (see Recipe 04) so the quality plane reviews the diff before sign-off.
- **No ruff in your stack**: swap the lint command check for any exit-code
  gate your project already trusts (mypy, flake8); the schema is unchanged.
