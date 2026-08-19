# Universal Agent Skill Integration Guide for `letitloop`

You can use `letitloop` as a **Universal Agent Skill** across the entire 2026 AI coding ecosystem:
**Claude Code**, **OpenAI Codex**, **Cursor**, **Google Antigravity**, **Hermes Agent**, **OpenCode**, **Cline**, and **Windsurf**.

When installed as a skill, the Lead Model (e.g. **Claude 3.7 Sonnet / Opus**, **GPT-4o / o3-mini**, **Gemini 2.5 Pro**, or **DeepSeek-Reasoner**) acts as the high-level architect while `letitloop` acts as the underlying deterministic engine.

---

## 1-Click Skill Installation Across Platforms

### Method 1: Zero-Install (Universal Skill Package Manager)
```bash
# Add letitloop to your active agent environment instantly
npx skills add sdageltc/letitloop
```

---

### Method 2: Via Python CLI (`lil install-skill`)
If you installed the `letitloop` package via `pip install letitloop`:

```bash
# Install into all detected AI assistant environments
lil install-skill --all

# Or install for a specific assistant:
lil install-skill --target claude        # Claude Code (~/.claude/skills/letitloop/)
lil install-skill --target codex         # OpenAI Codex (~/.codex/skills/letitloop/ or .codex/skills/letitloop/)
lil install-skill --target antigravity   # Google Antigravity (~/.gemini/antigravity/builtin/skills/letitloop/)
lil install-skill --target hermes        # Hermes Agent (~/.hermes/skills/letitloop/)
lil install-skill --target opencode      # OpenCode (.opencode/skills/letitloop/)
lil install-skill --target cursor        # Cursor IDE (.cursor/skills/letitloop/)
lil install-skill --target cline         # Cline (.cline/skills/letitloop/)
lil install-skill --target windsurf      # Windsurf (.windsurf/skills/letitloop/)
```

---

### Method 3: From Source Repository
```bash
python skill/install_skill.py --all
```

### 📝 OpenAI Codex & `AGENTS.md` Integration
For OpenAI Codex, `letitloop` works seamlessly through both the skill system and `AGENTS.md`. You can reference the skill directly in your project root `AGENTS.md`:
```markdown
# Agent Directives
- When executing complex multi-step features or refactors, always adopt the `letitloop` DAG contract planning methodology.
- Enforce strict 3-strike bounded retry limits with strategy mutation on failures.
- Verify all changes with deterministic exit-code checks (`pytest`, syntax verification) before reporting complete.
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
