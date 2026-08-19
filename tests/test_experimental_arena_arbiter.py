"""Unit tests for stripped and hardened ArenaArbiter multi-agent consensus module."""

from __future__ import annotations

import pytest
from orchestrator.experimental.arena_arbiter import (
    ArenaArbiter,
    ConsensusStrategy,
    Proposal,
    AgentVote,
    ArbitrationResult,
)


def test_unanimous_consensus():
    arbiter = ArenaArbiter(default_strategy=ConsensusStrategy.UNANIMOUS)
    p1 = Proposal(proposal_id="p1", agent_id="a1", content="Plan A")
    p2 = Proposal(proposal_id="p2", agent_id="a2", content="Plan A")
    
    res = arbiter.resolve_consensus([p1, p2])
    assert res.consensus_reached is True
    assert res.selected_proposal == p1

    p3 = Proposal(proposal_id="p3", agent_id="a3", content="Plan B")
    res_diff = arbiter.resolve_consensus([p1, p3])
    assert res_diff.consensus_reached is False


def test_majority_consensus():
    arbiter = ArenaArbiter(default_strategy=ConsensusStrategy.MAJORITY_VOTE)
    p1 = Proposal(proposal_id="p1", agent_id="a1", content="Plan A")
    p2 = Proposal(proposal_id="p2", agent_id="a2", content="Plan B")

    votes = [
        AgentVote(agent_id="a1", proposal_id="p1"),
        AgentVote(agent_id="a2", proposal_id="p1"),
        AgentVote(agent_id="a3", proposal_id="p2"),
    ]
    res = arbiter.resolve_consensus([p1, p2], votes=votes)
    assert res.consensus_reached is True
    assert res.selected_proposal == p1
    assert "a3" in res.dissenting_agents


def test_supermajority_consensus():
    arbiter = ArenaArbiter(default_strategy=ConsensusStrategy.SUPERMAJORITY, supermajority_threshold=0.66)
    p1 = Proposal(proposal_id="p1", agent_id="a1", content="Plan A")
    p2 = Proposal(proposal_id="p2", agent_id="a2", content="Plan B")

    # Split 50/50 fails supermajority
    votes = [
        AgentVote(agent_id="a1", proposal_id="p1"),
        AgentVote(agent_id="a2", proposal_id="p2"),
    ]
    res = arbiter.resolve_consensus([p1, p2], votes=votes)
    assert res.consensus_reached is False

    # 3 of 4 votes (75%) passes supermajority
    votes_pass = [
        AgentVote(agent_id="a1", proposal_id="p1"),
        AgentVote(agent_id="a2", proposal_id="p1"),
        AgentVote(agent_id="a3", proposal_id="p1"),
        AgentVote(agent_id="a4", proposal_id="p2"),
    ]
    res_pass = arbiter.resolve_consensus([p1, p2], votes=votes_pass)
    assert res_pass.consensus_reached is True
    assert res_pass.selected_proposal == p1


def test_tie_breaking():
    arbiter = ArenaArbiter(default_strategy=ConsensusStrategy.MAJORITY_VOTE)
    p1 = Proposal(proposal_id="p1", agent_id="a1", content="Plan A", score=10.0)
    p2 = Proposal(proposal_id="p2", agent_id="a2", content="Plan B", score=20.0)

    votes = [
        AgentVote(agent_id="a1", proposal_id="p1"),
        AgentVote(agent_id="a2", proposal_id="p2"),
    ]

    res_tied = arbiter.resolve_consensus([p1, p2], votes=votes)
    assert res_tied.consensus_reached is False

    res_broken = arbiter.resolve_consensus(
        [p1, p2], votes=votes, tie_breaker=lambda props: max(props, key=lambda p: p.score)
    )
    assert res_broken.consensus_reached is True
    assert res_broken.selected_proposal == p2
