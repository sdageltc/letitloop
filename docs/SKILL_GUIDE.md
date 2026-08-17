# Agent Skill Integration Guide for `letitloop`

You can use `letitloop` as a **Universal Agent Skill** across **Claude Code**, **Google Antigravity**, **Cursor**, and **OpenCode**.

When installed as a skill, the Lead Model (e.g. **Claude Opus 5** in Claude Code or **Gemini 3.1 Pro** in Antigravity) acts as the high-level architect while `letitloop` acts as the underlying deterministic engine.

---

## ⚡ 1-Click Skill Installation

From the `letitloop` repository directory, run:

```bash
# Install into all detected AI assistant environments
python skill/install_skill.py

# Or install for a specific assistant:
python skill/install_skill.py --target claude        # Claude Code (~/.claude/skills/)
python skill/install_skill.py --target antigravity   # Google Antigravity (~/.gemini/antigravity/builtin/skills/)
python skill/install_skill.py --target cursor        # Cursor IDE (.cursor/skills/)
python skill/install_skill.py --target opencode      # OpenCode (.opencode/skills/)
```

---

## 🤖 How Claude Code (Opus 5) Uses `letitloop`

In Claude Code, you can simply instruct the agent:

> **User**: "Use the letitloop skill to build a multi-tenant billing service with stripe integration and unit tests."

### What Claude Code (Opus 5) Does:
1. **Reads [`SKILL.md`](../skill/SKILL.md)**: Adopts the Orchestrator Architect role.
2. **Generates Contract DAG**: Breaks down the objective into modular task contracts (`contracts/stripe_client.json`, `contracts/billing_models.json`, `contracts/webhook_handler.json`).
3. **Executes & Verifies**:
   - Calls `lil run` or uses the `letitloop-mcp` tools.
   - Enforces deterministic acceptance checks (`python -m pytest`, AST syntax validation).
   - Arbitrates multi-lens quality reviews (*Security*, *Code Correctness*, *Documentation*).
4. **Reconciles Workspace**: Runs `lil reconcile` to ensure the evidence ledger matches all generated files.

---

## 🛡️ Advantages of Using `letitloop` as a Skill

| Feature | Without `letitloop` Skill | With `letitloop` Skill |
|---|---|---|
| **Macro-Task Execution** | Vulnerable to context window drift and hallucinations | Bounded DAG contracts with isolated task scopes |
| **Crash Recovery** | If IDE crashes, work in progress is lost | State journal with Write-Ahead Logging (WAL) resumes instantly |
| **Verification** | Assumed correctness / eyeball checks | Machine-verifiable AST syntax and exit-code assertions |
| **Retries** | Infinite hallucination loops | Bounded 3-strike policy with automated escalation |
| **Observability** | Scrolling chat terminal | Live ASCII status dashboard matrix (`lil dashboard`) |

---

## 📦 Manual Skill Installation (Optional)

If you prefer to copy the skill manually:

### Claude Code
```bash
mkdir -p ~/.claude/skills/letitloop
cp skill/SKILL.md ~/.claude/skills/letitloop/SKILL.md
```

### Google Antigravity
```bash
mkdir -p ~/.gemini/antigravity/builtin/skills/letitloop
cp skill/SKILL.md ~/.gemini/antigravity/builtin/skills/letitloop/SKILL.md
```
