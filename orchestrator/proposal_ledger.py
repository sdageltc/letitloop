"""
orchestrator/proposal_ledger.py
Human-in-the-Loop (HITL) Architectural Proposal Ledger & Escalation Manager.
"""

from __future__ import annotations
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ProposalResearchItem:
    title: str
    summary: str
    source_url: str
    provider_name: str


@dataclass
class ArchitecturalProposal:
    proposal_id: str
    target_module: str
    target_function: str
    complexity_score: float
    deliberation_verdict: str
    risk_score: float
    rationale: str
    suggested_strategy: str
    research_findings: List[Dict[str, str]] = field(default_factory=list)
    proposed_approach: str = ""
    status: str = "PENDING_HUMAN_REVIEW"  # PENDING_HUMAN_REVIEW, APPROVED, EXECUTED, REJECTED, FAILED_VERIFICATION
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArchitecturalProposal:
        return cls(**data)

    def to_markdown(self) -> str:
        res_md = ""
        if self.research_findings:
            res_md = "\n".join(
                f"- **[{f.get('provider_name', 'Web')}] [{f.get('title', 'Link')}]({f.get('source_url', '#')})**:\n  {f.get('summary', '')}"
                for f in self.research_findings
            )
        else:
            res_md = "_No external research findings required or recorded._"

        return f"""# Architectural Proposal: `{self.proposal_id}`

- **Target Symbol**: `{self.target_module}::{self.target_function}`
- **McCabe Complexity Score**: {self.complexity_score:.1f}
- **Status**: `{self.status}`
- **Risk Score**: {self.risk_score:.2f} ({self.deliberation_verdict})
- **Created**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created_at))}

---

## 1. Feasibility Deliberation & Protection Rationale
> **Verdict**: `{self.deliberation_verdict}` (Risk Score: {self.risk_score:.2f})  
> **Rationale**: {self.rationale}

## 2. Recommended Refactoring Strategy
{self.suggested_strategy}

## 3. Prior Art & External Research
{res_md}

## 4. Proposed Architectural Action
```
{self.proposed_approach or self.suggested_strategy}
```

---

## Human Approval Directive
To approve and execute this refactoring in the isolated sandbox, run:
```powershell
python scripts/run_live_self_evolution.py --approve-proposal "{self.proposal_id}"
```
"""


class ProposalLedger:
    """Manages persistence and lifecycle of architectural proposals staged for human review."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self.proposals_dir = (
            workspace_root / "scratch" / "evolution_state" / "proposals"
        )
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sanitize_id(module_path: str, function_name: str) -> str:
        clean_mod = re.sub(r"[^\w\-]", "_", module_path.replace(".py", ""))
        clean_fn = re.sub(r"[^\w\-]", "_", function_name)
        return f"PROP-{clean_mod}-{clean_fn}"

    def record_proposal(
        self,
        target_module: str,
        target_function: str,
        complexity_score: float,
        deliberation_verdict: str,
        risk_score: float,
        rationale: str,
        suggested_strategy: str,
        research_findings: Optional[List[Any]] = None,
        proposed_approach: str = "",
    ) -> ArchitecturalProposal:
        """Create or update an architectural proposal staged for human review."""
        proposal_id = self.sanitize_id(target_module, target_function)

        clean_research: List[Dict[str, str]] = []
        if research_findings:
            for item in research_findings:
                if hasattr(item, "__dict__"):
                    clean_research.append(
                        {
                            "title": getattr(item, "title", "Web Finding"),
                            "summary": getattr(item, "summary", ""),
                            "source_url": getattr(item, "source_url", ""),
                            "provider_name": getattr(
                                item, "provider_name", "Research"
                            ),
                        }
                    )
                elif isinstance(item, dict):
                    clean_research.append(item)

        existing = self.get_proposal(proposal_id)
        created_at = existing.created_at if existing else time.time()

        proposal = ArchitecturalProposal(
            proposal_id=proposal_id,
            target_module=target_module,
            target_function=target_function,
            complexity_score=complexity_score,
            deliberation_verdict=deliberation_verdict,
            risk_score=risk_score,
            rationale=rationale,
            suggested_strategy=suggested_strategy,
            research_findings=clean_research,
            proposed_approach=proposed_approach or suggested_strategy,
            status="PENDING_HUMAN_REVIEW",
            created_at=created_at,
            updated_at=time.time(),
        )

        self._save(proposal)
        return proposal

    def _save(self, proposal: ArchitecturalProposal) -> None:
        json_path = self.proposals_dir / f"{proposal.proposal_id}.json"
        md_path = self.proposals_dir / f"{proposal.proposal_id}.md"

        json_path.write_text(
            json.dumps(proposal.to_dict(), indent=2), encoding="utf-8"
        )
        md_path.write_text(proposal.to_markdown(), encoding="utf-8")

    def get_proposal(self, proposal_id: str) -> Optional[ArchitecturalProposal]:
        json_path = self.proposals_dir / f"{proposal_id}.json"
        if not json_path.exists():
            return None
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return ArchitecturalProposal.from_dict(data)
        except Exception:
            return None

    def list_proposals(
        self, status_filter: Optional[str] = None
    ) -> List[ArchitecturalProposal]:
        proposals: List[ArchitecturalProposal] = []
        for json_file in sorted(self.proposals_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                prop = ArchitecturalProposal.from_dict(data)
                if status_filter is None or prop.status == status_filter:
                    proposals.append(prop)
            except Exception:
                continue
        return proposals

    def mark_status(self, proposal_id: str, new_status: str) -> bool:
        prop = self.get_proposal(proposal_id)
        if not prop:
            return False
        prop.status = new_status
        prop.updated_at = time.time()
        self._save(prop)
        return True
