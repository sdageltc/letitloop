# letitloop Recipe Book

Practical, end-to-end recipes for driving the `letitloop` (LIL) orchestration engine.
Every JSON example here is machine-checked against the real contract schema by
`tests/test_recipes.py`, so the snippets stay honest as the engine evolves.

## Recipe Index

| # | Recipe | What you will learn |
|---|--------|---------------------|
| 01 | [Legacy Codebase Refactor](01-legacy-codebase-refactor/README.md) | Refactoring an existing Python module under `pytest` + `ruff` acceptance gates, scope fencing, and bounded retries. |
| 02 | [FastAPI CRUD Generator](02-fastapi-crud-generator/README.md) | Decomposing a feature into a 4-node contract DAG chained with `depends_on`, each fenced to a single file. |
| 03 | [Offline Local LLM Loop](03-offline-local-llm-loop/README.md) | Running the full loop with zero cloud keys using Ollama, the `local-tool` adapter, and the `docker` sandbox adapter. |
| 04 | [Multi-Agent QC Audit](04-multi-agent-qc-audit/README.md) | Configuring the multi-lens quality plane: panels, arbitration, budgets, and `quality_spec`. |

## How To Use A Recipe

Each recipe directory pairs a walkthrough (`README.md`) with a ready-made goal
document (`*-goal.json`) containing one or more typed contracts.

Two ways to run what you read:

```bash
# One-shot: propose a plan from natural language and execute it
lil propose "<your objective>" --run

# Granular: create a goal, generate its plan, sanity-check it, then supervise
lil goal-create <goal_id>
lil plan <goal_id>
lil plan-check <goal_id>
lil supervise <goal_id>
```

The JSON files are reference plans: they show the exact shape the orchestrator's
strict contract validator accepts (required keys, `status: "drafted"`, risk tiers,
scope lists, acceptance-check kinds, and `quality_spec`). Copy one, rename the
IDs, adjust the `workspace_scope` fences and acceptance checks to your task.

## Contributing A Recipe

- Add a new numbered directory with a `README.md` walkthrough and at least one
  `*-goal.json` document.
- Every contract key must exist in the engine: unknown top-level keys are
  rejected by the validator, and invented CLI flags will not work.
- Run `python -m pytest tests/test_recipes.py -q` before opening a PR; it
  validates every embedded contract against the live schema and enforces a
  README in each recipe directory.
- Prefer generic phrasing for anything version-dependent (model tags, endpoint
  URLs), and keep examples runnable offline where possible.
