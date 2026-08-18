# Model Context Protocol (MCP) & Agent Integration Guide

The `letitloop` Model Context Protocol (MCP) server (`letitloop-mcp`) allows AI assistants and coding agents across the entire 2026 agent ecosystem (**Claude Code**, **OpenAI Codex**, **Cursor**, **Google Antigravity**, **Hermes Agent**, **OpenCode**, **Cline**, and **Windsurf**) to autonomously orchestrate macro-tasks, manage contracts, run verification suites, and conduct multi-lens quality reviews.

---

## 🚀 Quick Setup by Environment

### 1. Claude Code
Add the MCP server directly via CLI:
```bash
claude mcp add letitloop -- python -m orchestrator.mcp_server
```
Or in `~/.claude.json` / `.claude/mcp.json`:
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"]
    }
  }
}
```

---

### 2. OpenAI Codex Integration
Add the MCP server via Codex CLI:
```bash
codex mcp add letitloop -- letitloop-mcp
```
Or in `~/.codex/config.toml` / `.codex/config.toml`:
```toml
[mcp_servers.letitloop]
command = "letitloop-mcp"
args = []
```
Or in `~/.codex/mcp.json` / `.codex/mcp.json`:
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "letitloop-mcp",
      "env": {
        "WORKER_MODEL": "openai:gpt-5.6-luna",
        "QC_MODEL": "openai:gpt-5.6-sol"
      }
    }
  }
}
```

---

### 3. Cursor IDE Integration
In Cursor Settings -> **Features** -> **MCP Servers** -> **Add New MCP Server**:
- **Name**: `letitloop`
- **Type**: `command`
- **Command**: `letitloop-mcp` (or `python -m orchestrator.mcp_server`)

---

### 4. Google Antigravity Integration
Add to your Antigravity MCP configuration (`~/.gemini/antigravity/mcp/letitloop/` or project `mcp.json`):
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "letitloop-mcp",
      "args": [],
      "env": {
        "WORKER_MODEL": "gemini:gemini-3.6-flash",
        "QC_MODEL": "gemini:gemini-3.1-pro"
      }
    }
  }
}
```

---

### 5. OpenCode Integration
In `~/.config/opencode/config.json` or `.opencode/mcp.json`:
```json
{
  "mcp": {
    "letitloop": {
      "command": "letitloop-mcp",
      "env": {
        "WORKER_MODEL": "openai:gpt-5.6-luna",
        "QC_MODEL": "openai:gpt-5.6-sol"
      }
    }
  }
}
```

---

### 6. Hermes Agent (Nous Research)
In `~/.hermes/config.json` or `.hermes/mcp.json`:
```json
{
  "mcp_servers": {
    "letitloop": {
      "command": "letitloop-mcp"
    }
  }
}
```

---

### 7. Cline & Windsurf
Add to Cline MCP settings or `.cline/mcp.json` / `.windsurf/mcp.json`:
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"]
    }
  }
}
```

---

### 8. Omniroute Gateway Integration
If using Omniroute or a local OpenAI-compatible proxy:
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "letitloop-mcp",
      "env": {
        "OMNIROUTE_BASE_URL": "http://localhost:8000/v1",
        "WORKER_MODEL": "omniroute:auto"
      }
    }
  }
}
```

---

## 🛠️ Exposed MCP Tools (8 Native Capabilities)

| Tool Name | Parameters | Description |
|---|---|---|
| `create_goal` | `goal_id`, `title`, `description` | Initialize a new autonomous macro-goal with isolated tracking |
| `load_contract_file` | `contract_path` | Load, parse, and validate a contract JSON specification |
| `run_contract_verification` | `contract_path`, `task_id` | Execute deterministic acceptance checks (AST syntax, regex, unit tests) |
| `run_quality_review` | `contract_path`, `lens` | Run multi-lens quality review (*code_correctness*, *security*, *tests*) |
| `execute_supervisor_plan` | `goal_id`, `contracts`, `parallel` | Execute DAG plan using Supervisor with automated retries and WAL logging |
| `inspect_task_state` | `task_id`, `run_dir` | Inspect state journal, attempt history, and evidence artifacts |
| `reconcile_workspace_state` | `goal_id`, `contracts` | Audit file hashes and verify evidence ledger integrity |
| `get_system_health` | none | Query runtime engine health and registered worker adapters |

---

## 💻 Example Usage via MCP

Once connected, your AI assistant can invoke tools conversationally:

> **User Prompt**: "Please initialize a goal `refactor_auth` and run contract verification on `contracts/auth.json`."

The assistant invokes:
1. `create_goal(goal_id="refactor_auth", title="Refactor Authentication Engine")`
2. `run_contract_verification(contract_path="contracts/auth.json")`
3. Returns a structured verification report with pass/fail exit codes directly in the chat interface.

---

## 🔒 Safety & Rate-Limit Protections
- **No Direct Key Exposure**: API keys and OAuth tokens are read only from the host environment.
- **Fail-Closed Execution**: Invalid contracts or sandbox violations halt immediately without mutating files.
- **Bounded Worker Retries**: Hard-capped 3-strike loops prevent runaway API credit consumption.
