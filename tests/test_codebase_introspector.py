"""
tests/test_codebase_introspector.py
Unit tests for Min-Max Normalized UCB1 Exploration Engine.
"""

from orchestrator.codebase_introspector import NormalizedExplorationEngine, ModuleProfile


def test_ucb1_exploration_normalization():
    engine = NormalizedExplorationEngine(exploration_constant=1.414)

    mod_high = ModuleProfile(path="high.py", symbol="func_h", raw_complexity=100.0)
    mod_low = ModuleProfile(path="low.py", symbol="func_l", raw_complexity=20.0)
    modules = [mod_high, mod_low]

    # First pick should favor high complexity
    first = engine.select_hotspot(modules)
    assert first == mod_high
    assert mod_high.visits == 1

    # If mod_high is put on cooldown, mod_low must be selected
    engine.record_success(mod_high, cooldown_cycles=3)
    second = engine.select_hotspot(modules)
    assert second == mod_low
    assert mod_low.visits == 1

    # Ticking cooldowns
    engine.tick_cooldowns(modules)
    assert mod_high.cooldown_remaining == 2
