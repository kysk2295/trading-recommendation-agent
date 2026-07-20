from __future__ import annotations

import datetime as dt
from typing import override

from trading_agent.alpaca_security_master_models import AlpacaSecurityMasterSnapshot
from trading_agent.security_master_models import resolve_instrument_alias
from trading_agent.us_news_catalyst_collection_models import UsNewsCatalystCollectionPlan
from trading_agent.us_news_catalyst_trial_models import (
    UsNewsCatalystCohortArtifact,
    UsNewsCatalystCohortStatus,
)
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionChannel,
)

_MAX_SECURITY_MASTER_AGE = dt.timedelta(days=1)
_MAX_COLLECTION_DELAY = dt.timedelta(minutes=2)


class InvalidUsNewsCatalystCollectionScopeError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst cohort collection scope is blocked"


def cohort_subscriptions(
    cohort: UsNewsCatalystCohortArtifact,
    security_master: AlpacaSecurityMasterSnapshot,
) -> tuple[DesiredMarketDataSubscription, ...]:
    if (
        type(cohort) is not UsNewsCatalystCohortArtifact
        or cohort.payload.status is not UsNewsCatalystCohortStatus.READY
        or type(security_master) is not AlpacaSecurityMasterSnapshot
        or security_master.observed_at > cohort.payload.observed_at
        or cohort.payload.observed_at - security_master.observed_at
        > _MAX_SECURITY_MASTER_AGE
    ):
        raise InvalidUsNewsCatalystCollectionScopeError
    symbols = tuple(
        sorted((*cohort.payload.treatment_symbols, *cohort.payload.control_symbols))
    )
    instruments = {item.value: item for item in security_master.instruments}
    channels = (SubscriptionChannel.QUOTE, SubscriptionChannel.TRADE)
    result: list[DesiredMarketDataSubscription] = []
    for symbol in symbols:
        instrument_id = resolve_instrument_alias(
            security_master.aliases,
            namespace="alpaca",
            value=symbol,
            as_of=cohort.payload.observed_at,
        )
        instrument = instruments[instrument_id]
        if instrument.valid_from > cohort.payload.observed_at or (
            instrument.valid_to is not None
            and cohort.payload.observed_at >= instrument.valid_to
        ):
            raise InvalidUsNewsCatalystCollectionScopeError
        result.append(DesiredMarketDataSubscription(instrument_id, symbol, channels))
    return tuple(result)


def validate_collection_time(
    cohort: UsNewsCatalystCohortArtifact,
    evaluated_at: dt.datetime,
) -> None:
    target = cohort.payload.observed_at + dt.timedelta(minutes=30)
    if (
        type(evaluated_at) is not dt.datetime
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
        or not target <= evaluated_at <= target + _MAX_COLLECTION_DELAY
    ):
        raise InvalidUsNewsCatalystCollectionScopeError


def validate_collection_plan(
    plan: UsNewsCatalystCollectionPlan,
    cohort: UsNewsCatalystCohortArtifact,
    security_master: AlpacaSecurityMasterSnapshot,
    subscriptions: tuple[DesiredMarketDataSubscription, ...],
) -> None:
    content = plan.content
    expected = tuple((item.symbol, item.instrument_id) for item in subscriptions)
    actual = tuple((item.symbol, item.instrument_id) for item in content.bindings)
    if (
        content.cohort_artifact_id != cohort.artifact_id
        or content.trial_id != cohort.payload.trial_id
        or content.session_date != cohort.payload.session_date
        or content.cohort_observed_at != cohort.payload.observed_at
        or content.security_master_snapshot_id != security_master.snapshot_id
        or actual != expected
    ):
        raise InvalidUsNewsCatalystCollectionScopeError


__all__ = (
    "InvalidUsNewsCatalystCollectionScopeError",
    "cohort_subscriptions",
    "validate_collection_plan",
    "validate_collection_time",
)
