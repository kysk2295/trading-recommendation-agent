from __future__ import annotations

from trading_agent.canonical_dataset_models import CanonicalDatasetBatch
from trading_agent.canonical_event_history import active_canonical_events_as_of
from trading_agent.canonical_event_models import CanonicalEventOperation
from trading_agent.canonical_history_coverage import (
    CanonicalHistoryCoverage,
    CanonicalHistoryCoverageError,
)
from trading_agent.data_capability_models import DataSourceId

_SOURCE = DataSourceId(provider="alpaca", feed="sip")
_RAW_SOURCE = "alpaca.sip.trades"


def assess_alpaca_sip_trade_history_coverage(
    batch: CanonicalDatasetBatch,
) -> CanonicalHistoryCoverage:
    try:
        if type(batch) is not CanonicalDatasetBatch:
            raise CanonicalHistoryCoverageError
        checked = CanonicalDatasetBatch.model_validate(dict(batch.__dict__))
        if (
            checked.partition.source_id != _SOURCE
            or checked.partition.event_type != "trade"
            or checked.raw_manifest.source_id != _RAW_SOURCE
        ):
            raise CanonicalHistoryCoverageError
        observed_from = min(event.normalized_at for event in checked.events)
        observed_through = max(event.normalized_at for event in checked.events)
        _ = active_canonical_events_as_of(checked.events, as_of=observed_through)
        receipt_ids = {receipt.receipt_id for receipt in checked.raw_manifest.receipts}
        raw_first_verified = bool(receipt_ids) and all(event.raw_receipt_ref in receipt_ids for event in checked.events)
        correction_observed = any(event.operation is CanonicalEventOperation.CORRECTION for event in checked.events)
        tombstone_observed = any(event.operation is CanonicalEventOperation.TOMBSTONE for event in checked.events)
        return CanonicalHistoryCoverage(
            source_id=_SOURCE,
            event_type="trade",
            observed_from=observed_from,
            observed_through=observed_through,
            raw_first_verified=raw_first_verified,
            correction_required=True,
            correction_supported=True,
            correction_observed=correction_observed,
            tombstone_required=True,
            tombstone_supported=True,
            tombstone_observed=tombstone_observed,
            continuity_attested=False,
            complete_history=False,
            reason_codes=("continuity_unattested",),
        )
    except (AttributeError, TypeError, ValueError):
        raise CanonicalHistoryCoverageError from None


__all__ = ("assess_alpaca_sip_trade_history_coverage",)
