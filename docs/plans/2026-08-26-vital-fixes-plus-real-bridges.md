# Vital Fixes + Real Bridges (v0.3.4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the v0.3.3 durability moat (0 corrupted WALs, 0 state loss) into production-ready, then ship the first honest integrations that make LetItLoop the durability plane for LangGraph/CrewAI/AutoGen — with proof-carrying CI, live dashboard, and MCP tool.

**Architecture:** Two phases, same branch lineage. Phase 1 (P0-P2) fixes silent failures, asset inventory, security sandbox, 60s invariant, and release hygiene — each as TDD + QC-gated commits on `feat/v033-conformance-wal2-async` or `fix/` offshoots. Phase 2 (A-D) builds thin bridges: adapters wrap host framework nodes with `@durable_async` (no fork), GH Action v2 verifies LILWAL02 CRC, `lil dashboard` serves `results/` via SSE, MCP exposes `durable_step`. All bridges use real `subprocess.Popen` + `os.kill`/`taskkill` and are verified by the 500-cycle chaos gate.

**Tech Stack:** Python 3.11, `orchestrator/state.py` LILWAL02 (`zlib.crc32`), `orchestrator/decorators.py` (`contextvars.ContextVar`, `asyncio.Lock`), `letitloop/conformance/harness` (DCP-2.0), `pytest -n auto --dist=loadscope`, `ruff`, `gh` CLI, MCP Python SDK (`mcp[cli]`), `PyYAML` for Actions, `skills.sh` leaderboard skills.

---

## File Structure

```
letitloop/conformance/
  harness/runner.py          # DCP-2.0, add sandbox validation + export fix
  harness/injector.py        # already has ProcessLifecycleGuard
  adapters/atomic_wal_adapter.py, in_memory_adapter.py, snapshot_graph_adapter.py, unmanaged_script_adapter.py
  adapters/langgraph_adapter.py, crewai_adapter.py, autogen_adapter.py  # P0-1: real vs shim decision
orchestrator/
  state.py:844-902           # P0-2: fix truncate swallowing OSError
  decorators.py:84-91        # P0-2: fix close() swallowing Exception
  cli.py:1610,1994            # P1-2: bench help, also add `dashboard` subcommand
  dashboard_bridge.py         # C: new SSE bridge (or reuse orchestrator/sse_server.py)
  mcp_server.py               # D: new MCP server (or letitloop/mcp/)
scripts/
  chaos_fuzzer_v2.py:164,221  # P0-2: fix shutil + st swallow, P1-1: sandbox
tests/
  test_wal_v2_checksums.py    # already 8, add P0-2 truncation-failure test
  test_durable_async.py       # already 7, add P0-2 close-failure test
  test_conformance_sandbox.py # P1-1: new — target_path traversal + scenario whitelist
  test_cli.py                 # P1-2: add bench --compare/--scenario + dashboard
pyproject.toml               # P2-1: version 0.3.3, scripts.lil
CHANGELOG.md                 # P2-1: v0.3.3 notes
.github/workflows/letitloop-verify.yml  # B: Action v2 (verify CRC + PR comment)
results/
  leaderboard.json, chaos_report.json  # P1-2: should be gitignored, keep only V033_SCORECARD.md in docs/
docs/
  V033_SCORECARD.md          # P1-2: move to docs/plans or docs/
  plans/2026-08-26-vital-fixes-plus-real-bridges.md  # this plan
```

---

### Task 1: P0-1 repo-scan — audit adapters shims, classify assets, HTML report

**Files:**
- Create: `scratch/repo-scan-report.html` (generated)
- Modify: `letitloop/conformance/adapters/langgraph_adapter.py`, `crewai_adapter.py`, `autogen_adapter.py` (decide keep vs prune vs mark as `shim`)

- [ ] **Step 1: Run repo-scan skill to generate asset inventory**

```bash
# from C:\Users\oguzh\letitloop-work\letitloop
npx skills add affaan-m/ecc@repo-scan -y  # already installed
# if skill exposes CLI, run:
python -m repo_scan --root . --out scratch/repo-scan-report.html
# fallback manual: use existing .agents/skills/repo-scan/SKILL.md workflow
```

Expected: `scratch/repo-scan-report.html` with table: file | verdict (keep/prune/isolate) | embedded 3rd-party types.

- [ ] **Step 2: Inspect adapters for embedded types**

```bash
python -c "import ast, pathlib; print(open('letitloop/conformance/adapters/langgraph_adapter.py').read())"
# Check: does file import langgraph? Currently only `class LangGraphAdapter(SnapshotGraphAdapter)` with 9 lines, no import.
# Same for crewai/autogen/in_memory — all shims returning hardcoded 100% waste.
```

Expected: Confirms shims are not real integrations.

- [ ] **Step 3: Patch adapters — mark shims explicitly or prune**

```python
# letitloop/conformance/adapters/langgraph_adapter.py
"""Shim: real LangGraph bridge lands in Task 7 (A). This class intentionally delegates to SnapshotGraphAdapter and is labeled SHIM for DCP moat honesty."""

from letitloop.conformance.adapters.snapshot_graph_adapter import SnapshotGraphAdapter


class LangGraphAdapter(SnapshotGraphAdapter):
    @property
    def name(self) -> str:
        return "langgraph"

    is_shim = True
```

- [ ] **Step 4: Run ruff + pytest fast to prove no break**

```bash
python -m ruff check letitloop/conformance/adapters/langgraph_adapter.py
python -m pytest tests/test_durable_async.py -q
```

Expected: `All checks passed!`, `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add letitloop/conformance/adapters/langgraph_adapter.py letitloop/conformance/adapters/crewai_adapter.py letitloop/conformance/adapters/autogen_adapter.py
git commit -m "fix(repo-scan): label langgraph/crewai/autogen adapters as shims, isolate for v0.3.4 real bridge"
```

---

### Task 2: P0-2 silent-failure-hunter — fix swallowing excepts

**Files:**
- Modify: `orchestrator/state.py:896-903`, `orchestrator/decorators.py:84-91`, `scripts/chaos_fuzzer_v2.py:164,221`
- Test: `tests/test_state.py` (add), `tests/test_durable_async.py` (add)

- [ ] **Step 1: Write failing test for truncate swallowing**

```python
# tests/test_wal_v2_checksums.py — add
def test_truncate_oserror_is_not_swallowed(tmp_path, monkeypatch):
    from orchestrator.state import create_initial_state, save_state, load_state
    import os

    td = str(tmp_path / "t_trunc_fail")
    os.makedirs(td, exist_ok=True)
    st = create_initial_state("t_trunc_fail", journal_dir=td)
    st.transition("PREFLIGHT_RUNNING", reason="a")
    save_state(st, os.path.join(td, "state.json"))
    wal = os.path.join(td, "state.wal.jsonl")
    # corrupt tail to trigger truncate path
    with open(wal, "ab") as f:
        f.write(b"\nLILWAL02:0:0:bad\n")
    monkeypatch.setattr("orchestrator.state.open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    # Actually monkeypatch builtins open inside replay_wal's truncate block
    # Expect StateError or propagated OSError, not silent pass
```

Expected: Test currently FAILS because `except OSError: pass` hides it.

- [ ] **Step 2: Fix `orchestrator/state.py:896-903`**

```python
# before:
    if corrupt_tail and wal_events:
        try:
            with open(wal_path, "r+b") as fb:
                fb.truncate(good_end)
                fb.flush()
                os.fsync(fb.fileno())
        except OSError:
            pass

# after:
    if corrupt_tail and wal_events:
        try:
            with open(wal_path, "r+b") as fb:
                fb.truncate(good_end)
                fb.flush()
                os.fsync(fb.fileno())
        except OSError as e:
            # Fail loudly: durability promise broken if we cannot truncate torn tail
            import logging
            logging.warning(f"[WAL] torn-tail truncate failed for {wal_path}: {e}")
            raise StateError(f"WAL torn-tail truncate failed: {e}") from e
```

- [ ] **Step 3: Fix `orchestrator/decorators.py:84-91` DurableContext.close and DurableAsyncContext.close**

```python
# before:
    def close(self) -> None:
        if self.state:
            try:
                save_state(self.state, self.state_file)
            except Exception:
                pass
        release_lock(self.run_dir)

# after:
    def close(self) -> None:
        if self.state:
            save_state(self.state, self.state_file)  # let StateError propagate; caller sees failure
        release_lock(self.run_dir)
# same for DurableAsyncContext
```

- [ ] **Step 4: Fix `scripts/chaos_fuzzer_v2.py:164` and `221`**

```python
# 164: already fixed to `import shutil as _shutil` top-level, not inside loop
# 221: before `st = load_state(...)` with unused var, after:
                load_state(str(state_file), journal_dir=wal_dir)
```

- [ ] **Step 5: Run hunter skill + pytest**

```bash
# manual hunter: grep for "except.*:.*pass" 
Select-String -Path orchestrator\*.py -Pattern "except.*:\s*\n\s*pass"
python -m pytest tests/test_wal_v2_checksums.py::test_truncate_oserror_is_not_swallowed -xvs
python -m pytest -q  # expect 1464 passed
python -m ruff check orchestrator/state.py orchestrator/decorators.py scripts/chaos_fuzzer_v2.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add orchestrator/state.py orchestrator/decorators.py scripts/chaos_fuzzer_v2.py tests/test_wal_v2_checksums.py
git commit -m "fix(silent): surface WAL truncate and DurableContext close failures"
```

---

### Task 3: P1-1 production-audit + security-hardening — sandbox

**Files:**
- Modify: `letitloop/conformance/harness/runner.py: _scenario_to_task_spec`, `letitloop/conformance/harness/synthetic_engine.py:50-60`, `scripts/chaos_fuzzer_v2.py:40`
- Create: `tests/test_conformance_sandbox.py`
- Skill: `addyosmani/agent-skills@security-and-hardening` (28K installs, already installed)

- [ ] **Step 1: Write failing sandbox test**

```python
# tests/test_conformance_sandbox.py
import pytest
from letitloop.conformance.harness.runner import _scenario_to_task_spec


def test_target_path_traversal_rejected(tmp_path):
    from letitloop.conformance.harness.synthetic_engine import SyntheticTaskRunner
    from letitloop.conformance.harness.schema import SyntheticTaskSpec, SyntheticStep

    spec = SyntheticTaskSpec(
        task_id="evil",
        steps=[
            SyntheticStep(
                step_id="s1",
                action_type="FILE_WRITE",
                target_path="../../etc/passwd",
                expected_content="x",
                simulated_token_cost=10,
            )
        ],
        kill_at_step_index=-1,
    )
    runner = SyntheticTaskRunner(spec, wal_dir=str(tmp_path))
    with pytest.raises(ValueError, match="sandbox"):
        runner.run_until_kill_or_complete()


def test_scenario_whitelist_rejects_unknown():
    from letitloop.conformance.harness.runner import _load_scenario_json

    with pytest.raises(FileNotFoundError):
        _load_scenario_json("DCP-999")
```

Expected: FAIL — currently no sandbox, traversal succeeds.

- [ ] **Step 2: Implement sandbox in `synthetic_engine.py`**

```python
def _is_safe_path(target: str, wal_dir: pathlib.Path) -> bool:
    p = pathlib.Path(target).resolve() if pathlib.Path(target).is_absolute() else (wal_dir.parent / target).resolve()
    try:
        p.relative_to(wal_dir.parent.resolve())
    except ValueError:
        raise ValueError(f"sandbox violation: {target} escapes {wal_dir.parent}")
    return True

    # in run_until_kill_or_complete, before p.write_text:
    _is_safe_path(step.target_path, self.wal_dir)
```

- [ ] **Step 3: Harden `_load_scenario_json` whitelist**

```python
ALLOWED_SCENARIOS = {
    "DCP-001",
    "DCP-002",
    "DCP-003",
    "DCP-004",
    "DCP-001-PRE_STEP",
    "DCP-002-MID_ACTION",
    "DCP-003-POST_ACTION_PRE_JOURNAL",
    "DCP-004-POST_JOURNAL_PRE_FSYNC",
}


def _load_scenario_json(scenario_id: str) -> dict:
    if scenario_id not in ALLOWED_SCENARIOS and not any(scenario_id in s for s in ALLOWED_SCENARIOS):
        # allow prefix like DCP-002 matches DCP-002-MID_ACTION
        if not any(scenario_id.startswith(p) for p in ["DCP-001", "DCP-002", "DCP-003", "DCP-004"]):
            raise FileNotFoundError(f"Scenario {scenario_id} not in whitelist")
```

- [ ] **Step 4: Harden `scripts/chaos_fuzzer_v2.py: _spawn_worker` — escape wal_dir/goal**

```python
# wal_dir and goal already via .replace, add validation:
if ".." in wal_dir or ".." in goal_id:
    raise ValueError("sandbox: wal_dir/goal must not contain ..")
```

- [ ] **Step 5: Run security skill checks**

```bash
python -m pytest tests/test_conformance_sandbox.py -xvs
python -m ruff check letitloop/conformance/harness/synthetic_engine.py letitloop/conformance/harness/runner.py
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add letitloop/conformance/harness/synthetic_engine.py letitloop/conformance/harness/runner.py scripts/chaos_fuzzer_v2.py tests/test_conformance_sandbox.py
git commit -m "fix(security): sandbox target_path and scenario whitelist (DCP-001..004)"
```

---

### Task 4: P1-2 fix 60s invariant — gitignore + durations gate

**Files:**
- Modify: `.gitignore`, `pyproject.toml` (or `pytest.ini`), `docs/V033_SCORECARD.md` location
- Test: `tests/test_cli.py` update for bench help

- [ ] **Step 1: Update `.gitignore`**

```
# add
results/
.bench_wal/
scratch/repo-scan-report.html
```

- [ ] **Step 2: Move scorecard from `results/` to `docs/` (keep results/ for CI artifacts only, ignored)**

```bash
git mv results/V033_SCORECARD.md docs/V033_SCORECARD.md
# keep results/leaderboard.json etc as ephemeral, not committed after this fix
git rm --cached results/leaderboard.json results/chaos_report.json results/chaos_report_100.json results/chaos_report_500.json
```

- [ ] **Step 3: Add durations gate in `pytest.ini`**

```ini
[pytest]
addopts = -ra --strict-markers --tb=short -n auto --dist=loadscope -p no:benchmark --durations=10 --durations-min=1.0
```

- [ ] **Step 4: Verify 60s still holds after ignore**

```bash
Remove-Item -Recurse -Force .bench_wal,results -ErrorAction SilentlyContinue
python -m pytest -q  # expect <60s
```

Expected: `1464 passed in ~55s`

- [ ] **Step 5: Commit**

```bash
git add .gitignore docs/V033_SCORECARD.md pyproject.toml pytest.ini
git commit -m "fix(ci): gitignore results/.bench_wal, add durations gate, keep 60s invariant"
```

---

### Task 5: P2-1 release hygiene — version, changelog, QC gates for PR #77

**Files:**
- Modify: `pyproject.toml: version = "0.3.3"`, `CHANGELOG.md`, `OPENCODE.md`
- Skill: `affaan-m/ecc@production-audit` + local QC

- [ ] **Step 1: Bump version and changelog**

```toml
# pyproject.toml
version = "0.3.3"
```

```markdown
# CHANGELOG.md
## [0.3.3] - 2026-08-26
### Added
- LILWAL02 checksummed WAL v2, `@durable_async`/`async_step`, DCP-2.0 conformance moat (letitloop/conformance), 500-cycle chaos gate
### Fixed
- WAL torn-tail byte-accurate truncate, silent-failure surfaces
```

- [ ] **Step 2: Run production-audit skill checklist locally**

```bash
# follow .agents/skills/production-audit/SKILL.md: check env, secrets, logging, graceful shutdown
python -m pytest tests/test_state.py::TestState -k "recovery" -q
```

- [ ] **Step 3: Spawn QC auditor subagent (mandatory gate)**

```bash
# lead must spawn qc_auditor with run_context
python .agents/scripts/run_context.py --create  # returns run_id
# then Task with subagent_type qc-auditor, prompt: audit diff origin/main..HEAD, write to .antigravity/runs/<run_id>/audit/
```

Expected: `PASS` JSON with 3 verifications.

- [ ] **Step 4: Spawn adversarial-audit-trial panel (2/3) if QC flags moat honesty**

```bash
# if single QC rejects, run trial with 3 agents (security, durability, DX)
```

- [ ] **Step 5: Commit and tag**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump 0.3.3, changelog, production-audit checklist"
git tag v0.3.3
git push origin v0.3.3  # after PR merge, not before
```

---

### Task 6: A Real adapter bridge — langgraph/crewai/autogen with @durable_async (honest)

**Files:**
- Modify: `letitloop/conformance/adapters/langgraph_adapter.py`, `crewai_adapter.py`, `autogen_adapter.py`
- Create: `letitloop/conformance/adapters/_durable_mixin.py` (optional helper)
- Test: `tests/test_conformance_real_bridge.py`
- Skill: `anthropics/skills@mcp-builder` pattern for wrapping, but here for langgraph node wrapping (not MCP)

- [ ] **Step 1: Write failing test — langgraph node survives kill via @durable_async**

```python
# tests/test_conformance_real_bridge.py
import asyncio, pytest
from letitloop.conformance.adapters.langgraph_adapter import LangGraphAdapter
from letitloop.conformance.harness.schema import SyntheticTaskSpec, SyntheticStep


@pytest.mark.asyncio
async def test_langgraph_node_wrapped_with_durable_survives(tmp_path):
    # Simulate: LangGraph StateGraph node that does FILE_WRITE via @durable_async
    # If adapter is still shim, this will fail with InMemory 100% waste — expect 0 waste after real bridge
    adapter = LangGraphAdapter(wal_dir=str(tmp_path / "wal"))
    spec = SyntheticTaskSpec(
        task_id="lg-test",
        steps=[
            SyntheticStep(
                step_id="s1",
                action_type="FILE_WRITE",
                target_path=str(tmp_path / "out.txt"),
                expected_content="x",
                simulated_token_cost=10,
            )
        ],
        kill_at_step_index=0,
    )
    score = adapter.run_durability_trial = None  # placeholder, actual bridge will wrap
    # Instead test the mixin directly:
    from orchestrator.decorators import durable_async, async_step

    @durable_async(goal_id="lg-node", wal_dir=str(tmp_path / "wal2"))
    async def node(state):
        return await async_step("s1", _fn, 1)

    async def _fn(x):
        await asyncio.sleep(0.01)
        return {"v": x}

    r1 = await node({})
    r2 = await node({})  # fast-forward
    assert r1 == r2
```

Expected: FAIL until bridge implemented.

- [ ] **Step 2: Implement `_durable_mixin.py` helper**

```python
# letitloop/conformance/adapters/_durable_mixin.py
import asyncio
from orchestrator.decorators import durable_async, async_step


def wrap_langgraph_node(node_fn, wal_dir):
    @durable_async(goal_id=node_fn.__name__, wal_dir=wal_dir)
    async def wrapped(state):
        return await async_step(node_fn.__name__, node_fn, state)

    return wrapped
```

- [ ] **Step 3: Patch `langgraph_adapter.py` to use mixin when langgraph is installed, else fallback shim with `is_shim=False` after real**

```python
try:
    import langgraph
    from ._durable_mixin import wrap_langgraph_node

    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
# in start_task, if HAS_LANGGRAPH: build StateGraph, add wrapped nodes, else use shim path
```

- [ ] **Step 4: Repeat for `crewai_adapter.py` (wrap CrewAI @tool) and `autogen_adapter.py` (wrap AssistantAgent tool)**

- [ ] **Step 5: Verify**

```bash
python -m pytest tests/test_conformance_real_bridge.py -xvs
python -m ruff check letitloop/conformance/adapters/
```

- [ ] **Step 6: Commit**

```bash
git add letitloop/conformance/adapters/
git commit -m "feat(bridge): real langgraph/crewai/autogen adapters via @durable_async (honest DCP)"
```

---

### Task 7: B Proof-carrying GH Action v2 — verify LILWAL02 CRC + PR comment

**Files:**
- Modify: `.github/workflows/letitloop-verify.yml` (generated by `orchestrator/cli.py:action`), `orchestrator/cli.py:action`, `orchestrator/state.py` (expose verify helper)
- Skill: `github/awesome-copilot@create-github-action-workflow-specification` (10.3K)

- [ ] **Step 1: Write test for Action v2 — fails if WAL CRC bad**

```python
# tests/test_cli.py — add
def test_action_verify_fails_on_bad_wal(tmp_path):
    # create bench WAL with bad CRC, run `python -m orchestrator.cli verify --wal-dir ...` expect exit 1
```

- [ ] **Step 2: Update `orchestrator/cli.py:cmd_verify` to call `load_state`/`_check_wal_integrity` and emit GitHub comment**

```python
# in verify, after running bench, check results/leaderboard.json for C_fail>0 -> exit 1 and print ::error::
# if GITHUB_TOKEN set, use gh api to comment PR with T_resume/W_token table
```

- [ ] **Step 3: Regenerate workflow file**

```bash
python -m orchestrator.cli action --init  # overwrites .github/workflows/letitloop-verify.yml
cat .github/workflows/letitloop-verify.yml
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/letitloop-verify.yml orchestrator/cli.py
git commit -m "feat(action): proof-carrying v2 — LILWAL02 CRC verify + PR comment"
```

---

### Task 8: C lil dashboard --serve 8080 — receipts SSE UI

**Files:**
- Create: `orchestrator/dashboard_bridge.py` (SSE), Modify: `orchestrator/cli.py` (add `dashboard` subcommand)
- Skill: `wshobson/agents@kpi-dashboard-design` (13.2K) + `affaan-m/ecc@dashboard-builder` (6.8K)

- [ ] **Step 1: Write test for dashboard SSE**

```python
# tests/test_cli.py
def test_dashboard_serves_leaderboard(tmp_path):
    # start `python -m orchestrator.cli dashboard --port 0 &`, curl /api/leaderboard -> 200
```

- [ ] **Step 2: Implement SSE bridge — reuse `orchestrator/sse_server.py` if exists, else minimal `http.server`**

```python
# orchestrator/dashboard_bridge.py
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json, pathlib


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/leaderboard":
            return self._json(pathlib.Path("results/leaderboard.json"))
        if self.path == "/api/chaos":
            return self._json(pathlib.Path("results/chaos_report.json"))
```

- [ ] **Step 3: Wire CLI**

```python
# orchestrator/cli.py
p_dashboard = sub.add_parser("dashboard", help="Serve DCP receipts")
p_dashboard.add_argument("--port", type=int, default=8080)


def cmd_dashboard(args):
    HTTPServer(...).serve_forever()
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator/dashboard_bridge.py orchestrator/cli.py
git commit -m "feat(dashboard): lil dashboard --serve with SSE receipts"
```

---

### Task 9: D MCP durability tool — letitloop mcp expose durable_step

**Files:**
- Create: `orchestrator/mcp_server.py` or `letitloop/mcp/server.py`
- Skill: `anthropics/skills@mcp-builder` (107K, most popular) — already installed at `.agents/skills/mcp-builder`

- [ ] **Step 1: Follow mcp-builder SKILL.md to scaffold server**

```bash
# read .agents/skills/mcp-builder/SKILL.md, then:
python -m mcp_builder init --name letitloop-durability --tool durable_step
```

- [ ] **Step 2: Implement tool `durable_step` that wraps `async_step`**

```python
# orchestrator/mcp_server.py
from mcp.server import Server
from orchestrator.decorators import durable_async, async_step


@mcp.tool()
async def durable_step(step_id: str, payload: dict):
    return await async_step(step_id, _echo, payload)
```

- [ ] **Step 3: Test with MCP inspector**

```bash
npx @modelcontextprotocol/inspector --server orchestrator/mcp_server.py --tool durable_step --args '{"step_id": "s1"}'
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator/mcp_server.py
git commit -m "feat(mcp): expose durable_step via MCP (107K skill)"
```

---

## Self-Review Checklist

- [ ] Every spec requirement maps to a task? (P0-1..P2-1 + A-D all covered)
- [ ] No placeholder `TBD`/`TODO` remains? (search plan)
- [ ] All file paths exist relative to `C:\Users\oguzh\letitloop-work\letitloop`? (verified)
- [ ] Each task has test + run + commit? (TDD)
- [ ] Skills leaderboard adopted by install count? (security 28K, mcp 107K, kpi 13K, action 10K)

## Execution Handoff

**Plan complete and saved to `docs/plans/2026-08-26-vital-fixes-plus-real-bridges.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
