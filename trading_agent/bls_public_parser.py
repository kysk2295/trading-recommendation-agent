from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from trading_agent.bls_public_models import (
    BlsMacroSnapshot,
    BlsObservation,
    BlsPublicError,
    BlsPublicRawResponse,
    BlsPublicRequest,
    BlsSeriesSnapshot,
)
from trading_agent.bls_public_provider_models import (
    BlsProviderObservation,
    BlsProviderResponse,
)


def parse_bls_macro_snapshot(
    request: BlsPublicRequest,
    response: BlsPublicRawResponse,
) -> BlsMacroSnapshot:
    try:
        if (
            response.request_id != request.request_id
            or response.status_code != 200
            or response.content_type != "application/json"
        ):
            raise BlsPublicError
        provider = BlsProviderResponse.model_validate_json(response.raw_payload)
        observed = tuple(item.seriesID for item in provider.Results.series)
        if len(observed) != len(set(observed)) or set(observed) != set(request.series_ids):
            raise BlsPublicError
        by_id = {item.seriesID: item for item in provider.Results.series}
        series = tuple(
            BlsSeriesSnapshot(
                series_id=series_id,
                observations=_observations(by_id[series_id].data),
            )
            for series_id in request.series_ids
        )
        return BlsMacroSnapshot(
            request_id=request.request_id,
            raw_receipt_id=response.receipt_id,
            requested_series_ids=request.series_ids,
            start_year=request.start_year,
            end_year=request.end_year,
            observed_at=response.received_at,
            series=series,
        )
    except BlsPublicError:
        raise
    except (InvalidOperation, KeyError, TypeError, ValidationError, ValueError):
        raise BlsPublicError from None


def _observations(
    values: tuple[BlsProviderObservation, ...],
) -> tuple[BlsObservation, ...]:
    return tuple(
        sorted(
            (_observation(item) for item in values),
            key=lambda item: (item.year, item.period),
            reverse=True,
        )
    )


def _observation(item: BlsProviderObservation) -> BlsObservation:
    footnotes = tuple(
        footnote
        for footnote in item.footnotes
        if footnote.code is not None or footnote.text is not None
    )
    value = None if item.value == "-" else Decimal(item.value)
    return BlsObservation(
        year=int(item.year),
        period=item.period,
        period_name=item.periodName,
        value=value,
        is_latest=item.latest == "true",
        footnotes=footnotes,
    )


__all__ = ("parse_bls_macro_snapshot",)
