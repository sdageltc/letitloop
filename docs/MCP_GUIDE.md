# Model Context Protocol (MCP) Guide for `letitloop`

The `letitloop` Model Context Protocol (MCP) server allows AI assistants (Claude Desktop, Cursor, Antigravity, OpenCode, and custom MCP clients) to autonomously orchestrate macro-tasks, manage contracts, run verification suites, and conduct multi-lens quality reviews.

---

## 🚀 Quick Setup

### 1. Claude Desktop Integration

Add the following to your `claude_desktop_config.json`:

#### On macOS (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"],
      "cwd": "/path/to/your/workspace"
    }
  }
}
```

#### On Windows (`%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "letitloop": {
      "command": "python",
      "args": ["-m", "orchestrator.mcp_server"],
      "cwd": "C:\\workspace\\project"
    }
  }
}
```

---

### 2. Cursor IDE Integration

In Cursor Settings -> **Features** -> **MCP Servers** -> **Add New MCP Server**:
- **Name**: `letitloop`
- **Type**: `command`
- **Command**: `letitloop-mcp` (or `python -m orchestrator.mcp_server`)

---

## 🛠️ Exposed MCP Tools

| Tool Name | Parameters | Description |
|---|---|---|
| `create_goal` | `goal_id`, `title`, `description` | Initialize a new autonomous macro-goal |
| `load_contract_file` | `contract_path` | Load, parse, and validate a contract JSON file |
| `run_contract_verification` | `contract_path`, `task_id` | Execute deterministic acceptance checks |
| `run_quality_review` | `contract_path`, `lens` | Run multi-lens quality plane review |
| `execute_supervisor_plan` | `goal_id`, `contracts`, `parallel` | Execute DAG plan using Supervisor with automated retries |
| `inspect_task_state` | `task_id`, `run_dir` | Inspect state journal and evidence store |
| `reconcile_workspace_state` | `goal_id`, `contracts` | Audit file hashes and evidence ledger |
| `get_system_health` | none | Query engine health and runtime status |

---

## 💻 Example Usage via MCP

Once connected, your AI assistant can run commands naturally:

> **User Prompt**: "Please initialize a goal `refactor_auth` and run contract verification on `contracts/auth.json`."

The assistant will invoke:
1. `create_goal(goal_id="refactor_auth", title="Refactor Authentication Engine")`
2. `run_contract_verification(contract_path="contracts/auth.json")`
3. Return the exact pass/fail acceptance report directly in the chat.
