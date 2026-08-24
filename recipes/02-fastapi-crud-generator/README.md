# Recipe 02 - FastAPI CRUD Generator

Turn one sentence ("add an Item CRUD API") into a four-stage contract DAG
where every stage is fenced to exactly one file and verified before the next
stage starts.

## The DAG: Schema -> Models -> Endpoints -> Tests

```
01-define-schema ──> 02-persistence-models ──> 03-endpoints ──> 04-crud-tests
```

Each arrow is a `depends_on` edge on the plan entry (see
[`crud-goal.json`](crud-goal.json)). The orchestrator topologically sorts these
edges, refuses cycles, and only dispatches a stage when its parents are done.
This ordering is not cosmetic:

- **Schema first** - downstream stages import stable Pydantic shapes instead of
  inventing their own.
- **Models before endpoints** - endpoints wire validation to storage, so both
  imports must already exist; they enter the contract as `inputs`.
- **Tests last** - the suite runs against real endpoints, and its `command`
  check (`pytest`, `expected: 0`) is the feature's final proof.

## One File Per Contract

Each contract's `workspace_scope.allow` lists a single path, and its declared
`outputs` live inside it:

| Stage | allow | Output |
|-------|-------|--------|
| 01-define-schema | `src/schema.py` | `src/schema.py` |
| 02-persistence-models | `src/models.py` | `src/models.py` |
| 03-endpoints | `src/api.py` | `src/api.py` |
| 04-crud-tests | `tests/test_items.py` | `tests/test_items.py` |

Narrow fences mean a worker bug in stage 02 physically cannot touch your
endpoints file. Anything the worker needs to *read* but not write goes into
`inputs`. Shared `deny` entries (`.env`, `.git/`) are repeated everywhere -
deny always wins over allow.

## Acceptance Checks: Mixing Kinds

The recipe mixes deterministic kinds deliberately:

- `file_exists` with `"expected": "nonempty"` - cheap existence gate first.
- `syntax` - AST-parse each generated Python file before anything imports it.
- `content_regex` - assert the required surface appears
  (`class ItemBase`, `def create_item`, `def test_create_item`). Regex checks
  need an `expected` pattern by schema.
- `min_size` - byte floor on the test module so "passing" cannot mean hollow.
- `command` - `python -m pytest tests/test_items.py -q` with `"expected": 0`;
  exit code or it did not happen.

## Choosing risk_tier Per Stage

- `"auto"` (stages 01, 02, 04): mechanical generation where deterministic gates
  are enough. Cheap and fast.
- `"qc_required"` (stage 03): endpoints are where injection, auth, and status
  code mistakes hide. This tier routes output through the quality plane for a
  model review in addition to the deterministic gates.

One schema rule to remember: when `qc.required` is `true` and outputs are not
scratch files, the contract must also carry a non-empty `quality_spec` - stage
03 sets `"quality_spec": {"minimum_score": 0.7}` for exactly this reason.
See Recipe 04 for full quality-plane configuration.

## Run It

```bash
lil goal-create fastapi-crud-items
lil plan fastapi-crud-items
lil plan-check fastapi-crud-items
lil supervise fastapi-crud-items
```

Or let the planner decompose your own phrasing directly:

```bash
lil propose "Add an Item CRUD API to the FastAPI app with schemas, models, and tests" --run
```

## Variations

- **Parallel fan-out**: stages 01 and any independent utility module can share
  no edges and run concurrently; add edges only where imports demand them.
- **Stricter tests stage**: bump it to `"qc_required"` too if coverage claims
  matter more than latency.
