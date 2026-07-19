from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from trading_agent.intraday_feature_reobservation import (
    IntradayFeatureReobservationError,
    reobserve_ready_intraday_feature,
)


def test_ready_feature_can_be_reobserved_within_same_completed_minute() -> None:
    original = quote_fixtures._snapshot()
    observed_at = original.observed_at + dt.timedelta(seconds=2)

    reobserved = reobserve_ready_intraday_feature(original, observed_at)

    assert reobserved == dataclasses.replace(original, observed_at=observed_at)


@pytest.mark.parametrize(
    "observed_at",
    (
        quote_fixtures._snapshot().observed_at - dt.timedelta(microseconds=1),
        quote_fixtures._snapshot().observed_at.replace(second=0, microsecond=0) + dt.timedelta(minutes=1),
    ),
)
def test_backward_or_next_minute_reobservation_is_blocked(observed_at: dt.datetime) -> None:
    with pytest.raises(IntradayFeatureReobservationError):
        _ = reobserve_ready_intraday_feature(quote_fixtures._snapshot(), observed_at)
