from __future__ import annotations

import hashlib
import json
from pathlib import Path

from trading_agent.alpaca_security_master_models import AlpacaSecurityMasterSnapshot
from trading_agent.canonical_dataset_models import CanonicalDatasetBatch, CanonicalDatasetPartition
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay, replay_canonical_dataset
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.canonical_parquet_writer import write_canonical_dataset_parquet
from trading_agent.data_capability_models import DataSourceId
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.raw_object_manifest_models import RawReceipt, RawReceiptPayload
from trading_agent.raw_receipt_projection import project_raw_receipt_partition
from trading_agent.research_identity_models import MarketId
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_opportunity_scanner_models import (
    StoredUsOpportunityRaw,
    UsOpportunityScannerProjectionError,
)
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_opportunity_security_resolution import (
    ResolvedUsOpportunityCandidate,
    resolve_us_opportunity_candidates,
)
from trading_agent.us_subscription_models import BroadScannerCandidate, BroadScannerSnapshot

_RAW_SOURCE_ID = "internal.us_opportunity"
_SOURCE = DataSourceId(provider="internal", feed="us_opportunity")
_IDENTITY_SCOPE = "us_equities.broad_scanner"


class UsOpportunityScannerProjector:
    __slots__ = ("_output_root", "_store")

    def __init__(self, store: UsOpportunityScannerStore, output_root: Path) -> None:
        self._store = store
        self._output_root = output_root.resolve(strict=False)

    def project(
        self,
        opportunity: OpportunitySnapshot,
        foundation: DataFoundationManifest,
        *,
        security_master: AlpacaSecurityMasterSnapshot | None = None,
    ) -> BroadScannerSnapshot:
        try:
            raw_payload = _opportunity_payload(opportunity)
            stored = self._store.append_raw(
                opportunity.opportunity_id,
                opportunity.observed_at,
                raw_payload,
            )
            resolved = resolve_us_opportunity_candidates(
                opportunity,
                foundation,
                security_master,
            )
            manifest = project_raw_receipt_partition(
                (_raw_receipt(stored, opportunity),),
                source_id=_RAW_SOURCE_ID,
                market_date=opportunity.observed_at.astimezone(NEW_YORK).date(),
                parent_ledger_generation=stored.generation,
            )
            events = tuple(_event(opportunity, foundation, item, stored) for item in resolved)
            projection_key = _projection_key(manifest.manifest_id, foundation.manifest_id, events)
            existing = self._store.projection_directory(projection_key)
            if existing is not None:
                replay = replay_canonical_dataset(existing)
                if replay.raw_manifest_id != manifest.manifest_id or replay.event_count != len(events):
                    raise UsOpportunityScannerProjectionError
                snapshot = _snapshot(opportunity, resolved, replay)
                self._store.append_projection(
                    replay.dataset_id,
                    projection_key,
                    opportunity.opportunity_id,
                    existing,
                    snapshot,
                    opportunity.observed_at,
                )
                return snapshot
            publication = write_canonical_dataset_parquet(
                CanonicalDatasetBatch(
                    partition=CanonicalDatasetPartition(
                        source_id=_SOURCE,
                        market_domain=DataMarketDomain.US_EQUITIES,
                        event_type="scanner_candidate",
                        market_date=manifest.market_date,
                    ),
                    raw_manifest=manifest,
                    events=events,
                ),
                output_root=self._output_root,
            )
            replay = replay_canonical_dataset(publication.dataset_directory)
            snapshot = _snapshot(opportunity, resolved, replay)
            self._store.append_projection(
                replay.dataset_id,
                projection_key,
                opportunity.opportunity_id,
                publication.dataset_directory,
                snapshot,
                opportunity.observed_at,
            )
            return snapshot
        except (KeyError, OSError, TypeError, ValueError):
            raise UsOpportunityScannerProjectionError from None


def _opportunity_payload(opportunity: OpportunitySnapshot) -> bytes:
    if (
        type(opportunity) is not OpportunitySnapshot
        or opportunity.strategy_lane.market_id is not MarketId.US_EQUITIES
    ):
        raise UsOpportunityScannerProjectionError
    return json.dumps(
        opportunity.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _raw_receipt(
    stored: StoredUsOpportunityRaw,
    opportunity: OpportunitySnapshot,
) -> RawReceipt:
    return RawReceipt.from_payload(
        receipt_id=stored.receipt_id,
        source_id=_RAW_SOURCE_ID,
        market_date=opportunity.observed_at.astimezone(NEW_YORK).date(),
        received_at=stored.observed_at,
        payload_sha256=stored.payload_sha256,
        payload=RawReceiptPayload(stored.raw_payload),
    )


def _event(
    opportunity: OpportunitySnapshot,
    foundation: DataFoundationManifest,
    resolved: ResolvedUsOpportunityCandidate,
    stored: StoredUsOpportunityRaw,
) -> CanonicalEventEnvelope:
    content_hash = hashlib.sha256(resolved.canonical_payload).hexdigest()
    return CanonicalEventEnvelope(
        event_id=f"scanner-candidate-{resolved.candidate.rank:04d}-{content_hash[:16]}",
        source_id=_SOURCE,
        provider_event_id=f"{opportunity.opportunity_id}:{resolved.candidate.rank}:{foundation.manifest_id}",
        entity_refs=(
            CanonicalEntityRef(
                entity_type=CanonicalEntityType.INSTRUMENT,
                entity_id=resolved.instrument.value,
            ),
        ),
        event_type="scanner_candidate",
        event_time=opportunity.observed_at,
        received_at=stored.observed_at,
        normalized_at=opportunity.observed_at,
        sequence_or_offset=str(resolved.candidate.rank),
        operation=CanonicalEventOperation.ORIGINAL,
        raw_receipt_ref=stored.receipt_id,
        content_hash=content_hash,
        quality_flags=("complete", "derived"),
    )


def _projection_key(
    manifest_id: str,
    foundation_id: str,
    events: tuple[CanonicalEventEnvelope, ...],
) -> str:
    encoded = json.dumps(
        {
            "events": [(event.event_id, event.content_hash) for event in events],
            "foundation_id": foundation_id,
            "manifest_id": manifest_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _snapshot(
    opportunity: OpportunitySnapshot,
    resolved: tuple[ResolvedUsOpportunityCandidate, ...],
    replay: CanonicalDatasetReplay,
) -> BroadScannerSnapshot:
    identity = ResearchInputIdentity.from_verified_replay(_IDENTITY_SCOPE, replay)
    return BroadScannerSnapshot(
        identity=identity,
        observed_at=opportunity.observed_at,
        candidates=tuple(
            BroadScannerCandidate(
                instrument_id=item.instrument.value,
                symbol=item.candidate.symbol,
                priority_score=item.candidate.score,
                source_rank=item.candidate.rank,
            )
            for item in resolved
        ),
    )


__all__ = (
    "UsOpportunityScannerProjectionError",
    "UsOpportunityScannerProjector",
)
