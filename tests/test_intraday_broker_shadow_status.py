from __future__ import annotations

import datetime as dt

from trading_agent.intraday_broker_shadow_models import BrokerShadowTradePair
from trading_agent.intraday_broker_shadow_statistics import (
    assess_broker_shadow_pairs,
)


def test_mature_positive_paired_sample_becomes_broker_shadow_ready() -> None:
    # Given: 100 exact pairs across 60 sessions with positive broker and shadow edge.
    dates = tuple(dt.date(2026, 1, 2) + dt.timedelta(days=index) for index in range(60))
    pairs = tuple(
        BrokerShadowTradePair(
            recommendation_id=f"recommendation-{index}",
            session_date=dates[index % len(dates)],
            symbol=f"S{index:03d}",
            strategy_version="orb-v1",
            broker_entry=10.0,
            broker_exit=10.2 if index < 80 else 9.9,
            shadow_entry=10.0,
            shadow_exit=10.2 if index < 80 else 9.9,
            broker_net_return=0.02 if index < 80 else -0.01,
            shadow_net_return=0.02 if index < 80 else -0.01,
            return_difference=0.0,
        )
        for index in range(100)
    )

    # When: the fixed promotion diagnostic evaluates the mature sample.
    assessment = assess_broker_shadow_pairs(pairs, 0)

    # Then: the evidence is ready without granting lifecycle or order authority.
    assert assessment.status.value == "broker_shadow_ready"
    assert assessment.blockers == ()
    assert assessment.broker_metrics.trade_count == 100
    assert assessment.broker_metrics.profit_factor == 8.0
    assert assessment.broker_metrics.mean_ci_low is not None
    assert assessment.broker_metrics.mean_ci_low > 0.0
