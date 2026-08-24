# Recipe 03 - Offline Local LLM Loop

Run the entire letitloop loop with zero cloud keys: Ollama serves the model on
your machine, and the deterministic verifiers prove the work.

## Step 1: Pull A Local Model

```bash
ollama pull qwen2.5-coder
ollama serve   # if the daemon is not already running; default port 11434
```

Pin any tag you like (`qwen2.5-coder:7b`, `:32b`, ...) - just keep the tag in
your model string consistent with what you pulled.

## Step 2: Point letitloop At The Local Endpoint

The provider registry treats `ollama:` as a first-class prefix, so a model
string like `ollama:qwen2.5-coder` resolves to the local OpenAI-compatible
endpoint at `http://localhost:11434/v1` by default. Environment variables in
`.env` control routing without touching contracts:

```bash
export WORKER_MODEL="ollama:qwen2.5-coder"
export QC_MODEL="ollama:qwen2.5-coder"
export PLANNER_MODEL="ollama:qwen2.5-coder"
```

In contract JSON, the worker is just:

```json
"worker": {
  "model": "ollama:qwen2.5-coder",
  "max_attempts": 3
}
```

`max_attempts` must be an integer >= 1; 3 matches the bounded 3-strike retry
policy, which matters more on smaller local models that may need a second try
to satisfy a strict verifier.

## Step 3: Pick The Right Adapter

Two registered adapters matter for local work (both defined in
`orchestrator/worker_adapters.py`):

### `local-tool` - native tool calling against local endpoints

`LocalToolWorkerAdapter` talks to any OpenAI-compatible `/chat/completions`
endpoint (Ollama, vLLM, LM Studio) and executes the model's tool calls -
`read_file`, `write_file`, `replace_lines`, `execute_command` - against a
sandboxed registry scoped to the task's `workspace_scope`. Relevant config
keys it reads:

| Key | Default | Purpose |
|-----|---------|---------|
| `base_url` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `model` | none | Model name served at the endpoint (required to be "available") |
| `max_turns` | `8` | Tool-call round trips before giving up for one attempt |
| `api_key` | none | Optional bearer token (vLLM/LM Studio setups sometimes need one) |

Every turn is journaled under `scratch/orchestrator_runs/<task_id>/worker_output.log`,
so you can debug why a small model kept missing its tool calls.

### `docker` - container sandbox

`DockerWorkerAdapter` runs the task inside an isolated container instead of on
your host. Config keys it reads:

| Key | Default | Purpose |
|-----|---------|---------|
| `image` | `python:3.11-slim` | Container image to run |
| `network` | `none` | Docker network mode (`none` = fully offline) |
| `cpus` | `1.0` | CPU limit passed to `docker run --cpus` |
| `memory` | `512m` | Memory limit passed to `docker run --memory` |
| `script` | `cat "${LIL_INSTRUCTIONS}"` | Command executed inside the container |

Allow-listed workspace paths are mounted read-write, everything else
read-only, and the instructions are staged as a file inside the container.
Use this adapter when you do not yet trust a model's shell habits.

## Step 4: The Worked Example

[`local-goal.json`](local-goal.json) contains one simple contract that asks the
local model to create `greeting.py` and `test_greeting.py` under
`scratch/local_llm_demo/`. Its final gate is a real proof:

```json
{
  "id": "pytest_pass",
  "kind": "command",
  "command": "python -m pytest scratch/local_llm_demo -q",
  "expected": 0
}
```

Because all declared outputs live under `scratch/`, the goal stays trivially
safe to run unattended - scratch output is throwaway by convention.

## Run It

```bash
lil goal-create offline-local-hello
lil plan offline-local-hello
lil supervise offline-local-hello
```

No API keys required at any point; verification is subprocess exit codes and
AST parsing, both fully offline.

## Variations

- **Bigger brain locally**: swap the model string to another pulled tag or a
  vLLM endpoint by changing only the `model` value and the endpoint config.
- **Hard isolation**: combine this recipe's goal with the `docker` adapter so
  even the worker's shell runs inside the sandbox.
