from __future__ import annotations

import datetime as dt
import hashlib
import importlib
from importlib.util import find_spec

import pytest
from pydantic import ValidationError

from trading_agent.day_learning_report_models import (
    CumulativeLineageSection,
    DailyLearningReport,
    ExecutionReportSection,
    MarketCloseReport,
    MarketCloseReportPayload,
    MarketFinalizationWatermark,
    NextSessionSection,
    ResearchReportSection,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.research_identity_models import MarketId

NOW = dt.datetime(2026, 8, 20, 22, 0, tzinfo=dt.UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def test_day_learning_report_modules_exist() -> None:
    # Given: the completed Day review and session-eligibility foundation.
    module_names = (
        "trading_agent.day_learning_report_models",
        "trading_agent.day_learning_report_store",
        "trading_agent.day_learning_reports",
    )

    # When: the market-close reporting surface is resolved.
    modules = tuple(find_spec(name) for name in module_names)

    # Then: models, immutable storage, and query-only projection have distinct modules.
    assert all(module is not None for module in modules)


def test_day_learning_report_model_api_is_explicit() -> None:
    # Given: the market-close report model module.
    module = importlib.import_module("trading_agent.day_learning_report_models")

    # When: its public immutable report contracts are inspected.
    names = {
        "CumulativeLineageSection",
        "DailyLearningReport",
        "ExecutionReportSection",
        "MarketCloseReport",
        "MarketCloseReportPayload",
        "MarketFinalizationWatermark",
        "NextSessionSection",
        "ResearchReportSection",
    }

    # Then: execution, research, lineage, and next-session facts are separate sections.
    assert names <= set(module.__all__)


def _payload(
    market_id: MarketId = MarketId.US_EQUITIES,
    *,
    session_date: dt.date = dt.date(2026, 8, 20),
    watermark_id: str = SHA_A,
    revision: int = 1,
    previous_report_id: str | None = None,
    cumulative_lineage_report_ids: tuple[str, ...] = (),
    cumulative_actual_return: float | None = None,
    cumulative_modeled_return: float | None = None,
) -> MarketCloseReportPayload:
    provider_read_only = market_id is MarketId.KR_EQUITIES
    session_offset = (session_date - dt.date(2026, 8, 20)).days
    finalized_at = NOW + dt.timedelta(days=session_offset)
    actual_return = None if provider_read_only else 0.004
    cumulative_actual = (
        None
        if provider_read_only
        else actual_return
        if cumulative_actual_return is None
        else cumulative_actual_return
    )
    return MarketCloseReportPayload(
        market_id=market_id,
        session_date=session_date,
        watermark=MarketFinalizationWatermark(
            watermark_id=watermark_id,
            market_id=market_id,
            session_date=session_date,
            finalized_through=finalized_at - dt.timedelta(minutes=15),
            source_event_ids=("close-final-1",),
        ),
        revision=revision,
        previous_report_id=previous_report_id,
        execution=ExecutionReportSection(
            market_id=market_id,
            actual_return=actual_return,
            modeled_return=0.006,
            filled_order_count=0 if provider_read_only else 2,
            unresolved_count=1,
            censored_count=1,
            provider_read_only=provider_read_only,
            eligibility_event_ids=() if provider_read_only else (SHA_B,),
        ),
        research=ResearchReportSection(
            market_id=market_id,
            attempted_variant_count=3,
            supported_count=1,
            refuted_count=1,
            inconclusive_count=1,
            modeled_return=0.006,
            evidence_ids=(SHA_A,),
        ),
        lineage=CumulativeLineageSection(
            market_id=market_id,
            report_count=len(cumulative_lineage_report_ids) + 1,
            cumulative_actual_return=cumulative_actual,
            cumulative_modeled_return=(
                0.006 if cumulative_modeled_return is None else cumulative_modeled_return
            ),
            lineage_report_ids=cumulative_lineage_report_ids,
        ),
        next_session=NextSessionSection(
            market_id=market_id,
            active_capsule_ids=(SHA_A,),
            queued_capsule_ids=(SHA_B,),
            reason_codes=("keep_supported_capsule",),
        ),
        finalized_at=finalized_at,
    )


def _report(payload: MarketCloseReportPayload) -> MarketCloseReport:
    report_id = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    return MarketCloseReport(report_id=report_id, payload=payload)


def test_kr_report_is_read_only_and_separates_actual_from_modeled_return() -> None:
    # Given: a Korean market-close report payload.
    report = _report(_payload(MarketId.KR_EQUITIES))

    # When: its execution and research returns are inspected.
    values = (
        report.payload.execution.actual_return,
        report.payload.execution.modeled_return,
        report.payload.execution.provider_read_only,
    )

    # Then: no modeled result is presented as an actual broker return.
    assert values == (None, 0.006, True)


def test_report_revision_requires_an_immutable_previous_report_link() -> None:
    # Given: report fields marked as revision two without a previous report.
    payload = _payload().model_dump(mode="python")

    # When / Then: the revision chain cannot be created with a missing parent.
    with pytest.raises(ValidationError, match="revision"):
        _ = MarketCloseReportPayload.model_validate(payload | {"revision": 2, "previous_report_id": None})


def test_report_revision_and_cross_session_cumulative_lineage_are_independent() -> None:
    # Given: a second market session's initial final report linked to the prior session.
    first = _report(_payload())

    # When: the next day remains revision one while its cumulative report count becomes two.
    second = _report(
        _payload(
            session_date=dt.date(2026, 8, 21),
            watermark_id=SHA_C,
            cumulative_lineage_report_ids=(first.report_id,),
            cumulative_actual_return=0.008016,
            cumulative_modeled_return=0.012036,
        )
    )

    # Then: revision identity and cumulative trading-session lineage carry distinct counts.
    assert second.payload.revision == 1
    assert second.payload.previous_report_id is None
    assert second.payload.lineage.report_count == 2
    assert second.payload.lineage.lineage_report_ids == (first.report_id,)


def test_daily_learning_facade_links_markets_without_combined_return() -> None:
    # Given: separate verified US and KR final report IDs.
    facade = DailyLearningReport(
        us_report_id=SHA_A,
        kr_report_id=SHA_B,
        generated_at=NOW,
        query_only=True,
    )

    # When: the façade schema is inspected.
    fields = set(DailyLearningReport.model_fields)

    # Then: it links reports but exposes no cross-market return aggregation.
    assert facade.us_report_id != facade.kr_report_id
    assert "combined_return" not in fields
    assert "aggregate_return" not in fields
