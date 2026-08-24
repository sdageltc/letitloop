# Contributing to letitloop

First off: thank you. Every PR here gets reviewed by a human (currently one) within 48 hours, and first-time contributors get active pairing - not just a review stamp.

## The `/claim` workflow

1. Browse the [good first issues](https://github.com/sdageltc/letitloop/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) or any open issue.
2. Comment `/claim` on the issue you want. This reserves it for you for 7 days and gets you a maintainer reply with pointers (relevant files, gotchas, test strategy).
3. Fork, create a feature branch (`feat/your-thing` or `fix/your-thing`), and open a **draft PR early** - early drafts get design feedback before you invest.
4. Check your boxes, request review, and we merge.

## Local setup

```bash
git clone https://github.com/sdageltc/letitloop.git
cd letitloop
pip install -e ".[dev]"
python -m pytest tests/ -q            # expect: all green, ~3 min
python -m ruff check orchestrator tests
python -m ruff format --check orchestrator tests
```

Python 3.11 or 3.12. No API keys required - the entire suite runs offline with mocked workers.

## Ground rules

- **Every behavior change needs a test.** The suite is the project's contract; 1,400+ tests and counting.
- **Lint gates are CI gates.** `ruff check` and `ruff format --check` must pass - match the existing style and you're 95% there.
- **Zero-dependency philosophy.** New runtime dependencies need a strong justification (stdlib first, always). Dev/test deps are fine.
- **One logical change per PR.** Small PRs merge in hours; big PRs merge in weeks.
- **No secrets, ever.** The zero-leak privacy audit is part of CI culture here. If you paste a token in a test fixture, the review will catch it - but don't.

## Architecture orientation (5-minute version)

- `orchestrator/supervisor/` - the execution engine: state machine, recovery, reporting (see `docs/adr/0001`)
- `orchestrator/qc/` - the multi-lens quality plane (personas, parsing, aggregation, runner)
- `orchestrator/contract.py` + `goal.py` - the typed contract/goal schemas; everything hangs off these
- `orchestrator/verifier.py` - deterministic acceptance checks (see `docs/adr/0002`)
- `orchestrator/worker_adapters.py` - pluggable agent backends (Claude Code, Codex, Ollama, Docker sandbox, ...)
- `docs/adr/` - why the system looks the way it does. Read ADR-0001 and 0002 before proposing architecture changes.

## Where help is needed most

See the [community issues](https://github.com/sdageltc/letitloop/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) - JSON schemas, shell completions, a Gemini CLI adapter, the CI reusable workflow, and the web dashboard are all open with acceptance criteria written.

## Reporting bugs

Open an issue with: what you ran, what happened, what you expected, and the run directory contents (`.letitloop/runs/<goal>/`) minus anything private. The WAL journal + evidence ledger usually makes debugging fast.
