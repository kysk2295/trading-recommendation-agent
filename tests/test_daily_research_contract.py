from __future__ import annotations

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.strategy_factory import StrategyMode


def test_current_intraday_contracts_have_canonical_global_lineage() -> None:
    contracts = tuple(strategy_contract(mode) for mode in StrategyMode)

    assert len({contract.strategy_version for contract in contracts}) == 4
    assert CURRENT_DATA_CONTRACT == (
        "completed_bars_only=true",
        "point_in_time_candidate_inputs=true",
        "source=KIS_read_only_rankings",
    )
    assert CURRENT_COST_MODEL == (
        "side_cost_bps=5,10,20",
        "same_bar_stop_target=stop_first",
        "time_exit=last_completed_bar_fallback",
    )
    assert SHADOW_PORTFOLIO_POLICY == (
        "max_ranked_candidates=10",
        "max_one_symbol_strategy_recommendation_per_day",
        "broker_orders=false",
    )


def test_strategy_version_identity_is_deterministic_and_code_coupled() -> None:
    first = strategy_version_identity(StrategyMode.ORB, "a" * 40)

    assert first == strategy_version_identity(StrategyMode.ORB, "a" * 40)
    assert first != strategy_version_identity(StrategyMode.ORB, "b" * 40)
    assert first.startswith("orb_5m_buffer5bp_volume1.5_v1-code-")
