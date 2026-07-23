from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from trading_agent.cftc_tff_models import (
    CftcTffCategory,
    CftcTffError,
    CftcTffRawResponse,
    CftcTffRequest,
)
from trading_agent.cftc_tff_parser import parse_cftc_tff_context

FIXTURE = Path(__file__).parent / "fixtures/cftc_tff/es_latest_two.json"
RECEIVED = dt.datetime(2026, 7, 24, 6, 0, tzinfo=dt.UTC)


def test_latest_two_reports_project_category_net_changes() -> None:
    # Given
    request = _request()
    response = _response(FIXTURE.read_bytes(), request)

    # When
    context = parse_cftc_tff_context(request, response)

    # Then
    assert context.latest_report_date == dt.date(2026, 7, 14)
    assert context.previous_report_date == dt.date(2026, 7, 7)
    assert len(context.categories) == 5
    leveraged = context.categories[2]
    assert leveraged.category is CftcTffCategory.LEVERAGED_MONEY
    assert leveraged.current_net == -365_002
    assert leveraged.previous_net == -361_875
    assert leveraged.weekly_change == -3_127
    assert context.observed_at == RECEIVED


@pytest.mark.parametrize(
    "raw_payload",
    (
        FIXTURE.read_bytes().replace(b'"13874A"', b'"13874B"', 1),
        FIXTURE.read_bytes().replace(
            b'"2026-07-07T00:00:00.000"',
            b'"2026-07-14T00:00:00.000"',
        ),
        FIXTURE.read_bytes().replace(b'"FutOnly"', b'"Combined"', 1),
        FIXTURE.read_bytes().replace(b'"142914"', b'"-142914"', 1),
        FIXTURE.read_bytes().replace(b'"1941500"', b'"1941501"', 1),
    ),
)
def test_invalid_provider_rows_are_rejected(
    raw_payload: bytes,
) -> None:
    # Given
    request = _request()
    response = _response(raw_payload, request)

    # When/Then
    with pytest.raises(CftcTffError):
        _ = parse_cftc_tff_context(request, response)


def test_report_after_requested_through_date_is_rejected() -> None:
    # Given
    request = CftcTffRequest(
        collection_id="es-tff-20260713",
        contract_market_code="13874A",
        through_date=dt.date(2026, 7, 13),
    )
    response = _response(FIXTURE.read_bytes(), request)

    # When/Then
    with pytest.raises(CftcTffError):
        _ = parse_cftc_tff_context(request, response)


def _request() -> CftcTffRequest:
    return CftcTffRequest(
        collection_id="es-tff-20260724",
        contract_market_code="13874A",
        through_date=dt.date(2026, 7, 24),
    )


def _response(
    raw_payload: bytes,
    request: CftcTffRequest,
) -> CftcTffRawResponse:
    return CftcTffRawResponse(
        request_id=request.request_id,
        received_at=RECEIVED,
        status_code=200,
        content_type="application/json",
        raw_payload=raw_payload,
    )
