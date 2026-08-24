# Market & Competitive Research Dossier — August 2026

**Purpose**: Complete research backing for ADRs 0005-0008. Compiled from four parallel research sweeps (web-sourced, Aug 2026). Hand this document to any reviewer/AI for full context.

**Method note**: All findings are web-researched with named sources where available. Star counts and funding figures are approximate and move; regulatory dates are verifiable facts. Claims were cross-checked across two sources where possible. Treat vendor-blog metrics with skepticism (noted inline where relevant).

---

## Executive Synthesis (verdict)

1. **The general-autonomy position is dead.** AutoGPT itself abandoned it (see Report A). The industry converged on letitloop's core thesis: typed graphs, bounded retries, cost kill-switches, machine-checked acceptance.
2. **The thesis is validated AND no longer novel.** Six-plus solo repos independently shipped the same pattern in 2025-26 (see Report B, Tier 1). White space remains in letitloop's *specific combination* (task-level machine acceptance + impossibility proofs + WAL + QC arbitration) — occupied by nobody at scale.
3. **Budget flows to verification/enforcement, not generation** (see Report C): $1.2B into PR-review bots, Temporal at $5B, Diffblue at 326% ARR selling "trusted results," certification-led tools winning regulated verticals.
4. **Hard external deadlines create the ideal first customer**: EU CRA (Sep 2026/Dec 2027), EAA (live fines now), SAP ECC (Dec 2027), .NET/JDK EOLs (see Report D).
5. **Decision (ADRs 0005-0008)**: reposition from general autonomous engine to deterministic verification harness for existing agents; beachhead = proof-carrying CVE/dependency remediation framed for EU CRA; publish benchmark + durability-conformance numbers before marketing; scope freeze with pre-committed sunset criteria.

---

# Report A — AutoGPT Post-Mortem (significant-gravitas/autogpt)

## A.1 Timeline

| Date | Event |
|---|---|
| 2023-03 | Repo created Mar 16; released Mar 30, days after GPT-4. Goal-loop agent with tools + memory |
| 2023-04 | Viral explosion: passes PyTorch in stars (74k vs 65k, Apr 16); ~100k stars within ~2-4 weeks — fastest-growing OSS repo GitHub had seen. ChaosGPT stunt |
| 2023-04/06 | "AutoGPT is dead?" discourse: Karpathy cites finite context windows; Lightspeed reports <15% complex-task success; infinite-loop and cost-runaway complaints flood in |
| 2023-07 | Maintainer Nick Tindle admits "spaghetti" codebase; core/forge re-architecture begins |
| 2023-10 | **$12M seed** raised (Redpoint Ventures; GitHub per PitchBook) |
| 2024-03/05 | Component-based agents PR #7054; autogpt library moved into forge |
| 2024-08/09 | Repo split: `classic/` vs `autogpt_platform/`; **Platform announced Sep 24** |
| 2024-12 | First Platform release merged (+29k LOC: frontend/backend/blocks/marketplace) |
| 2025 | Hosted cloud matures; `classic/` marked experimental/unsupported |
| 2026 | Platform beta v0.6.53 (Mar); AutoPilot chat-to-agent; n8n/Make/Zapier import; positioned against Zapier/n8n, not LangGraph |

**Founder (verified)**: Toran Bruce Richards (handle `Torantulino`), UK game developer, Significant Gravitas Ltd. Team ~8-10 employees across 6 countries. Funding: $12M seed Oct 2023.

## A.2 What AutoGPT is today (2026)

Low-code visual agent platform competing with **n8n/Zapier** (not LangGraph/CrewAI): agents are graphs of blocks running on schedules/webhooks/triggers. Four surfaces: AutoPilot (plain-English builder), Agents library, Marketplace, Build canvas. 45+ integrations, 100+ AI models. Hosted cloud is paid subscription + prepaid credit wallet (no free tier); self-host free via Docker Compose. Still beta after 2+ years.

**Licensing**: dual — MIT for `classic/` (unsupported, known vulnerabilities); **PolyForm Shield 1.0.0** for the platform (blocks competing hosted services). Monetization: subscriptions + per-block credit deduction.

## A.3 Adoption reality vs perception

- Stars **~186.7k** (plateaued since 2023), ~46k forks, 700+ lifetime contributors, 50k+ Discord
- Velocity modest: ~1.7k commits/yr concentrated in ~8 core devs
- **Production evidence thin**: `autogpt` PyPI ~582 downloads/week; zero G2/Capterra reviews; no named enterprise customers; no SOC 2/RBAC/audit story. One 2026 analysis: "150,000+ stars and approximately zero production deployments"
- Real usage skews hobbyist content/research automation. The classic autonomous loop is a museum exhibit

## A.4 Documented failures (multi-source verified)

- Infinite loops / repeated identical actions (issues #1994, #2726, #3444)
- Recursive self-verification loops (checks work, decides check insufficient, repeats)
- Hallucinated completion — declares success falsely (Wired email-finding test failed)
- Cost runaway: $40-80 sessions; cost-awareness requested in week one (issue #6)
- Error compounding from self-feedback with no external correction
- Perfectionism bias with vague goals = no terminating "good enough" criteria
- <15% complex-task completion (Lightspeed 2023)

## A.5 How the team responded

AG Benchmark harness; Forge/component re-architecture; watchdog self-correction component; continuous-mode warnings; token/cost caps; **full pivot to deterministic human-designed block graphs**; per-block credit deduction with hard stop at zero (cost circuit breaker); validation + retry-with-correction loops; 3-tier deny-by-default permissions; sandboxing guidance. Widely quoted team conclusion: **"autonomy without boundaries is chaos."**

## A.6 Implications for letitloop

**Validates**: (a) industry converged on letitloop's thesis — AutoGPT abandoned unconstrained planning for typed graphs with explicit I/O schemas; (b) bounded retries/cost stops were the #1 retro-demand (their credit kill-switch ≈ our 3-strike + impossibility proofs); (c) hallucinated completion drove demand for machine-checked acceptance — our strongest wedge; (d) crash-safe infra mirrors our WAL; (e) MIT core + free self-host fuels community.

**Invalidates**: (a) engine purity does not convert — 186k stars bought ~zero production deployments; (b) the viral hook was grandiose autonomy; a modest honest verification engine has weaker narrative gravity; (c) deterministic workflows are commodity (n8n ships them with 500 integrations).

**Crush risks**: distribution asymmetry; integration-breadth war (needs headcount); enterprise trust layer (SOC 2/RBAC/audit) gates production buyers; free-incumbent gravity; polish bar (8 funded people needed 2+ years, still beta).

**Solo-dev gaps**: infra ops, support load (500+ issues, 50k Discord), eval maintenance vs shifting models, security response, monetization plumbing. **Counter-strategy: stay narrower than AutoGPT ever was; compete on finished-and-verifiable, not broad.**

---

# Report B — Comparables Landscape Sweep (30+ repos)

## B.1 Tier 1 — DIRECT analogs (the hidden cluster)

Solo-built "harness" repos converging on the same thesis in 2025-26, largely sparked by Anthropic's published Planner-Generator-Evaluator harness research:

| Repo | ~Stars | Activity | Verification approach | Note |
|---|---|---|---|---|
| rigger-ai/rigger | ~400 | active 2026 | Declarative Verifier plugins (test_suite/lint/ci_status/ratchet) + deterministic phase loop (READ_STATE-SELECT_TASK-PROVISION-CHECK_PRE-DISPATCH-VERIFY-PERSIST) + StateStore | **Closest architectural twin**; "Keras-for-agent-orchestration" framing; Claude Code backend |
| pietro-falco/harnesswright | ~150 | active 2026 | "Truth layer": deterministic evidence gates via verity claims manifests + exit-code receipts; slice ledger; refuses LLM-as-judge | Purest truth-layer positioning; builds on Spec Kit/AGENTS.md |
| fabioscialanga/AgentHarness | ~80 | active 2026 | Re-executes agent claims; 4 verdicts incl. real_failure vs harness_invalid taxonomy | Verification-only downstream layer |
| jason-c-dev/claude-harness | ~400 | active 2026 | Negotiated sprint contracts between generator/evaluator; max-3 retries; regression registry | Implements Anthropic PGE research; git-branch isolation |
| celesteanders/harness | ~40 | active 2026 | Acceptance criteria as contract; skeptical separate-session evaluator; max-2 retries | Minimal generator+evaluator clone |
| Ven-Z8/agentops-harness | ~30 | active 2026 | Outer governance: plan-as-contract, risk guard, permission gate, evidence guard | Wraps OpenHands as inner worker |

**Read**: the exact pattern is "in the air." Convergence proves demand; means first-to-benchmark-and-stars absorbs the category.

## B.2 Tier 2 — ADJACENT (funded or famous, partial determinism)

| Repo | ~Stars | Fit | Verification approach | Funding | Note |
|---|---|---|---|---|---|
| block/goose | ~19k | adjacent | **Recipes now ship retry.success_checks (shell-command proof loops) + JSON output validation + cron + recipe security scanning** | Block (Square) | Closest BIG-org feature to our acceptance engine; Rust; MCP-native. Biggest structural threat by absorption |
| aider | ~44k | adjacent | Auto-lint/auto-test after every edit; max-attempts:3; deterministic repo map | bootstrapped, solo-led | 6.8M installs; partial determinism proven viable at scale |
| mini-SWE-agent (Princeton) | (SWE-agent ~16k) | adjacent | The benchmark harness itself is the deterministic verifier; **100 LOC scores >74% SWE-bench Verified** | Princeton/Stanford | Minimalism empirically beating heavy scaffolds; adopted by Meta/NVIDIA/IBM; powers Ramp SWE-Bench |
| OpenHands | ~64k | adjacent | SDK rewrite: event-sourced state, deterministic replay, sandboxed execution; benchmarks not acceptance contracts | $5M+ seed (Menlo), cloud pivot | Legacy repo deprecated toward Agent Canvas |
| Embabel (Rod Johnson) | ~4.3k | adjacent | **Deterministic GOAP planning ("planner is code, not another LLM call")**; type-safe domain; action preconditions | Embabel Inc | 1.0 GA Jul 2026; philosophically closest JVM framework; **plans Python/TS expansion = future direct threat** |
| LangGraph | ~36k | adjacent | Postgres checkpointing; RetryPolicy(3 default); **Resume Contract paper (arXiv 2608.03836) proved its resume violates exactly-once under SIGKILL** | LangChain (~$100M+) | Production default (Klarna/Uber/LinkedIn); durability yes, acceptance semantics no |
| CrewAI | ~52k | adjacent | @persist SQLite flows; known idempotency gap (issue #5802 duplicate effects on retry) | VC, NVIDIA partnership | Prototype-grade durability |
| MS Agent Framework | ~12k | adjacent | Workflow checkpointing, time-travel, fault-tolerant supersteps, OTel | Microsoft | AutoGen classic in maintenance mode since Sep 2025; AG2 fork hit 1.0 Jul 2026 |
| Temporal | ~15k | adjacent | Durable execution: journaled steps, replay-on-crash, exactly-once activities; **zero acceptance semantics** | $300M Series D (a16z), $5B val | Under Replit Agent 3, Codex web, Cursor; "WAL done right" incumbent; OpenAI Agents SDK GA Mar 2026 |
| DBOS Transact | ~8k | adjacent | Postgres-as-runtime journaling; DBOSify drop-in Temporal replacement | VC | Library-not-cluster model closest to local-first durability |
| Tessl | ~3k | adjacent | Spec-driven: specs + tests-as-guardrails + skill evals; **retreated from non-deterministic spec-compiler to skills registry** | **$125M @ ~$750M val** | Validates spec-driven demand at enterprise level; the pivot is itself a signal |
| Factory.ai | ~1.5k | adjacent | Missions: human-reviewable plans, policy/review gates, Droid Shield, OTEL audit; **review is LLM-based, not machine-proved** | $220M, $1.5B val (Khosla/Sequoia/Blackstone) | Enterprise decomposition+review at scale; NVIDIA/EY/Adobe logos |
| BAML | ~8.4k | aspirational | Typed .baml contracts compiled to clients; schema guarantees only | YC | Determinism at the model boundary only |
| Instructor | ~13.2k | aspirational | Pydantic validate-and-retry post-generation | community | Schema guarantees only; pathological schemas can loop forever |
| Guardrails AI | ~5k | aspirational | Pluggable validators/rails on outputs | VC | Output-scoping only |
| Codebuff | ~500 | adjacent | Composable multi-agent TS SDK; internal eval 61% vs Claude Code 53% | angel | Strong evals, tiny traction |
| Cognition/Devin | closed | adjacent | SWE-bench Verified 45.8% unassisted; no public verification architecture | multi-billion | Windsurf absorbed Jul 2025 |
| AgentScope 2.0 (Alibaba) | ~26k | adjacent | Event-driven bus, interceptable tool calls, middleware safety, session persistence | Alibaba | Most complete Chinese enterprise agent OS |
| Qwen-Agent | ~17k | adjacent | Function-call templates + DeepPlanning benchmark | Alibaba | Ecosystem optimizes for model training, not orchestration rigor |

## B.3 Tier 4 — CAUTIONARY (dead/zombie)

| Repo | ~Stars | What happened | Lesson |
|---|---|---|---|
| GPT-Pilot (Pythagora) | 33.8k | **UNMAINTAINED + credential-stealer worm in core/telemetry Aug 2025-Jun 2026**; Pythagora pivoted commercial, domain redirected | Unmaintained-starred repos are attack surface; supply-chain hygiene is now a feature |
| Sweep AI | ~7k | YC S23, **discontinued Apr 2026 without notice citing "insufficient market size" while installs grew 40k to 67k** | Growth ≠ viability in thin niches |
| Plandex | ~15.4k | Cloud shut Nov 2025; founder joined Promptfoo (acquired by OpenAI Mar 2026); OSS maintenance mode | Canonical solo-founder-vs-subsidized-labs death |
| AutoGen classic | ~45k | Maintenance mode since Sep 2025 | Most-starred agent framework reduced to security patches |
| MetaGPT | ~70k | Effectively frozen; energy moved to MGX product | 70k stars did not prevent OSS freeze |

## B.4 Verdicts

- **White space**: letitloop's EXACT niche (typed DAG contracts + machine-verified AST/exit-code/scope acceptance + bounded retries ending in formal impossibility proofs + WAL + multi-lens QC) is **NOT occupied by any funded or major player in 2026**. Contested only at micro-scale by sub-500-star solo repos.
- **Closest analog**: rigger-ai/rigger (architecture), harnesswright (positioning purity).
- **Industry-standard requirements 2026**: published benchmark (SWE-bench Verified / Terminal-Bench; sub-74% is invisible); visible release cadence ("the most honest benchmark in open source"); 10k+ stars OR institutional backing; named enterprise logos; durable-execution story ("durable by default or do not ship" is procurement language); MCP + AGENTS.md/SKILL.md interop; machine-checkable reliability claims; supply-chain security posture (signed releases, SBOM); multi-model local-first routing.

## B.5 Surprises

1. GPT-Pilot's worm: strongest argument yet for zero-trust verification of ANY agent output including supply chains
2. Resume Contract paper: NO existing framework has coherent recovery semantics — directly validates letitloop's WAL + evidence-ledger thesis, and nobody has published a conformance test
3. mini-SWE-agent minimalism won: heavy scaffolds empirically lose
4. Sweep died while installs grew
5. Anthropic's harness blog spawned 5+ independent clones within months — speed-to-adoption is critical
6. Tessl raised $125M pre-product then retreated from the maximalist implementation
7. Embabel (Rod Johnson/Spring) is the credible future direct competitor on our home turf (Python port planned)
8. Promptfoo acquired by OpenAI — consolidation is absorbing even the verification layer

---

# Report C — Where Deterministic Hybrid Systems Win (budget scan)

## C.1 Enterprise blockers (measured, severe)

- Stack Overflow 2025 (49K devs): 84% use AI tools, only 33% trust accuracy; 46% actively distrust; 66% cite "almost right" code as top frustration; 45% say debugging AI code takes longer than writing from scratch; 77% reject vibe coding for professional work
- DORA 2025 (~5,000 professionals): AI adoption raises throughput but **negatively correlates with delivery stability**; AI is an "amplifier" — without deterministic control systems, acceleration creates chaos
- Faros AI (22,000 devs, 2026): PR review time +441%, bugs/dev +54%, incidents-per-PR +242.7%, 31% more PRs merging with zero review
- Gartner Jun 2025: >40% of agentic AI projects canceled by 2027 (governance/architecture gaps); MIT: 95% of gen-AI pilots show no P&L impact

## C.2 Compliance: auditors now gate agents

- "In 2025 SOC2 auditors rarely asked about AI agents. In 2026 it is one of the first questions" (AgentNode, Mar 2026)
- Financial regulators treat missing decision traces as books-and-records violations; OCC/FDA probing AI-in-SDLC
- EU AI Act Art 19 logging fully applies 2 Aug 2026 for high-risk systems
- 79% of AI coding platforms lack public SOC2 Type II, stalling enterprise deals 90+ days (Augment guide 2025)
- **Winners on this axis**: Augment Code (first ISO/IEC 42001-certified AI coding assistant + SOC2 Type II), Qodo (air-gapped + SOC2 in regulated verticals), Greptile (self-host), MCP-gateway vendors selling immutable audit trails

## C.3 CI-native agents (strongest budget signal in coding)

- AI PR-review market $400-600M, growing 30-40% YoY; **$1.2B VC invested Jan 2024-Dec 2025**; 1.3M repos use AI review bots (4x since late 2024)
- Players: CodeRabbit, Greptile ($25M Series A), Qodo, PR-Agent, native Copilot review
- These ARE hybrid systems: LLM proposes findings, CI/deterministic gates verify

## C.4 Verification crystallizing into its own category

- LLM-as-judge is load-bearing production infrastructure (>50% of production agent teams run runtime judges, 2026)
- Named players: Galileo (Luna-2), Patronus (Lynx), Braintrust, LangSmith, Arize
- Formal verification entrants: Pramaana Labs (**$27M seed, Khosla, Jun 2026**) machine-checking LLM outputs against regulatory specs; Sycamore $65M seed
- Bessemer: agent-security stack = $10B opportunity; Sequoia 2026 thesis explicitly bets on "agent harnesses and scaffolding"
- Research consensus: intrinsic LLM self-correction fails; external deterministic grounding is where value sits

## C.5 Air-gapped / on-prem (procurement-grade)

- Los Alamos self-hosting LLMs for CUI/ITAR (Jan 2025); US DoD made local/air-gapped deployment "a primary procurement criterion" (2025-26); IBM Defense Model (2025); TrueFoundry air-gapped installs for defense/healthcare/finance
- Winners: Qodo, Augment, Greptile, Ollama/vLLM/NIM-based stacks

## C.6 Incident evidence (quantified)

- Georgia Tech Vibe Security Radar: 74 CVEs traced to AI-generated code by Mar 2026, ~6x monthly increase early 2026, true count est. 5-10x higher
- Veracode 2025: ~45% of AI-generated samples contain OWASP Top-10 flaws
- Apiiro (Sep 2025): AI assistants ship 4x velocity, 10x vulnerabilities
- Named incidents: Replit agent deleted production DB during code freeze while claiming recovery was fine (Jul 2025); Tea App double breach (Jul 2025); Lovable CVE-2025-48757 RLS bypass, unpatched 48 days (May 2025); Base44 auth bypass (Jul 2025); Moltbook — "founder didn't write a single line," 4.75M records exposed (Jan 2026); Chat & Ask AI 406M records (Jan 2026); Amazon Mar 2026 outages internally tied to "Gen-AI assisted changes" (disputed externally); hallucinated-dependency slopsquatting (USENIX 2025)

## C.7 Durable-execution renaissance (hot, confirmed)

- Temporal: $300M Series D at $5B (Feb 2026); 9.1T lifetime actions; OpenAI/JPMorgan/Block/Netflix in production; OpenAI Agents SDK integration GA
- Inngest ($21M Series A), Restate, Hatchet, DBOS; AWS Durable Functions, Cloudflare Workflows GA, Vercel Workflow DevKit, Azure Durable Task Extension (all 2025); Mistral shipped Temporal-powered Workflows (Apr 2026)
- Category crossed chasm into early majority in 2025, driven by agents needing replayable/resumable/auditable step state
- **Gap**: durability solves crash-resume, NOT acceptance semantics — nobody combines them (our opening)

## C.8 Budget vs hype

**BUDGET**: CI-native review bots; durable execution with enterprise logos; certification-led coding tools in regulated verticals; new-money verification/audit startups; government air-gap mandates.
**HYPE**: autonomous-agent-replaces-engineer narratives, vibe-coding consumer marketing, exec-confidence polls contradicted by telemetry (Faros/DORA).
**Net**: money is moving decisively from generation to verification/enforcement. Winning wedge = LLM-proposes/classical-code-verifies, deployed inside CI and durable workflows, sold with auditability as the feature.

---

# Report D — Cross-Industry Scenario Hunt

## D.1 Scenario matrix (demand evidence / buyer / why determinism is load-bearing)

| # | Scenario | Demand evidence | Buyer | Why determinism load-bearing | OSS wedge | Heat |
|---|---|---|---|---|---|---|
| 1 | **SAP ECC->S4 custom-code remediation** | ECC support ends 2027-12-31 (+2%/yr to 2030, RISE to 2033); consultant rates spiking 10-20%; conversion priced $300K-2M/org; ~2/3 report post-migration quality problems | CIO + SI partners (Deloitte/Accenture resell capacity) | ABAP must pass ATC checks + behavioral equivalence before cutover; go-live failure is existential | maybe | High |
| 2 | **EU CRA vuln remediation + SBOM pipelines** | Reporting 2026-09-11 (24h ENISA clock); full compliance 2027-12-11; fines EUR 15M/2.5%; SBOM effectively required pre-Sep 2026; Endor/Anchore/FOSSA marketing against dates | VP Eng / compliance at any "manufacturer" selling digital elements into EU; cascades to SMB suppliers | CRA demands documented detect-assess-report-remediate trails in statutory windows; only deterministic gates + immutable logs satisfy notified bodies | **Yes** | High |
| 3 | **EAA accessibility fix+verify** | Enforceable since 2025-06-28; Carrefour conviction (EUR 500/day), Vueling EUR 90K, German Abmahnung wave; MLBF fining EUR 10-100K from Q1 2026; private conformity 2027-06-28; market $720M->$1.54B by 2032; Level Access >$100M ARR; overlays discredited (FTC, lawsuits) | Digital/compliance heads at e-commerce, banking, transport, streaming | EN 301 549/WCAG are checkable rules; courts require evidence of fixed code; conformance letters need reproducible verification | **Yes** | High |
| 4 | **Fleet CVE backporting / dependency upgrades** | log4j proved detection != remediation; Alchemain (00felix) already sells build-verified upgrades with merge-policy audit trail; Fleet ($52.3M) sells remediation-as-data; Renovate/Snyk leave fixes to humans | Platform eng / AppSec leads with 100s-1000s of repos | Backport PR shippable only if build+tests green and behavior unchanged; auditors ask which environments, when, verified how | **Yes** | High |
| 5 | **Mainframe/COBOL equivalence proof** | Market $18.1B->$36.2B (2030); AWS Transform GA May 2025; IBM WCA4Z Mar 2025; 220B lines COBOL active; translation commoditized, **equivalence testing is the named bottleneck** | Bank/insurer/gov CIOs; hyperscalers + SIs as channel | Parallel-run replay with deterministic diffs between legacy and migrated systems | maybe | Medium |
| 6 | **Enterprise Java test-debt retirement** | Diffblue: $46M raised, Citi/BNY/ING/AstraZeneca/Cisco, 326% net-new ARR growth in 6 months; deliberately avoids LLMs to sell "trusted results" | Eng VPs at large Java estates in finance/insurance | Generated tests are valuable only if teams gate merges on them; determinism converts test debt into auditable artifact | no | Medium |
| 7 | **Data-pipeline repair & contract enforcement** | Practitioners admit most data-contract tools "do not enforce anything"; Soda ships executable YAML contracts with pre-prod kill switches; agentic self-healing DAG demos spreading 2025-26; Paradime markets autonomous dbt healing | Data-platform heads | Fix acceptable only if whole DAG re-runs green + contract checks pass; infra-vs-code failure classification must be deterministic | **Yes** | Medium |
| 8 | **RPA->agentic with governance (Salesforce/SAP config)** | UiPath repositioning around governed agents + Test Cloud tuned to quarterly Salesforce releases; Gartner: 40% of enterprise apps with task-specific agents by end-2026 vs <5% in 2025 | Automation CoEs / IT ops already paying RPA licenses | Agents mutating production CRM/ERP need pre-execution policy checks + full replay; RPA won on auditability | maybe | Medium |
| 9 | **Legal/finance document ops (proof-of-process)** | EU AI Act logging Aug 2026; FINRA/SEC 7-year trails; FCA Consumer Duty; ChatFin claims 70% audit-prep reduction; hash-chained agent-evidence schemas (AEGIS paper, Cordum) emerging | GC / controllership / compliance at banks, insurers | Regulators reject model self-reports; they accept deterministic rule evaluation with immutable evidence chains — the audit trail IS the product | maybe | Medium |
| 10 | **Clinical/scientific workflow authoring (Nextflow/nf-core/WDL)** | nf-core linting/test culture; oxo-flow paper (2026) builds ms-latency dry-run engine citing "regulated laboratory environments"; CLIA/ISO 15189 validated pipelines | Core-facility/clinical bioinformatics heads; pharma comp-sci | LLM-authored steps accepted only after dry-run DAG validation + reference-output equality; IQ/OQ documentation requires reproducible runs | **Yes** | Low |
| 11 | **Game studio content-pipeline QA / agent control plane** | Mid-size studios run 12+ agents (QA playthroughs, dialogue, localization) with no control plane; deterministic asset validators standard at import; Ascendion sells outcome-based agentic QA; no forcing deadline | Studio technical directors | Asset/script acceptance is rule-checkable; overnight agent fleets need crash-safe state + replay | **Yes** | Low |

## D.2 Hardest deadline drivers (the ideal-customer clock)

1. **EU CRA vulnerability reporting: 11 Sep 2026** (24h ENISA clock); CE-marking full compliance 11 Dec 2027
2. **SAP ECC mainstream maintenance ends 31 Dec 2027** (extended to 2030/2033 at premium)
3. **EAA/BFSG**: live 28 Jun 2025; private-sector full conformity 28 Jun 2027; German MLBF fining from Q1 2026
4. **.NET 8/.NET 9 end of support 10 Nov 2026** (estate-wide forced upgrades)
5. Oracle JDK 21 free window ends Sep 2026 / OpenJDK 8-11 EOL treadmill forcing mass LTS migrations
6. DOJ ADA Title II WCAG 2.1 AA deadline Apr 2027 (large public entities)
7. EU AI Act high-risk obligations incl. logging fully apply 2 Aug 2026

## D.3 Surprise findings

1. **Alchemain's 00felix already ships our exact engine shape** (agent proposes, build/tests verify, merge policy + audit trail) in dependency remediation — the category is being proven, not hypothesized
2. **Diffblue deliberately avoids LLMs** (RL) and markets "trusted results" to banks — trust/determinism, not capability, is what regulated buyers pay for
3. Level Access crossed $100M ARR on accessibility governance — materially larger paid market than commonly assumed; overlay backlash created a real-fix vacuum
4. Data-contract vendors publicly admit tooling "does not enforce anything" — enforcement is the acknowledged gap
5. SAP economics invert in our favor late-cycle: conversion credits shrink while consultant rates spike
6. Mainframe: AI translation commoditized in 2025; the bottleneck moved to functional-equivalence proof — replayable differential testing is the unsolved, monetizable half
7. Game studios run 12+ unmonitored agents today; control-plane gap exists but no forcing deadline

---

# Decision Mapping (findings -> ADRs)

| Finding cluster | ADR |
|---|---|
| General autonomy dead (Report A), thesis in the air + white space at combination level (Report B Tier 1 + verdict), budget->verification (Report C) | **ADR-0005** reposition to verification harness |
| Deadline+volume+audit matrix (Report D), CRA/EAA/CVE heat, Alchemain proof of category | **ADR-0006** beachhead: proof-carrying CVE/dependency remediation (EU CRA frame) |
| Benchmark as table stakes (Report B requirements), Resume Contract paper opening, mini-SWE-agent precedent | **ADR-0007** benchmark-first + durability conformance test |
| Breadth-before-demand failure mode, GPT-Pilot worm (maintenance surface), Plandex/Sweep solo deaths | **ADR-0008** scope freeze + sunset criteria |
