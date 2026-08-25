# Dual Self-Healing Architecture (Layer A + Layer B)

The LetItLoop ecosystem enforces a strict **Decoupled Self-Healing & Durability Topology**.

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                DUAL SELF-HEALING TOPOLOGY                                │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│   Layer A: Local Pre-Push AutoHealer (`lil heal`)                                        │
│   ──────────────────────────────────────────────                                         │
│   • Runs locally before `git push`                                                       │
│   • Executes AST syntax validation & ruff lint/formatting auto-fix                       │
│   • Runs local test suite to guarantee $LASTEXITCODE == 0                                │
│   • 0 cloud cost, 0 CI minute burn, 100% green commits                                   │
│                                                                                          │
│   Layer B: Cloud CI Watchdog Daemon (`lil watchdog`)                                     │
│   ──────────────────────────────────────────────────                                     │
│   • Runs out-of-band on Azure B1s ($100 Student Credits) or Oracle Ampere A1 (Free)      │
│   • Listens for failing GitHub Actions runs (`workflow_run` failure events)              │
│   • Clones failing branch in an isolated sandbox, runs AutoHealer, and verifies tests    │
│   • Dispatches verified fix branch and opens Pull Request                                │
│                                                                                          │
│   The Pure Judge: `letitloop-action` in GitHub Actions                                   │
│   ────────────────────────────────────────────────────                                   │
│   • Zero external dependencies, zero LLM calls, zero API keys                           │
│   • Sub-second deterministic AST & HMAC-SHA256 proof receipt verification                 │
│   • Fails closed on unauthorized regressions without polluting the gate                  │
│                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Layer A: Local Pre-Push AutoHealer (`lil heal`)

To automatically heal formatting, lint errors, and test regressions before pushing:

```bash
# Run auto-healer on current directory
lil heal --dir .

# Run with custom pytest test arguments
lil heal --dir . --test-args "tests/test_core.py -v"

# JSON output for IDE / Agent integration
lil heal --dir . --json
```

### Programmatic Python Usage
```python
from orchestrator.auto_healer import AutoHealer

healer = AutoHealer(workspace_dir=".", max_iterations=3, run_ruff=True, run_pytest=True)
result = healer.heal()

if result.success:
    print(f"Healed in {result.iterations} iterations!")
else:
    print(f"Unresolved errors: {result.remaining_errors}")
```

---

## 2. Layer B: Cloud CI Watchdog Daemon (`lil watchdog`)

To run the autonomous out-of-band watchdog on an **Azure B1s VM** or **Oracle Cloud Infrastructure (OCI Always Free)**:

```bash
# Query and inspect recent failed CI runs
lil watchdog --repo sdageltc/letitloop --limit 5

# JSON telemetry output
lil watchdog --repo sdageltc/letitloop --json
```

### VM Deployment (Docker & Systemd)

1. **Docker Container Deployment**:
   ```bash
   docker build -t letitloop-watchdog -f deploy/Dockerfile .
   docker run -d --restart always --name letitloop-watchdog \
     -e GITHUB_TOKEN="ghp_xxx" \
     letitloop-watchdog
   ```

2. **Systemd Daemon (`deploy/letitloop-watchdog.service`)**:
   ```bash
   sudo cp deploy/letitloop-watchdog.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now letitloop-watchdog
   ```
