# LetItLoop 6-Month Calibrated Milestone Scorecard & Sunset Protocol

This document pre-commits the project to empirical, measurable milestones to maintain discipline, track organic adoption, and guard against maintainer burnout.

---

## 6-Month Scorecard

| Month | Target Phase | Primary Milestone | Minimum Viable Success Metric | Sunset Failure Condition |
|---|---|---|---|---|
| **Month 1** | Scaffolding | `agent-durability-bench` live on GitHub | 100% reproducible synthetic crash suite | Zero external benchmark views / visits |
| **Month 2** | Conformance | Public Leaderboard on GitHub Pages | Real `letitloop` adapter; Leaderboard live | Zero external benchmark reproductions |
| **Month 3** | Distribution | `letitloop-action` on Marketplace | 5+ external repositories installing Action | Zero non-author GitHub Action installs |
| **Month 4** | Community | Community adoption & discussion | 50+ GitHub stars on benchmark repo; 1+ external PR | Fewer than 15 stars across ecosystem |
| **Month 5** | Case Studies | Regulated / CI Proof Case Study | 1 published case study verifying CVE/dependency PRs | Zero user engagement / feedback |
| **Month 6** | Sustainability | Active open source governance | ≥2 active external contributors | Archive repo cleanly with final post-mortem |

---

## Pre-Committed Sunset Protocol

If the minimum viable success metric is not met at Month 4 or Month 6:
1. **No Panic Re-architecture**: Do not invent new complex features to compensate.
2. **Formal Post-Mortem**: Publish a transparent post-mortem documenting empirical benchmark findings and lessons.
3. **Clean Archival**: Transition the repository to read-only archive status.
