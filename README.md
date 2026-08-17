# let it loop (LIL)

**let it loop (LIL)** — Autonomous task orchestration system, a durable macro-task control loop with planning, execution, verification, and quality review.

**Autonomous task orchestration system** — a durable macro-task control loop with planning, execution, verification, and quality review.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Tests](https://img.shields.io/badge/tests-363%20passing-brightgreen.svg)

## Overview

agent-loop is a production-ready orchestration system that manages autonomous AI agents to complete complex tasks. It provides:

- **Durable state management** — Tasks survive crashes and can be resumed
- **Multi-provider LLM support** — Works with OpenAI, Anthropic, Gemini, DeepSeek, or any OpenAI-compatible API
- **Quality verification** — Automated acceptance checks and quality review panels
- **Planner integration** — Automatic goal decomposition into executable contracts
- **Fault tolerance** — Retry logic, rollback capabilities, and error recovery

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/sdageltc/letitloop.git
cd letitloop

# Install in development mode
pip install -e .

# Verify installation
lil --help
```

### LLM Provider Configuration

The system works with **any** OpenAI-compatible API endpoint. Set one environment variable pair:

#### Option 1: Generic (any OpenAI-compatible endpoint)

```bash
# Linux/macOS
export LLM_API_KEY="your-key"
export LLM_BASE_URL="https://api.openai.com/v1"  # default

# Windows (PowerShell)
$env:LLM_API_KEY="your-key"
$env:LLM_BASE_URL="https://api.openai.com/v1"
```

#### Option 2: Named providers

| Provider  | Env Var           | Model prefix     |
|----------|-------------------|------------------|
| OpenAI   | `OPENAI_API_KEY`  | `openai:gpt-4o`  |
| Anthropic| `ANTHROPIC_API_KEY`| `anthropic:claude-3-5-sonnet-20241022` |
| Gemini   | `GEMINI_API_KEY`  | `gemini:gemini-2.0-flash` |
| DeepSeek | `DEEPSEEK_API_KEY`| `deepseek:deepseek-chat` |
| Any      | `LLM_API_KEY` + `LLM_BASE_URL` | `any:model-name` or bare `model-name` |

Each named provider can override its base URL via `<PROVIDER>_BASE_URL`, e.g. `OPENAI_BASE_URL=https://my-proxy.example/v1`.

#### Using with Ollama / vLLM / LM Studio

```bash
export LLM_API_KEY="not-needed"
export LLM_BASE_URL="http://localhost:11434/v1"
lil work task-1  # uses model "any:llama3" by default
```

Or set `ORCHESTRATOR_MODEL` to specify the model:

```bash
export ORCHESTRATOR_MODEL="any:qwen2.5-coder:14b"
```

### Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `WORKER_MODEL` | Worker model string | `gemini:gemini-3.6-flash` |
| `QC_MODEL` | QC reviewer model | `gemini:gemini-3.1-pro` |
| `PLANNER_MODEL` | Planner model | `WORKER_MODEL` value |
| `LLM_API_KEY` | API key for generic provider | - |
| `LLM_BASE_URL` | Base URL for generic provider | `https://api.openai.com/v1` |
| `ORCHESTRATOR_MODEL` | Override worker model | `gemini:gemini-3.6-flash` |
| `ORCHESTRATOR_RUN_DIR` | Run state directory | `./scratch/orchestrator_runs` |
| `ORCHESTRATOR_MAX_ATTEMPTS` | Max worker retries | `3` |
| `ORCHESTRATOR_TIMEOUT` | Worker timeout (seconds) | `300` |
| `ORCHESTRATOR_PARALLEL` | Enable parallel execution | `false` |
| `ORCHESTRATOR_MAX_WORKERS` | Max concurrent workers | `3` |

## Usage

### Basic Workflow

```bash
# 1. Create a task from a contract
lil create contract.json

# 2. Run preflight checks
lil preflight task-id

# 3. Execute the task
lil work task-id

# 4. Verify the output
lil verify task-id

# 5. Check status
lil status task-id
```

### Contract Format

Create a contract file `contract.json`:

```json
{
  "task_id": "hello-world",
  "title": "Create a hello world script",
  "objective": "Write a Python script that prints 'Hello, World!'",
  "outputs": [
    {"path": "scratch/hello.py"}
  ],
  "acceptance_checks": [
    {
      "id": "file_exists",
      "kind": "file_exists",
      "path": "scratch/hello.py",
      "expected": "nonempty"
    },
    {
      "id": "content_check",
      "kind": "content_regex",
      "path": "scratch/hello.py",
      "expected": "Hello, World!"
    }
  ]
}
```

### Advanced: Supervisor Mode

The supervisor manages multiple contracts and handles retries automatically:

```python
from orchestrator.supervisor import Supervisor
from orchestrator.config import OrchestratorConfig

config = OrchestratorConfig()
supervisor = Supervisor(config)

# Load and execute a goal
goal = supervisor.load_goal("goal.json")
result = supervisor.run(goal)
```

## Architecture

```
orchestrator/
├── cli.py              # CLI entry point
├── config.py           # Configuration management
├── llm.py              # Generic LLM transport layer
├── planner.py          # Goal decomposition via LLM
├── worker.py           # Task execution
├── verifier.py         # Output verification
├── supervisor.py       # Multi-contract orchestration
├── quality_plane.py    # Multi-reviewer QC panel
├── contract.py         # Contract validation
├── state.py            # Durable state management
├── preflight.py        # Pre-execution checks
├── evidence.py         # Evidence collection
├── checkpoint.py       # Execution checkpoints
├── feedback.py         # Feedback loops
├── metrics.py          # Performance metrics
├── safety.py           # Safety checks
├── scope.py            # Scope management
└── ...
```

## Testing

### Running Tests

```bash
# Full suite (no API keys needed — uses FAKE_WORKER mode)
python -m pytest tests -q --ignore=tests/test_integration.py

# Fast tests only
python -m pytest tests -q -m fast

# With coverage
python -m pytest tests --cov=orchestrator --cov-report=html

# Live smoke test (requires API key)
SKIP_LIVE=0 python -m pytest tests/test_live_smoke.py -q
```

### Test Categories

| Marker | Description |
|--------|-------------|
| `fast` | Pure logic tests, no live worker or subprocess |
| `integration` | CLI flow tests, may use subprocess but avoid live worker |
| `proof` | Real worker-backed end-to-end proofs, slow and expensive |
| `phase2` | Phase 2 supervisor and multi-contract tests |
| `benchmark` | Performance benchmark tests |
| `live` | Live end-to-end tests requiring API key (opt-in via `SKIP_LIVE=0`) |

### Writing Tests

Tests use `FAKE_WORKER` mode by default:

```python
import pytest
from orchestrator.worker import run_worker

@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")

def test_worker_execution():
    result = run_worker(contract, workspace_root, run_dir)
    assert result["success"] is True
```

## Development

### Setup

```bash
# Clone and install
git clone https://github.com/sdageltc/letitloop.git
cd letitloop
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Run linting
ruff check orchestrator tests
ruff format orchestrator tests
```

### Code Style

- Line length: 120 characters
- Python 3.11+ syntax
- Type hints required for public APIs
- Docstrings for all public functions
- Tests for all new features

### CI/CD

GitHub Actions runs automatically on push:

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: python -m pytest tests -q --ignore=tests/test_integration.py
```

## API Reference

### Core Classes

#### `OrchestratorConfig`
Central configuration for the orchestrator.

```python
from orchestrator.config import OrchestratorConfig

config = OrchestratorConfig(
    workspace_root="/path/to/workspace",
    run_dir="/path/to/runs",
    worker=WorkerConfig(model="gemini:gemini-3.6-flash"),
    pool=PoolConfig(enabled=True, max_workers=3),
)
```

#### `Supervisor`
Multi-contract orchestration engine.

```python
from orchestrator.supervisor import Supervisor

supervisor = Supervisor(config)
result = supervisor.run(goal)
```

#### `Contract`
Task contract definition.

```python
from orchestrator.contract import Contract

contract = Contract({
    "task_id": "my-task",
    "title": "My Task",
    "objective": "Do something",
    "outputs": [{"path": "output.txt"}],
})
```

### LLM Transport

```python
from orchestrator.llm import call_llm, provider_of

# Provider detection
provider = provider_of("openai:gpt-4o")  # Returns "openai"

# Direct LLM call
response = call_llm(
    prompt="Hello, world!",
    model="gemini:gemini-3.6-flash",
    timeout_s=30,
)
```

## Troubleshooting

### Common Issues

**"No LLM provider configured"**
- Set `LLM_API_KEY` and `LLM_BASE_URL` environment variables
- Or set provider-specific keys like `OPENAI_API_KEY`

**"Module not found: pytest"**
- Ensure you're using the correct Python environment
- Run `pip install pytest` in your active environment

**"Permission denied" on Windows**
- Run PowerShell as Administrator
- Or use `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

**Tests hang at exit**
- This is a known issue with some pytest plugin combinations
- Use `python -m pytest tests -q --ignore=tests/test_integration.py` for reliable runs

### Debug Mode

```bash
# Enable verbose logging
export ORCHESTRATOR_DEBUG=1

# Run with full tracebacks
python -m pytest tests -v --tb=long
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Contribution Guidelines

- Follow existing code style
- Add tests for new features
- Update documentation as needed
- Keep commits focused and atomic

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with Python 3.11+
- Uses PyYAML for configuration
- Designed for OpenAI-compatible APIs
- Inspired by durable execution patterns

## Support

- **Issues**: [GitHub Issues](https://github.com/sdageltc/letitloop/issues)
- **Discussions**: [GitHub Discussions](https://github.com/sdageltc/letitloop/discussions)
- **Email**: maintainers@letitloop.dev

## Roadmap

- [ ] Async/await support for concurrent operations
- [ ] Web UI for monitoring and management
- [ ] Plugin system for custom providers
- [ ] Distributed execution across multiple machines
- [ ] Real-time collaboration features

---

**Note**: This system is designed for autonomous operation. Always review AI-generated code before production use.
