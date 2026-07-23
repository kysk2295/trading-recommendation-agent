from __future__ import annotations

import datetime as dt
import hashlib
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.treasury_yield_artifact import (
    publish_treasury_yield_context,
)
from trading_agent.treasury_yield_models import (
    TreasuryMaturity,
    TreasuryYieldError,
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
)
from trading_agent.treasury_yield_parser import parse_treasury_yield_context

FIXTURE = Path(__file__).parent / "fixtures/treasury_yield_curve/2026-07.xml"
RECEIVED = dt.datetime(2026, 7, 23, 1, 0, tzinfo=dt.UTC)


def test_latest_two_curves_project_changes_and_slopes() -> None:
    # Given
    request = _request()
    response = TreasuryYieldRawResponse(
        request_id=request.request_id,
        received_at=RECEIVED,
        status_code=200,
        content_type="application/xml",
        raw_payload=FIXTURE.read_bytes(),
    )

    # When
    context = parse_treasury_yield_context(request, response)

    # Then
    assert context.latest_date == dt.date(2026, 7, 22)
    assert context.previous_date == dt.date(2026, 7, 21)
    assert len(context.points) == 14
    two_year = context.points[7]
    assert two_year.maturity is TreasuryMaturity.TWO_YEAR
    assert two_year.current_percent == Decimal("3.75")
    assert two_year.previous_percent == Decimal("3.72")
    assert two_year.change_bps == Decimal("3")
    assert context.ten_year_minus_two_year_bps == Decimal("33")
    assert context.ten_year_minus_three_month_bps == Decimal("43")
    assert context.thirty_year_minus_five_year_bps == Decimal("73")
    assert context.observed_at == RECEIVED


@pytest.mark.parametrize(
    "raw_payload",
    (
        FIXTURE.read_bytes().replace(
            b'<d:BC_1MONTH m:type="Edm.Double">3.60</d:BC_1MONTH>',
            b"",
            1,
        ),
        FIXTURE.read_bytes().replace(
            b"</m:properties>",
            b'<d:BC_1MONTH m:type="Edm.Double">3.60</d:BC_1MONTH></m:properties>',
            1,
        ),
        FIXTURE.read_bytes().replace(
            b"BC_1MONTH",
            b"BC_UNKNOWN",
            2,
        ),
        FIXTURE.read_bytes().replace(b">3.60<", b">30.00<", 1),
    ),
)
def test_schema_drift_and_out_of_range_yields_are_rejected(
    raw_payload: bytes,
) -> None:
    # Given
    request = _request()
    response = TreasuryYieldRawResponse(
        request_id=request.request_id,
        received_at=RECEIVED,
        status_code=200,
        content_type="application/xml",
        raw_payload=raw_payload,
    )

    # When/Then
    with pytest.raises(TreasuryYieldError):
        _ = parse_treasury_yield_context(request, response)


def test_feed_without_two_rows_at_or_before_through_date_is_rejected() -> None:
    # Given
    request = TreasuryYieldRequest(
        collection_id="treasury-yield-20260701",
        through_date=dt.date(2026, 7, 1),
    )
    response = TreasuryYieldRawResponse(
        request_id=request.request_id,
        received_at=RECEIVED,
        status_code=200,
        content_type="application/xml",
        raw_payload=FIXTURE.read_bytes(),
    )

    # When/Then
    with pytest.raises(TreasuryYieldError):
        _ = parse_treasury_yield_context(request, response)


def test_context_publisher_is_content_addressed_and_replay_safe(
    tmp_path: Path,
) -> None:
    # Given
    request = _request()
    context = parse_treasury_yield_context(
        request,
        TreasuryYieldRawResponse(
            request_id=request.request_id,
            received_at=RECEIVED,
            status_code=200,
            content_type="application/xml",
            raw_payload=FIXTURE.read_bytes(),
        ),
    )

    # When
    path, created = publish_treasury_yield_context(tmp_path, context)
    artifact_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    replay_path, replay_created = publish_treasury_yield_context(
        tmp_path,
        context,
    )

    # Then
    assert path.name == f"treasury_yield_curve_context_{context.context_id}.json"
    assert created is True
    assert replay_path == path
    assert replay_created is False
    assert hashlib.sha256(replay_path.read_bytes()).hexdigest() == artifact_sha256
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def _request() -> TreasuryYieldRequest:
    return TreasuryYieldRequest(
        collection_id="treasury-yield-20260724",
        through_date=dt.date(2026, 7, 24),
    )
