from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from trading_agent.data_capability_models import (
    DataCorrectionPolicy,
    DataHealthState,
    DataUse,
    TimestampSemantic,
)
from trading_agent.sec_edgar_capability_evidence import SecCapabilityEvidence
from trading_agent.sec_edgar_capability_projection import (
    SecCapabilityProjectionError,
    project_sec_edgar_capability,
)
from trading_agent.sec_edgar_models import SecCollectionStatus

ASSESSED_AT = dt.datetime(2026, 7, 20, 14, 1, 5, tzinfo=dt.UTC)
EVENT_AT = ASSESSED_AT - dt.timedelta(seconds=5)
COMPLETE_EVIDENCE = SecCapabilityEvidence(
    parent_run_id="a" * 64,
    parent_status=SecCollectionStatus.SUCCESS,
    assessed_at=ASSESSED_AT,
    latest_event_received_at=EVENT_AT,
    latest_source_heartbeat_at=ASSESSED_AT,
    historical_from=dt.date(2025, 12, 30),
    declared_slice_count=2,
    successful_slice_count=2,
    failed_slice_count=0,
    missing_slice_count=0,
    filing_count=3,
)


@pytest.mark.parametrize(
    ("evidence", "health", "completeness", "complete"),
    (
        (COMPLETE_EVIDENCE, DataHealthState.COMPLETE, 10_000, True),
        (
            replace(
                COMPLETE_EVIDENCE,
                successful_slice_count=1,
                missing_slice_count=1,
                filing_count=2,
            ),
            DataHealthState.INCOMPLETE,
            5_000,
            False,
        ),
        (
            replace(
                COMPLETE_EVIDENCE,
                successful_slice_count=1,
                failed_slice_count=1,
                filing_count=2,
            ),
            DataHealthState.DEGRADED,
            5_000,
            False,
        ),
        (
            replace(
                COMPLETE_EVIDENCE,
                parent_status=SecCollectionStatus.FAILED,
                declared_slice_count=1,
                successful_slice_count=0,
                failed_slice_count=1,
                filing_count=0,
                latest_event_received_at=None,
                historical_from=None,
            ),
            DataHealthState.FAILED,
            0,
            False,
        ),
    ),
)
def test_projects_terminal_coverage_without_claiming_market_wide_health(
    evidence: SecCapabilityEvidence,
    health: DataHealthState,
    completeness: int,
    complete: bool,
) -> None:
    projection = project_sec_edgar_capability(evidence)

    assert projection.complete is complete
    assert projection.capability.health_state is health
    assert projection.capability.observed_completeness_bps == completeness
    assert projection.capability.universe == "us_equities:bounded_issuer"
    assert projection.successful_slice_count == evidence.successful_slice_count
    assert projection.declared_slice_count == evidence.declared_slice_count


def test_projects_canonical_sec_contract_and_actual_history_start() -> None:
    projection = project_sec_edgar_capability(COMPLETE_EVIDENCE)
    capability = projection.capability
    entitlement = projection.entitlement

    assert capability.source_id.canonical_id == "sec/edgar_submissions"
    assert capability.historical_from == dt.date(2025, 12, 30)
    assert capability.timestamp_semantics == (
        TimestampSemantic.PROVIDER_TIME,
        TimestampSemantic.RECEIVED_AT,
    )
    assert capability.rate_limits.requests_per_minute == 600
    assert capability.retention.correction_policy is DataCorrectionPolicy.APPEND_CORRECTION
    assert entitlement.source_id == capability.source_id
    assert entitlement.permitted_uses == (
        DataUse.HISTORICAL_RESEARCH,
        DataUse.SHADOW_FORWARD,
    )


@pytest.mark.parametrize(
    "mutation",
    (
        {"successful_slice_count": 3},
        {"missing_slice_count": -1},
        {"latest_source_heartbeat_at": ASSESSED_AT + dt.timedelta(seconds=1)},
        {"filing_count": 0},
    ),
)
def test_rejects_fabricated_or_internally_inconsistent_evidence(
    mutation: dict[str, int | dt.datetime],
) -> None:
    evidence = replace(COMPLETE_EVIDENCE, **mutation)

    with pytest.raises(SecCapabilityProjectionError):
        project_sec_edgar_capability(evidence)
