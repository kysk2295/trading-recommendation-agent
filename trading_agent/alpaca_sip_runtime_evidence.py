from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from trading_agent.alpaca_sip_runtime_evidence_store import AlpacaSipRuntimeEvidenceStore
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipMinutePage,
    AlpacaSipRuntimeBar,
    AlpacaSipRuntimeError,
    StoredAlpacaSipRawPage,
)
from trading_agent.canonical_dataset_models import CanonicalDatasetBatch, CanonicalDatasetPartition
from trading_agent.canonical_duckdb_replay import replay_canonical_dataset
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.canonical_parquet_writer import write_canonical_dataset_parquet
from trading_agent.data_capability_models import DataSourceId
from trading_agent.raw_object_manifest_models import RawReceipt, RawReceiptPayload
from trading_agent.raw_receipt_projection import project_raw_receipt_partition
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.security_master_models import DataMarketDomain

_RAW_SOURCE_ID = "alpaca.sip"
_IDENTITY_SCOPE = "us_equities.day_trading.runtime_features"
_SOURCE = DataSourceId(provider="alpaca", feed="sip")


class AlpacaSipRuntimeEvidenceProjector:
    __slots__ = ("_output_root", "_store")

    def __init__(self, store: AlpacaSipRuntimeEvidenceStore, output_root: Path) -> None:
        self._store = store
        self._output_root = output_root.resolve(strict=False)

    def project(
        self,
        page_set: AlpacaSipMinutePage,
        instrument_id: str,
        bars: tuple[AlpacaSipRuntimeBar, ...],
    ) -> ResearchInputIdentity:
        try:
            if (
                type(page_set) is not AlpacaSipMinutePage
                or type(instrument_id) is not str
                or not instrument_id
                or type(bars) is not tuple
                or not bars
                or any(type(bar) is not AlpacaSipRuntimeBar for bar in bars)
            ):
                raise AlpacaSipRuntimeError
            stored = tuple(self._store.append_page(page_set.request, page) for page in page_set.pages)
            manifest = project_raw_receipt_partition(
                tuple(sorted((_raw_receipt(page_set, item) for item in stored), key=lambda item: item.receipt_id)),
                source_id=_RAW_SOURCE_ID,
                market_date=page_set.request.session_date,
                parent_ledger_generation=max(item.generation for item in stored),
            )
            received_by_page = {item.page_index: item for item in stored}
            normalized_at = max(item.received_at for item in stored)
            events = tuple(_event(instrument_id, bar, received_by_page[bar.page_index], normalized_at) for bar in bars)
            projection_key = _projection_key(manifest.manifest_id, instrument_id, events)
            existing_directory = self._store.projection_directory(projection_key)
            if existing_directory is not None:
                replay = replay_canonical_dataset(existing_directory)
                if replay.raw_manifest_id != manifest.manifest_id or replay.event_count != len(events):
                    raise AlpacaSipRuntimeError
                return ResearchInputIdentity.from_verified_replay(_IDENTITY_SCOPE, replay)
            batch = CanonicalDatasetBatch(
                partition=CanonicalDatasetPartition(
                    source_id=_SOURCE,
                    market_domain=DataMarketDomain.US_EQUITIES,
                    event_type="minute_bar",
                    market_date=page_set.request.session_date,
                ),
                raw_manifest=manifest,
                events=events,
            )
            publication = write_canonical_dataset_parquet(batch, output_root=self._output_root)
            replay = replay_canonical_dataset(publication.dataset_directory)
            self._store.append_projection(
                replay.dataset_id,
                projection_key,
                publication.dataset_directory,
                _IDENTITY_SCOPE,
                normalized_at,
            )
            return ResearchInputIdentity.from_verified_replay(_IDENTITY_SCOPE, replay)
        except (KeyError, OSError, TypeError, ValueError):
            raise AlpacaSipRuntimeError from None


def _raw_receipt(
    page_set: AlpacaSipMinutePage,
    stored: StoredAlpacaSipRawPage,
) -> RawReceipt:
    return RawReceipt.from_payload(
        receipt_id=stored.receipt_id,
        source_id=_RAW_SOURCE_ID,
        market_date=page_set.request.session_date,
        received_at=stored.received_at,
        payload_sha256=stored.payload_sha256,
        payload=RawReceiptPayload(stored.raw_response),
    )


def _event(
    instrument_id: str,
    bar: AlpacaSipRuntimeBar,
    stored: StoredAlpacaSipRawPage,
    normalized_at: dt.datetime,
) -> CanonicalEventEnvelope:
    if type(normalized_at) is not dt.datetime:
        raise AlpacaSipRuntimeError
    event_hash = hashlib.sha256(bar.canonical_payload).hexdigest()
    start_at = bar.completed_bar.start_at
    return CanonicalEventEnvelope(
        event_id=f"minute-bar-{bar.sequence:04d}-{event_hash[:16]}",
        source_id=_SOURCE,
        provider_event_id=f"{instrument_id}:{start_at.isoformat()}",
        entity_refs=(
            CanonicalEntityRef(
                entity_type=CanonicalEntityType.INSTRUMENT,
                entity_id=instrument_id,
            ),
        ),
        event_type="minute_bar",
        event_time=start_at,
        provider_time=start_at,
        received_at=stored.received_at,
        normalized_at=normalized_at,
        sequence_or_offset=str(bar.sequence),
        operation=CanonicalEventOperation.ORIGINAL,
        raw_receipt_ref=stored.receipt_id,
        content_hash=event_hash,
        quality_flags=("complete", "sip"),
    )


def _projection_key(
    manifest_id: str,
    instrument_id: str,
    events: tuple[CanonicalEventEnvelope, ...],
) -> str:
    identity = {
        "events": [
            {
                "content_hash": event.content_hash,
                "event_id": event.event_id,
                "provider_event_id": event.provider_event_id,
                "raw_receipt_ref": event.raw_receipt_ref,
                "received_at": event.received_at.isoformat(),
            }
            for event in events
        ],
        "instrument_id": instrument_id,
        "manifest_id": manifest_id,
        "scope": _IDENTITY_SCOPE,
    }
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = (
    "AlpacaSipRuntimeEvidenceProjector",
    "AlpacaSipRuntimeEvidenceStore",
)
