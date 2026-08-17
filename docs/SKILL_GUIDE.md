# Universal Agent Skill Integration Guide for `letitloop`

You can use `letitloop` as a **Universal Agent Skill** across the entire 2026 AI coding ecosystem:
**Claude Code**, **Cursor**, **Google Antigravity**, **Hermes Agent**, **OpenCode**, **Cline**, and **Windsurf**.

When installed as a skill, the Lead Model (e.g. **Claude Opus 5**, **Gemini 3.1 Pro**, **GPT-5.6 Sol**, or **DeepSeek-V4 Pro**) acts as the high-level architect while `letitloop` acts as the underlying deterministic engine.

---

## ⚡ 1-Click Skill Installation Across Platforms

From the `letitloop` repository directory, run:

```bash
# Install into all detected AI assistant environments
python skill/install_skill.py

# Or install for a specific assistant:
python skill/install_skill.py --target claude        # Claude Code (~/.claude/skills/letitloop/)
python skill/install_skill.py --target antigravity   # Google Antigravity (~/.gemini/antigravity/builtin/skills/letitloop/)
python skill/install_skill.py --target hermes        # Hermes Agent (~/.hermes/skills/letitloop/)
python skill/install_skill.py --target opencode      # OpenCode (.opencode/skills/letitloop/)
python skill/install_skill.py --target cursor        # Cursor IDE (.cursor/skills/letitloop/)
python skill/install_skill.py --target cline         # Cline (.cline/skills/letitloop/)
python skill/install_skill.py --target windsurf      # Windsurf (.windsurf/skills/letitloop/)
```

---

## 🤖 How Coding Agents Use `letitloop`

In your AI coding agent of choice, simply prompt:

> **User Prompt**: "Use the letitloop skill to implement a multi-tenant billing service with stripe integration and unit tests."

### What the Agent Does:
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
