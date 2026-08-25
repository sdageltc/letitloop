# Contributing to letitloop

Thank you for your interest in contributing to letitloop! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Code Style](#code-style)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Features](#suggesting-features)
- [License](#license)

## Code of Conduct

This project and everyone participating in it is governed by our Code of Conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior via LinkedIn at [https://www.linkedin.com/in/oguzhankayan/](https://www.linkedin.com/in/oguzhankayan/).

## Getting Started

### Prerequisites

- Python 3.11 or higher
- pip (Python package installer)
- Git
- An LLM API key (OpenAI, Anthropic, Gemini, DeepSeek, or any OpenAI-compatible provider)

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/sdageltc/letitloop.git
   cd letitloop
   ```

## Development Setup

### Install Development Dependencies

```bash
# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate  # Windows

# Install the package in development mode
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### Environment Variables

Set up your development environment variables:

```bash
# Linux/macOS
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://api.openai.com/v1"

# Windows (PowerShell)
$env:LLM_API_KEY="your-api-key"
$env:LLM_BASE_URL="https://api.openai.com/v1"
```

### Run the Test Suite

```bash
# Run all tests (excluding integration tests)
python -m pytest tests -q --ignore=tests/test_integration.py

# Run fast tests only
python -m pytest tests -q -m fast

# Run with coverage
python -m pytest tests --cov=orchestrator --cov-report=html
```

## Code Style

### Formatting

- **Line length**: 120 characters maximum
- **Formatter**: We use `ruff format` for consistent formatting
- **Linter**: We use `ruff check` for linting

### Running Code Quality Tools

```bash
# Format code
ruff format orchestrator tests

# Lint code
ruff check orchestrator tests

# Fix auto-fixable issues
ruff check --fix orchestrator tests
```

### Style Guidelines

1. **Type Hints**: Use type hints for all public function signatures
2. **Docstrings**: Include docstrings for all public classes and functions
3. **Imports**: Use absolute imports and group them properly
4. **Naming**: Follow PEP 8 naming conventions
5. **Comments**: Add comments for complex logic, but prefer self-documenting code

### Example Code Style

```python
"""Module for task orchestration."""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass


@dataclass
class Task:
    """A task to be executed by the orchestrator.
    
    Attributes:
        task_id: Unique identifier for the task.
        objective: Description of what the task should accomplish.
        outputs: List of output files expected.
    """
    
    task_id: str
    objective: str
    outputs: List[Dict[str, str]]
    
    def validate(self) -> bool:
        """Validate the task configuration.
        
        Returns:
            True if the task is valid, False otherwise.
            
        Raises:
            ValueError: If the task configuration is invalid.
        """
        if not self.task_id:
            raise ValueError("task_id cannot be empty")
        return True
```

## Testing

### Writing Tests

- Place tests in the `tests/` directory
- Use descriptive test names that explain what is being tested
- Follow the Arrange-Act-Assert pattern
- Mock external dependencies when appropriate
- Use fixtures for common setup

### Test Categories

- **Fast tests** (`@pytest.mark.fast`): Pure logic tests, no external dependencies
- **Integration tests** (`@pytest.mark.integration`): Tests that may use subprocesses
- **Proof tests** (`@pytest.mark.proof`): End-to-end tests with real LLM calls
- **Live tests** (`@pytest.mark.live`): Tests requiring API keys (opt-in)

### Running Specific Tests

```bash
# Run a specific test file
python -m pytest tests/test_worker.py

# Run a specific test function
python -m pytest tests/test_worker.py::test_worker_execution

# Run tests matching a pattern
python -m pytest tests -k "worker"
```

### Writing Integration Tests

```python
import pytest
from orchestrator.worker import run_worker


@pytest.mark.integration
def test_worker_with_mock(monkeypatch):
    """Test worker with mocked LLM calls."""
    monkeypatch.setenv("FAKE_WORKER", "1")

    result = run_worker(contract, workspace_root, run_dir)
    assert result["success"] is True
```

## Pull Request Process

### Before Submitting

1. **Ensure tests pass**: Run the full test suite
2. **Update documentation**: Add or update documentation as needed
3. **Follow code style**: Run linters and formatters
4. **Add tests**: Include tests for new features or bug fixes
5. **Update changelog**: Add a brief description of changes

### PR Template

```markdown
## Description
Brief description of the changes.

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Testing
- [ ] New tests added
- [ ] Existing tests pass
- [ ] Manual testing performed

## Checklist
- [ ] Code follows the project's style guidelines
- [ ] Documentation has been updated
- [ ] Changes have been tested locally
- [ ] Any dependent changes have been merged and published
```

### Review Process

1. Submit your PR with a clear description
2. Wait for CI checks to pass
3. Address any review comments
4. Once approved, your PR will be merged

## Reporting Bugs

### Bug Report Template

```markdown
**Describe the bug**
A clear and concise description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior:
1. Go to '...'
2. Run '...'
3. See error

**Expected behavior**
A clear and concise description of what you expected to happen.

**Screenshots**
If applicable, add screenshots to help explain your problem.

**Environment:**
- OS: [e.g. Windows 10, macOS 12]
- Python version: [e.g. 3.11.0]
- Package version: [e.g. 0.1.0]

**Additional context**
Add any other context about the problem here.
```

## Suggesting Features

### Feature Request Template

```markdown
**Is your feature request related to a problem?**
A clear and concise description of what the problem is.

**Describe the solution you'd like**
A clear and concise description of what you want to happen.

**Describe alternatives you've considered**
A clear and concise description of any alternative solutions or features you've considered.

**Additional context**
Add any other context or screenshots about the feature request here.
```

## License

By contributing to letitloop, you agree that your contributions will be licensed under the MIT License.

## Questions?

If you have questions about contributing, feel free to:

1. Open a [discussion](https://github.com/sdageltc/letitloop/discussions)
2. Reach out on LinkedIn at [https://www.linkedin.com/in/oguzhankayan/](https://www.linkedin.com/in/oguzhankayan/)
3. Check out our [documentation](https://github.com/sdageltc/letitloop#readme)

Thank you for contributing to letitloop!
