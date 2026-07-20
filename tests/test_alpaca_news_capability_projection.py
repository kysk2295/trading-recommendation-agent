from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.alpaca_news_capability_projection import (
    AlpacaNewsCapabilityProjectionError,
    project_alpaca_news_capability,
)
from trading_agent.alpaca_news_models import (
    AlpacaNewsFailure,
    AlpacaNewsRequest,
    AlpacaNewsRun,
    AlpacaNewsRunStatus,
)
from trading_agent.data_capability_models import (
    DataCorrectionPolicy,
    DataHealthState,
    DataUse,
    RedistributionPolicy,
    TimestampSemantic,
)

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
COMPLETED = START + dt.timedelta(hours=1, seconds=3)


def test_projects_successful_bounded_news_run_without_realtime_overclaim() -> None:
    projection = project_alpaca_news_capability(_run())
    capability = projection.capability
    entitlement = projection.entitlement

    assert projection.complete is True
    assert capability.source_id.canonical_id == "alpaca/news"
    assert capability.health_state is DataHealthState.COMPLETE
    assert capability.universe == "us_equities:bounded_symbols"
    assert capability.historical_from == START.date()
    assert capability.timestamp_semantics == (
        TimestampSemantic.PROVIDER_TIME,
        TimestampSemantic.PUBLISHED_AT,
        TimestampSemantic.RECEIVED_AT,
    )
    assert capability.observed_completeness_bps == 10_000
    assert capability.retention.correction_policy is DataCorrectionPolicy.APPEND_CORRECTION
    assert entitlement.permitted_uses == (
        DataUse.HISTORICAL_RESEARCH,
        DataUse.SHADOW_FORWARD,
    )
    assert entitlement.real_time is False
    assert entitlement.redistribution_policy is RedistributionPolicy.NONE


def test_failed_run_projects_failed_health_without_claiming_coverage() -> None:
    failed = _run(
        status=AlpacaNewsRunStatus.FAILED,
        failure=AlpacaNewsFailure.TRANSPORT,
        receipt_ids=(),
        page_count=0,
        article_count=0,
        latest_event_at=None,
    )

    projection = project_alpaca_news_capability(failed)

    assert projection.complete is False
    assert projection.capability.health_state is DataHealthState.FAILED
    assert projection.capability.historical_from is None
    assert projection.capability.observed_completeness_bps == 0


def test_rejects_fabricated_event_time_after_assessment() -> None:
    run = _run().model_copy(update={"latest_event_at": COMPLETED + dt.timedelta(seconds=1)})

    with pytest.raises(AlpacaNewsCapabilityProjectionError):
        project_alpaca_news_capability(run)


def _run(
    *,
    status: AlpacaNewsRunStatus = AlpacaNewsRunStatus.SUCCESS,
    failure: AlpacaNewsFailure | None = None,
    receipt_ids: tuple[str, ...] = ("a" * 64,),
    page_count: int = 1,
    article_count: int = 1,
    latest_event_at: dt.datetime | None = START + dt.timedelta(minutes=31),
) -> AlpacaNewsRun:
    return AlpacaNewsRun(
        request=AlpacaNewsRequest(
            collection_id="capability-news-001",
            symbols=("AAPL",),
            start_at=START,
            end_at=START + dt.timedelta(hours=1),
            limit=50,
            max_pages=2,
        ),
        started_at=START,
        completed_at=COMPLETED,
        status=status,
        failure_code=failure,
        receipt_ids=receipt_ids,
        page_count=page_count,
        article_count=article_count,
        latest_event_at=latest_event_at,
    )
