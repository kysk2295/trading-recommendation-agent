from __future__ import annotations

from types import ModuleType
from typing import Protocol, cast

import pytest
from pydantic import TypeAdapter

from tests.day_agent_version_learning_support import champion
from tests.day_strategy_capsule_support import builtin_capsule
from tests.us_forward_shadow_support import no_signal_source
from trading_agent.day_agent_challenger_builders import DerivedSourceRequest, render_derived_source
from trading_agent.day_agent_version_models import AgentVersionPatch

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonRecord = dict[str, JsonValue]


class _RenderedStrategy(Protocol):
    def observe(self, bar: JsonRecord, candidate: JsonRecord | None) -> JsonRecord | None: ...


class _StrategyFactory(Protocol):
    def __call__(self, context: dict[str, int]) -> _RenderedStrategy: ...


@pytest.mark.parametrize(
    "payload",
    (
        {"kind": "market_regime_policy", "rule": "trend_alignment", "confirmation_bars": 2},
        {"kind": "theme_selection_policy", "timing_window": "opening_30_minutes", "minimum_catalyst_count": 2},
        {"kind": "catalyst_interpretation_policy", "rule": "freshness_first", "maximum_age_minutes": 15},
        {"kind": "leader_ranking_policy", "feature": "relative_volume", "weight_bps": 5000},
        {"kind": "flow_interpretation_policy", "rule": "volume_confirmation", "confirmation_bars": 2},
        {"kind": "entry_policy", "rule": "breakout_confirmation", "confirmation_bars": 2},
        {"kind": "exit_policy", "rule": "trailing_structure", "trailing_window_bars": 2},
        {"kind": "execution_review_policy", "rule": "slippage_attribution", "review_window_sessions": 5},
    ),
)
def test_derived_patch_never_fabricates_a_signal_when_parent_returns_none(
    payload: dict[str, str | int],
) -> None:
    strategy = _rendered_strategy(payload, no_signal_source())

    assert strategy.observe(_semantic_bars()[-1], _semantic_candidate()) is None


def test_derived_patches_apply_only_their_declared_stage_semantics() -> None:
    parent = (
        "def create_strategy(context):\n"
        "    class Strategy:\n"
        "        def observe(self, bar, candidate):\n"
        "            return {'symbol': bar['symbol'], 'timestamp': bar['timestamp'], "
        "'entry': 100.0, 'stop': 90.0, 'rationale': 'parent signal'}\n"
        "    return Strategy()\n"
    )
    bars = _semantic_bars()
    candidate = _semantic_candidate()

    def observed(
        payload: dict[str, str | int],
        candidate_override: JsonRecord | None = None,
    ) -> JsonRecord | None:
        strategy = _rendered_strategy(payload, parent)
        result = None
        for bar in bars:
            result = strategy.observe(bar, candidate if candidate_override is None else candidate_override)
        return result

    unchanged: JsonRecord = {
        "symbol": "TEST", "timestamp": bars[-1]["timestamp"], "entry": 100.0,
        "stop": 90.0, "rationale": "parent signal",
    }
    gate_pairs = (
        (
            {"kind": "market_regime_policy", "rule": "trend_alignment", "confirmation_bars": 3},
            {"kind": "market_regime_policy", "rule": "trend_alignment", "confirmation_bars": 4},
        ),
        (
            {"kind": "theme_selection_policy", "timing_window": "opening_30_minutes", "minimum_catalyst_count": 2},
            {"kind": "theme_selection_policy", "timing_window": "opening_30_minutes", "minimum_catalyst_count": 4},
        ),
        (
            {"kind": "catalyst_interpretation_policy", "rule": "freshness_first", "maximum_age_minutes": 15},
            {"kind": "catalyst_interpretation_policy", "rule": "freshness_first", "maximum_age_minutes": 5},
        ),
        (
            {"kind": "flow_interpretation_policy", "rule": "volume_confirmation", "confirmation_bars": 3},
            {"kind": "flow_interpretation_policy", "rule": "volume_confirmation", "confirmation_bars": 4},
        ),
        (
            {"kind": "execution_review_policy", "rule": "slippage_attribution", "review_window_sessions": 5},
            {"kind": "execution_review_policy", "rule": "slippage_attribution", "review_window_sessions": 15},
        ),
    )
    for allowed, blocked in gate_pairs:
        assert observed(allowed) == unchanged
        assert observed(blocked) is None

    assert observed(
        {"kind": "market_regime_policy", "rule": "volatility_contraction", "confirmation_bars": 3}
    ) is None
    late_candidate = candidate | {"minutes_from_open": 45}
    assert observed(
        {"kind": "theme_selection_policy", "timing_window": "opening_30_minutes", "minimum_catalyst_count": 2},
        late_candidate,
    ) is None
    assert observed(
        {"kind": "theme_selection_policy", "timing_window": "opening_60_minutes", "minimum_catalyst_count": 2},
        late_candidate,
    ) == unchanged
    mismatched_catalyst = candidate | {"catalyst": "filing"}
    assert observed(
        {"kind": "catalyst_interpretation_policy", "rule": "freshness_first", "maximum_age_minutes": 15},
        mismatched_catalyst,
    ) == unchanged
    assert observed(
        {"kind": "catalyst_interpretation_policy", "rule": "confirmation_first", "maximum_age_minutes": 15},
        mismatched_catalyst,
    ) is None
    assert observed(
        {"kind": "flow_interpretation_policy", "rule": "spread_confirmation", "confirmation_bars": 3}
    ) is None
    assert observed(
        {"kind": "execution_review_policy", "rule": "fill_quality_attribution", "review_window_sessions": 5}
    ) is None

    leader_relative = observed(
        {"kind": "leader_ranking_policy", "feature": "relative_volume", "weight_bps": 5000}
    )
    leader_dollar = observed(
        {"kind": "leader_ranking_policy", "feature": "dollar_volume", "weight_bps": 5000}
    )
    leader_strict = observed(
        {"kind": "leader_ranking_policy", "feature": "relative_volume", "weight_bps": 9000}
    )
    assert leader_relative == unchanged
    assert leader_dollar is None
    assert leader_strict is None

    entry_breakout = observed(
        {"kind": "entry_policy", "rule": "breakout_confirmation", "confirmation_bars": 2}
    )
    entry_pullback = observed(
        {"kind": "entry_policy", "rule": "pullback_confirmation", "confirmation_bars": 2}
    )
    assert entry_breakout is not None and entry_pullback is not None
    assert entry_breakout["entry"] != entry_pullback["entry"]
    assert {key: entry_breakout[key] for key in unchanged if key != "entry"} == {
        key: unchanged[key] for key in unchanged if key != "entry"
    }
    assert {key: entry_pullback[key] for key in unchanged if key != "entry"} == {
        key: unchanged[key] for key in unchanged if key != "entry"
    }
    entry_longer = observed(
        {"kind": "entry_policy", "rule": "breakout_confirmation", "confirmation_bars": 4}
    )
    assert entry_longer is not None and entry_longer["entry"] != entry_breakout["entry"]

    exit_r_multiple = observed(
        {"kind": "exit_policy", "rule": "r_multiple_targets", "trailing_window_bars": 2}
    )
    exit_trailing = observed(
        {"kind": "exit_policy", "rule": "trailing_structure", "trailing_window_bars": 2}
    )
    assert exit_r_multiple is not None and exit_trailing is not None
    assert exit_r_multiple["stop"] != exit_trailing["stop"]
    assert {key: exit_r_multiple[key] for key in unchanged if key != "stop"} == {
        key: unchanged[key] for key in unchanged if key != "stop"
    }
    assert {key: exit_trailing[key] for key in unchanged if key != "stop"} == {
        key: unchanged[key] for key in unchanged if key != "stop"
    }
    exit_longer = observed(
        {"kind": "exit_policy", "rule": "r_multiple_targets", "trailing_window_bars": 4}
    )
    assert exit_longer is not None and exit_longer["stop"] != exit_r_multiple["stop"]


def _rendered_strategy(payload: dict[str, str | int], parent_source: str) -> _RenderedStrategy:
    patch = TypeAdapter(AgentVersionPatch).validate_python(payload)
    module = ModuleType("day_agent_challenger_semantics")
    source = render_derived_source(
        DerivedSourceRequest(champion(), builtin_capsule(), patch, "9" * 64, parent_source)
    )
    exec(compile(source, "<day-agent-challenger>", "exec"), module.__dict__)
    factory = cast("_StrategyFactory", module.create_strategy)
    return factory({"protocol_version": 1})


def _semantic_bars() -> tuple[JsonRecord, ...]:
    return tuple(
        {
            "symbol": "TEST", "timestamp": f"2026-08-21T13:{30 + index:02d}:00+00:00",
            "open": 96.0 + index, "high": 99.0 + index * 2, "low": 95.0 + index,
            "close": 97.0 + index, "volume": 100_000 + index * 10_000,
            "average_daily_volume": 1_000_000, "spread_bps": 8.0 + index,
            "prior_close": 96.0, "catalyst": "earnings",
        }
        for index in range(3)
    )


def _semantic_candidate() -> JsonRecord:
    return {
        "symbol": "TEST", "timestamp": "2026-08-21T13:32:00+00:00", "price": 99.0,
        "gap_pct": 6.0, "change_pct": 2.0, "relative_volume": 4.0,
        "cumulative_dollar_volume": 2_000_000.0, "spread_bps": 5.0, "catalyst": "earnings",
        "minutes_from_open": 20, "theme_catalyst_count": 3, "catalyst_age_minutes": 10,
        "execution_review_sessions": 10, "estimated_slippage_bps": 10.0, "fill_quality_bps": 30.0,
    }
