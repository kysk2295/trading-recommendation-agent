from __future__ import annotations

from itertools import pairwise

from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipBoundedTradeHistoryAttestation,
    AlpacaSipTradeStreamSessionEvidence,
)
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
        return _coverage(_checked_batch(batch), continuity_attested=False)
    except (AttributeError, TypeError, ValueError):
        raise CanonicalHistoryCoverageError from None


def assess_alpaca_sip_bounded_trade_history_coverage(
    batch: CanonicalDatasetBatch,
    attestation: AlpacaSipBoundedTradeHistoryAttestation,
) -> CanonicalHistoryCoverage:
    try:
        checked = _checked_batch(batch)
        if type(attestation) is not AlpacaSipBoundedTradeHistoryAttestation:
            raise CanonicalHistoryCoverageError
        receipt_ids = tuple(receipt.receipt_id for receipt in checked.raw_manifest.receipts)
        identity_prefix = f"{attestation.config.market_date.isoformat()}:{attestation.config.symbol}:"
        if (
            attestation.config.market_date != checked.partition.market_date
            or set(attestation.receipt_ids) != set(receipt_ids)
            or not attestation.subscribed_at <= checked.raw_manifest.received_at_start
            or checked.raw_manifest.received_at_end > attestation.completed_at
            or any(
                event.provider_event_id is None or not event.provider_event_id.startswith(identity_prefix)
                for event in checked.events
            )
        ):
            raise CanonicalHistoryCoverageError
        return _coverage(checked, continuity_attested=True)
    except (AttributeError, TypeError, ValueError):
        raise CanonicalHistoryCoverageError from None


def assess_alpaca_sip_multi_epoch_trade_history_coverage(
    batch: CanonicalDatasetBatch,
    sessions: tuple[AlpacaSipTradeStreamSessionEvidence, ...],
) -> CanonicalHistoryCoverage:
    try:
        checked = _checked_batch(batch)
        if (
            type(sessions) is not tuple
            or len(sessions) < 2
            or any(type(item) is not AlpacaSipTradeStreamSessionEvidence for item in sessions)
        ):
            raise CanonicalHistoryCoverageError
        ordered = tuple(sorted(sessions, key=lambda item: (item.authorized_at, item.connection_epoch)))
        config = sessions[0].config
        receipt_owners = {receipt_id: session for session in sessions for receipt_id in session.receipt_ids}
        receipt_ids = tuple(receipt.receipt_id for receipt in checked.raw_manifest.receipts)
        if (
            sessions != ordered
            or any(item.config != config for item in sessions)
            or config.market_date != checked.partition.market_date
            or len(receipt_owners) != sum(len(item.receipt_ids) for item in sessions)
            or set(receipt_owners) != set(receipt_ids)
            or any(previous.terminal_at > following.authorized_at for previous, following in pairwise(sessions))
            or any(
                not receipt_owners[receipt.receipt_id].subscribed_at
                <= receipt.received_at
                <= receipt_owners[receipt.receipt_id].terminal_at
                for receipt in checked.raw_manifest.receipts
            )
        ):
            raise CanonicalHistoryCoverageError
        identity_prefix = f"{config.market_date.isoformat()}:{config.symbol}:"
        if any(
            event.provider_event_id is None or not event.provider_event_id.startswith(identity_prefix)
            for event in checked.events
        ):
            raise CanonicalHistoryCoverageError
        return _coverage(checked, continuity_attested=False)
    except (AttributeError, TypeError, ValueError):
        raise CanonicalHistoryCoverageError from None


def _checked_batch(batch: CanonicalDatasetBatch) -> CanonicalDatasetBatch:
    if type(batch) is not CanonicalDatasetBatch:
        raise CanonicalHistoryCoverageError
    checked = CanonicalDatasetBatch.model_validate(dict(batch.__dict__))
    if (
        checked.partition.source_id != _SOURCE
        or checked.partition.event_type != "trade"
        or checked.raw_manifest.source_id != _RAW_SOURCE
    ):
        raise CanonicalHistoryCoverageError
    observed_through = max(event.normalized_at for event in checked.events)
    _ = active_canonical_events_as_of(checked.events, as_of=observed_through)
    return checked


def _coverage(
    checked: CanonicalDatasetBatch,
    *,
    continuity_attested: bool,
) -> CanonicalHistoryCoverage:
    receipt_ids = {receipt.receipt_id for receipt in checked.raw_manifest.receipts}
    raw_first_verified = bool(receipt_ids) and all(event.raw_receipt_ref in receipt_ids for event in checked.events)
    correction_observed = any(event.operation is CanonicalEventOperation.CORRECTION for event in checked.events)
    tombstone_observed = any(event.operation is CanonicalEventOperation.TOMBSTONE for event in checked.events)
    return CanonicalHistoryCoverage(
        source_id=_SOURCE,
        event_type="trade",
        observed_from=min(event.normalized_at for event in checked.events),
        observed_through=max(event.normalized_at for event in checked.events),
        raw_first_verified=raw_first_verified,
        correction_required=True,
        correction_supported=True,
        correction_observed=correction_observed,
        tombstone_required=True,
        tombstone_supported=True,
        tombstone_observed=tombstone_observed,
        continuity_attested=continuity_attested,
        complete_history=raw_first_verified and continuity_attested,
        reason_codes=() if continuity_attested else ("continuity_unattested",),
    )


__all__ = (
    "assess_alpaca_sip_bounded_trade_history_coverage",
    "assess_alpaca_sip_multi_epoch_trade_history_coverage",
    "assess_alpaca_sip_trade_history_coverage",
)
