"""ArenaArbiter multi-agent consensus and decision arbitration module (Pruned & Hardened)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


class ConsensusStrategy(str, Enum):
    """Supported consensus strategies for arbitrating multi-agent proposals."""

    MAJORITY_VOTE = "majority_vote"
    WEIGHTED_MAJORITY = "weighted_majority"
    SUPERMAJORITY = "supermajority"
    UNANIMOUS = "unanimous"


@dataclass
class Proposal:
    """Represents a proposal submitted by an agent."""

    proposal_id: str
    agent_id: str
    content: Any
    confidence: float = 1.0
    score: float = 0.0
    rank: Optional[int] = None
    rationale: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.proposal_id or not isinstance(self.proposal_id, str):
            raise ValueError("proposal_id must be a non-empty string")
        if not self.agent_id or not isinstance(self.agent_id, str):
            raise ValueError("agent_id must be a non-empty string")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be in range [0.0, 1.0], got {self.confidence}")


@dataclass
class AgentVote:
    """Represents a vote or evaluation from an agent for proposals."""

    agent_id: str
    proposal_id: str
    weight: float = 1.0
    rank: Optional[int] = None
    score: Optional[float] = None
    confidence: float = 1.0
    comment: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent_id or not isinstance(self.agent_id, str):
            raise ValueError("agent_id must be a non-empty string")
        if not self.proposal_id or not isinstance(self.proposal_id, str):
            raise ValueError("proposal_id must be a non-empty string")
        if self.weight < 0:
            raise ValueError(f"Weight must be non-negative, got {self.weight}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be in range [0.0, 1.0], got {self.confidence}")


@dataclass
class ArbitrationResult:
    """Result of multi-agent proposal arbitration and consensus resolution."""

    selected_proposal: Optional[Proposal]
    consensus_reached: bool
    strategy_used: ConsensusStrategy
    confidence_score: float
    vote_distribution: Dict[str, float] = field(default_factory=dict)
    ranked_proposals: List[Tuple[Proposal, float]] = field(default_factory=list)
    reasoning: str = ""
    dissenting_agents: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ArenaArbiter:
    """Arbitrates consensus across multi-agent proposals using clean majority rules."""

    def __init__(
        self,
        default_strategy: ConsensusStrategy = ConsensusStrategy.MAJORITY_VOTE,
        min_quorum: int = 1,
        min_consensus_threshold: float = 0.5,
        supermajority_threshold: float = 0.66,
    ):
        self.default_strategy = default_strategy
        self.min_quorum = min_quorum
        self.min_consensus_threshold = min_consensus_threshold
        self.supermajority_threshold = supermajority_threshold
        self.agent_weights: Dict[str, float] = {}

    def register_agent_weight(self, agent_id: str, weight: float) -> None:
        if weight < 0:
            raise ValueError("Agent weight cannot be negative")
        self.agent_weights[agent_id] = float(weight)

    def resolve_consensus(
        self,
        proposals: Sequence[Proposal],
        votes: Optional[Sequence[AgentVote]] = None,
        strategy: Optional[ConsensusStrategy] = None,
        quorum: Optional[int] = None,
        tie_breaker: Optional[Callable[[Sequence[Proposal]], Proposal]] = None,
    ) -> ArbitrationResult:
        if not proposals:
            return ArbitrationResult(
                selected_proposal=None,
                consensus_reached=False,
                strategy_used=strategy or self.default_strategy,
                confidence_score=0.0,
                reasoning="No proposals provided for arbitration.",
            )

        active_strategy = strategy or self.default_strategy
        active_quorum = quorum if quorum is not None else self.min_quorum
        prop_map = {p.proposal_id: p for p in proposals}

        # Quorum validation
        participant_count = len(votes) if votes is not None else len(proposals)
        if participant_count < active_quorum:
            return ArbitrationResult(
                selected_proposal=None,
                consensus_reached=False,
                strategy_used=active_strategy,
                confidence_score=0.0,
                reasoning=f"Quorum failure: required {active_quorum}, got {participant_count}",
            )

        # 1. Unanimous
        if active_strategy == ConsensusStrategy.UNANIMOUS:
            if votes is not None:
                vote_targets = {v.proposal_id for v in votes}
                if len(vote_targets) == 1:
                    target_id = list(vote_targets)[0]
                    if target_id in prop_map:
                        return ArbitrationResult(
                            selected_proposal=prop_map[target_id],
                            consensus_reached=True,
                            strategy_used=ConsensusStrategy.UNANIMOUS,
                            confidence_score=1.0,
                            reasoning="Unanimous consensus reached via agent votes.",
                        )
                dissenters = [v.agent_id for v in votes if v.proposal_id != votes[0].proposal_id]
                return ArbitrationResult(
                    selected_proposal=None,
                    consensus_reached=False,
                    strategy_used=ConsensusStrategy.UNANIMOUS,
                    confidence_score=0.0,
                    dissenting_agents=dissenters,
                    reasoning="Unanimous consensus failed: votes differ.",
                )
            else:
                first_content = proposals[0].content
                all_match = all(p.content == first_content for p in proposals)
                if all_match:
                    return ArbitrationResult(
                        selected_proposal=proposals[0],
                        consensus_reached=True,
                        strategy_used=ConsensusStrategy.UNANIMOUS,
                        confidence_score=1.0,
                        reasoning="Unanimous consensus reached across proposals.",
                    )
                dissenters = [p.agent_id for p in proposals if p.content != first_content]
                return ArbitrationResult(
                    selected_proposal=None,
                    consensus_reached=False,
                    strategy_used=ConsensusStrategy.UNANIMOUS,
                    confidence_score=0.0,
                    dissenting_agents=dissenters,
                    reasoning="Unanimous consensus failed across proposals.",
                )

        # 2. Majority & Supermajority
        tally: Dict[str, float] = {p.proposal_id: 0.0 for p in proposals}
        total_weight = 0.0

        if votes is not None:
            for v in votes:
                if v.proposal_id in tally:
                    w = (
                        v.weight
                        if active_strategy != ConsensusStrategy.WEIGHTED_MAJORITY
                        else self.agent_weights.get(v.agent_id, v.weight)
                    )
                    tally[v.proposal_id] += w
                    total_weight += w
        else:
            for p in proposals:
                w = (
                    self.agent_weights.get(p.agent_id, 1.0)
                    if active_strategy == ConsensusStrategy.WEIGHTED_MAJORITY
                    else 1.0
                )
                tally[p.proposal_id] += w
                total_weight += w

        if total_weight <= 0:
            total_weight = 1.0

        sorted_tally = sorted(tally.items(), key=lambda item: item[1], reverse=True)
        top_id, top_score = sorted_tally[0]
        top_ratio = top_score / total_weight

        # Check threshold
        req_threshold = (
            self.supermajority_threshold
            if active_strategy == ConsensusStrategy.SUPERMAJORITY
            else self.min_consensus_threshold
        )
        if len(sorted_tally) > 1 and sorted_tally[0][1] == sorted_tally[1][1]:
            # Tie breaking
            if tie_breaker is not None:
                tied_props = [prop_map[pid] for pid, sc in sorted_tally if sc == top_score]
                chosen = tie_breaker(tied_props)
                return ArbitrationResult(
                    selected_proposal=chosen,
                    consensus_reached=True,
                    strategy_used=active_strategy,
                    confidence_score=top_ratio,
                    vote_distribution=tally,
                    reasoning="Tie resolved via tie-breaker.",
                )
            return ArbitrationResult(
                selected_proposal=None,
                consensus_reached=False,
                strategy_used=active_strategy,
                confidence_score=top_ratio,
                vote_distribution=tally,
                reasoning="Tied vote without tie-breaker.",
            )

        if top_ratio >= req_threshold:
            dissenters = []
            if votes is not None:
                dissenters = [v.agent_id for v in votes if v.proposal_id != top_id]
            else:
                dissenters = [p.agent_id for p in proposals if p.proposal_id != top_id]

            return ArbitrationResult(
                selected_proposal=prop_map[top_id],
                consensus_reached=True,
                strategy_used=active_strategy,
                confidence_score=top_ratio,
                vote_distribution=tally,
                dissenting_agents=dissenters,
                reasoning=f"Consensus reached under {active_strategy.value} (ratio: {top_ratio:.2f})",
            )

        return ArbitrationResult(
            selected_proposal=None,
            consensus_reached=False,
            strategy_used=active_strategy,
            confidence_score=top_ratio,
            vote_distribution=tally,
            reasoning=f"Threshold not met: {top_ratio:.2f} < {req_threshold:.2f}",
        )
