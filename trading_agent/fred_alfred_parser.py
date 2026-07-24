from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from trading_agent.fred_alfred_models import (
    FredAlfredError,
    FredAlfredRequest,
    FredRawReceipt,
    FredSourceMode,
)
from trading_agent.fred_alfred_provider_models import FredProviderResponse
from trading_agent.fred_alfred_snapshot_models import (
    FredAlfredSnapshot,
    FredObservation,
)


def parse_fred_alfred_snapshot(
    request: FredAlfredRequest,
    receipt: FredRawReceipt,
) -> FredAlfredSnapshot:
    try:
        if (
            receipt.request_id != request.request_id
            or receipt.status_code != 200
            or receipt.content_type != "application/json"
        ):
            raise FredAlfredError
        provider = FredProviderResponse.model_validate_json(receipt.raw_payload)
        if (
            provider.observation_start != request.observation_start
            or provider.observation_end != request.observation_end
            or provider.limit != request.limit
            or provider.count != len(provider.observations)
            or provider.count > request.limit
            or not provider.observations
        ):
            raise FredAlfredError
        if request.source_mode is FredSourceMode.ALFRED and (
            provider.realtime_start != request.vintage_date
            or provider.realtime_end != request.vintage_date
        ):
            raise FredAlfredError
        return FredAlfredSnapshot(
            request_id=request.request_id,
            raw_receipt_id=receipt.receipt_id,
            observed_at=receipt.received_at,
            source_mode=request.source_mode,
            series_id=request.series_id,
            observation_start=request.observation_start,
            observation_end=request.observation_end,
            vintage_date=request.vintage_date,
            units=provider.units,
            observations=tuple(
                FredObservation(
                    realtime_start=item.realtime_start,
                    realtime_end=item.realtime_end,
                    observation_date=item.date,
                    value=None if item.value == "." else Decimal(item.value),
                )
                for item in provider.observations
            ),
        )
    except FredAlfredError:
        raise
    except (InvalidOperation, TypeError, ValidationError, ValueError):
        raise FredAlfredError from None


__all__ = ("parse_fred_alfred_snapshot",)
