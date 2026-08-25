"""
tests/test_elasticity_governor.py
Unit tests for the Dynamic Elasticity Governor.
"""

from orchestrator.elasticity_governor import ComputeTier, DynamicElasticityGovernor


def test_micro_complexity_allocation():
    simple_code = """
def add(a, b):
    return a + b
"""
    score = DynamicElasticityGovernor.calculate_complexity(simple_code)
    assert score < 25.0
    budget = DynamicElasticityGovernor.allocate(score)
    assert budget.tier == ComputeTier.TIER_1_MICRO
    assert budget.thinking_tokens == 2048
    assert budget.speculative_workers == 1


def test_moderate_complexity_allocation():
    moderate_code = """
def process_data(items):
    res = []
    for item in items:
        if item.is_valid():
            if item.score > 10:
                res.append(item.value)
            elif item.score > 5:
                res.append(item.value / 2)
            else:
                res.append(0)
    return res
"""
    score = DynamicElasticityGovernor.calculate_complexity(moderate_code)
    budget = DynamicElasticityGovernor.allocate(score)
    assert budget.tier in (ComputeTier.TIER_1_MICRO, ComputeTier.TIER_2_MODERATE)
    assert budget.thinking_tokens in (2048, 8192)


def test_macro_complexity_allocation():
    # Large synthetic complex function
    lines = ["def complex_state_machine(state, event):"]
    for i in range(30):
        lines.append(f"    if event == {i}:")
        lines.append(f"        if state.cond_{i} and state.ready:")
        lines.append(f"            return state.apply_{i}()")
    lines.append("    return None")
    complex_code = "\n".join(lines)

    score = DynamicElasticityGovernor.calculate_complexity(complex_code)
    assert score > 75.0
    budget = DynamicElasticityGovernor.allocate(score)
    assert budget.tier == ComputeTier.TIER_3_MACRO
    assert budget.thinking_tokens == 16384
    assert budget.speculative_workers == 3
    assert budget.hard_token_cap == 45000
