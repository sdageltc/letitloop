# 🚀 `letitloop` (LIL) — Official Launch & Promotion Kit

Use these ready-to-publish assets to launch `letitloop` across developer communities, social media, and open-source directories.

---

## 🐦 1. X (Twitter) Launch Thread

### Tweet 1 (The Hook):
> Most AI coding agents fail on multi-file tasks because they run in fragile, open-ended while-loops with optimistic text assumptions.
> 
> We built something better: **let it loop (LIL)** — an OS-grade autonomous control loop with typed DAG contracts, crash-proof WAL state recovery, and 8 deterministic acceptance verifiers.
> 
> 100% open-source & provider-agnostic. 🧵👇
> 
> 🔗 https://github.com/sdageltc/letitloop

### Tweet 2 (The Problem & Solution):
> Why do standard agent loops hallucinate?
> ❌ Context window drift
> ❌ Eyeball QA (LLM grading itself)
> ❌ Infinite retry money-burns
> 
> `letitloop` treats coding tasks like an OS process scheduler:
> 1️⃣ Decomposes goals into strongly-typed JSON contract DAGs
> 2️⃣ Enforces sandbox `allow`/`deny` scopes
> 3️⃣ Verifies with AST syntax & command exit codes ($? == 0)

### Tweet 3 (Zero-Subscription & Local Models):
> You don't need expensive commercial subscriptions to run autonomous macro-goals.
> 
> `letitloop` works out-of-the-box with:
> 🦙 100% Local Models via **Ollama**, **vLLM**, or **LM Studio**
> 🔀 Multi-model gateways via **Omniroute**, **OpenRouter**, **Groq**
> ⚡ Frontier APIs (**Gemini 3.7 Flash**, **GPT-5.6 Sol**, **Claude Opus 5**, **DeepSeek-V4 Pro**)

### Tweet 4 (Universal 7-Platform Support):
> Whether you code in the terminal or inside an IDE, `letitloop` meets you there with a built-in MCP server (`letitloop-mcp`) and 1-click universal agent skill installer:
> 
> • Claude Code (`claude mcp add`)
> • Cursor IDE
> • Google Antigravity
> • Hermes Agent (Nous Research)
> • OpenCode
> • Cline
> • Windsurf

### Tweet 5 (Call-to-Action):
> 362+ unit tests passing (1,095 assertions) + 7 integration suites.
> 
> 💻 Get started in 60 seconds:
> ```bash
> git clone https://github.com/sdageltc/letitloop.git
> cd letitloop && pip install -e .
> lil propose "Build JWT auth with redis rate limiting and unit tests" --run
> ```
> 
> ⭐ Star the repo & test it out: https://github.com/sdageltc/letitloop

---

## 🟠 2. Hacker News (Show HN)

### Title:
> **Show HN: letitloop – Autonomous coding control loop with DAG contracts and WAL state recovery**

### Post Body:
> Hey HN,
> 
> Over the past year, we noticed that most autonomous coding agents fail when tasks exceed 3-4 files not because models are incapable, but because the control architecture is fragile: agents run in unbounded while-loops that guess success through conversational text output.
> 
> We built **let it loop (LIL)** ([github.com/sdageltc/letitloop](https://github.com/sdageltc/letitloop)) to bring operating-system-level rigor to autonomous agent loops:
> 
> **1. Typed DAG Contract Planner:**
> Instead of dumping a monolithic goal into a chat prompt, LIL decomposes intent into a strongly-typed dependency graph of JSON contracts with topological cycle detection and strict `workspace_scope.allow` / `workspace_scope.deny` boundaries.
> 
> **2. Crash-Resilient Supervisor (WAL):**
> Execution progress is recorded in a Write-Ahead Log (WAL) journal (`state.jsonl`) protected by Win32/POSIX atomic heartbeat file locks. If your terminal, machine, or IDE crashes mid-task, LIL resumes instantly from the exact interrupted contract state without re-executing completed work.
> 
> **3. Zero-Trust Deterministic Verifier (8 Check Types):**
> A task contract is never marked `COMPLETE` through LLM self-evaluation. It requires physical machine proof:
> - Multi-language AST syntax validation (Python, TS, JS, Go, Rust)
> - Command execution assertions (`exit_code == 0`) with recursive process-tree timeouts
> - Deterministic regex pattern matching
> - Markdown/HTML structure render validation
> - Undeclared output detection (flags any file created outside the contract scope)
> 
> **4. Multi-Lens Quality Plane & Bounded 3-Strike Governance:**
> Includes specialized evaluation lenses (*Security Hardening*, *Code Correctness*, *Documentation Fidelity*, *Test Completeness*, *Adversarial Architecture Audit*) with senior arbitration. If a worker fails 3 attempts on a task, the supervisor halts and emits an `impossibility.md` diagnostic report rather than burning API credits in an infinite loop.
> 
> **5. Universal MCP & 7-Platform Agent Skill:**
> Exposes 8 native Model Context Protocol (MCP) tools and a 1-click skill installer supporting Claude Code, Cursor, Google Antigravity, Hermes Agent, OpenCode, Cline, and Windsurf. Fully provider-agnostic—runs for free locally on Ollama/vLLM or with Omniroute/commercial APIs.
> 
> All 362 tests (1,095 assertions) are 100% green. We'd love your feedback, critiques on our contract state machine, and PRs!
> 
> Repo: https://github.com/sdageltc/letitloop

---

## 🔴 3. Reddit Post (r/LocalLLaMA & r/MachineLearning)

### Title:
> **[P] letitloop: Open-source autonomous control loop with DAG contracts, WAL crash recovery, and local Ollama support**

### Post Body:
> Hey everyone,
> 
> We just open-sourced **let it loop (LIL)**, an autonomous macro-task control loop designed to solve the context-drift and infinite-loop problems in coding agents.
> 
> 🔗 **GitHub**: https://github.com/sdageltc/letitloop
> 
> ### Why we built it:
> Most agent frameworks rely on the model to self-report when it's "done." On local models (e.g. Qwen 2.5 Coder 32B or DeepSeek-R1), this often leads to hallucinated test passes or runaway loops.
> 
> LIL separates high-level planning from deterministic execution:
> - **DAG Planner**: Breaks objectives into modular JSON contracts.
> - **Zero-Trust Verification Engine**: Runs AST parsers (5 languages), command exit-code checks ($? == 0), regex validators, and rogue-file detection before marking anything complete.
> - **WAL State Recovery**: Write-Ahead Logging means you can kill a long-running job and resume it seamlessly.
> - **100% Local / Free**: Point it at `http://localhost:11434/v1` (Ollama) or your favorite gateway (Omniroute / OpenRouter) with zero subscriptions.
> - **Works with your tools**: Has native MCP support (`letitloop-mcp`) and 1-click skills for Claude Code, Cursor, Hermes Agent, OpenCode, Cline, and Antigravity.
> 
> The repo is MIT-licensed with 1,095 automated test assertions passing. Check it out and let us know what you think!

---

## 🏷️ 4. Recommended GitHub Repository Topics (SEO Tags)
Add these 20 tags to your GitHub repo settings under **About $\rightarrow$ Topics**:

`autonomous-agents`, `coding-agent`, `mcp-server`, `claude-code`, `cursor-ide`, `antigravity`, `hermes-agent`, `opencode`, `cline`, `windsurf`, `dag-planner`, `state-machine`, `write-ahead-log`, `deterministic-verification`, `quality-assurance`, `ollama`, `local-llm`, `omniroute`, `ai-engineer`, `python`
