from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.canonical_dataset_event_reader import replay_canonical_dataset_events
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplayError
from trading_agent.canonical_event_models import CanonicalEventEnvelope
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.strategy_data_gate import StrategyDataStatus
from trading_agent.us_opportunity_scanner_models import decode_broad_scanner_snapshot
from trading_agent.us_opportunity_scanner_schema import US_OPPORTUNITY_SCANNER_SCHEMA_VERSION
from trading_agent.us_subscription_models import BroadScannerSnapshot


class UsScannerResearchSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US scanner research source is blocked"


@dataclass(frozen=True, slots=True)
class UsScannerResearchSource:
    opportunity: OpportunitySnapshot
    snapshot: BroadScannerSnapshot
    foundation: DataFoundationManifest
    security_master_id: str | None
    raw_receipt_ref: str
    events: tuple[CanonicalEventEnvelope, ...]


@dataclass(frozen=True, slots=True)
class _StoredScannerSource:
    dataset_directory: str
    dataset_id: str
    snapshot_payload: bytes
    foundation_manifest_id: str
    foundation_payload: bytes
    security_master_id: str | None
    opportunity_id: str
    recorded_at: str
    observed_at: str
    receipt_id: str
    payload_sha256: str
    raw_payload: bytes


def load_latest_us_scanner_research_source(path: Path) -> UsScannerResearchSource:
    try:
        store = _private_store(path)
        with sqlite3.connect(f"file:{store}?mode=ro", uri=True) as connection:
            _require_schema(connection)
            row: (
                tuple[
                    str,
                    str,
                    bytes,
                    str,
                    bytes,
                    str | None,
                    str,
                    str,
                    str,
                    str,
                    str,
                    bytes,
                ]
                | None
            ) = connection.execute(
                "SELECT p.dataset_directory,p.dataset_id,p.snapshot_payload,"
                "p.foundation_manifest_id,p.foundation_payload,p.security_master_id,"
                "p.opportunity_id,p.recorded_at,r.observed_at,r.receipt_id,"
                "r.payload_sha256,r.raw_payload FROM us_opportunity_scanner_projections p "
                "JOIN us_opportunity_scanner_raw r ON r.opportunity_id = p.opportunity_id "
                "ORDER BY p.generation DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise UsScannerResearchSourceError
        stored = _StoredScannerSource(*row)
        replay, events = replay_canonical_dataset_events(Path(stored.dataset_directory))
        snapshot = decode_broad_scanner_snapshot(stored.snapshot_payload, replay)
        opportunity = _opportunity(stored.raw_payload, stored.payload_sha256)
        foundation = _foundation(
            stored.foundation_payload,
            stored.foundation_manifest_id,
            opportunity.observed_at,
        )
        source = UsScannerResearchSource(
            opportunity,
            snapshot,
            foundation,
            stored.security_master_id,
            stored.receipt_id,
            events,
        )
        _validate_bundle(stored, source, replay.dataset_id)
        return source
    except (
        CanonicalDatasetReplayError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ):
        raise UsScannerResearchSourceError from None


def _private_store(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    if candidate != candidate.resolve(strict=True):
        raise UsScannerResearchSourceError
    metadata = candidate.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise UsScannerResearchSourceError
    return candidate


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (US_OPPORTUNITY_SCANNER_SCHEMA_VERSION,):
        raise UsScannerResearchSourceError


def _opportunity(payload: bytes, expected_sha256: str) -> OpportunitySnapshot:
    opportunity = OpportunitySnapshot.model_validate_json(payload)
    canonical = json.dumps(
        opportunity.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if canonical != payload or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise UsScannerResearchSourceError
    return opportunity


def _foundation(
    payload: bytes,
    manifest_id: str,
    observed_at: dt.datetime,
) -> DataFoundationManifest:
    foundation = DataFoundationManifest.model_validate_json(payload)
    if (
        foundation.manifest_id != manifest_id
        or foundation.evaluated_at > observed_at
        or foundation.evaluate_data_readiness().status is not StrategyDataStatus.READY
    ):
        raise UsScannerResearchSourceError
    return foundation


def _validate_bundle(
    stored: _StoredScannerSource,
    source: UsScannerResearchSource,
    dataset_id: str,
) -> None:
    opportunity = source.opportunity
    snapshot = source.snapshot
    observed_at = dt.datetime.fromisoformat(stored.observed_at)
    recorded_at = dt.datetime.fromisoformat(stored.recorded_at)
    receipt_identity = {
        "observed_at": observed_at.isoformat(),
        "opportunity_id": stored.opportunity_id,
        "payload_sha256": stored.payload_sha256,
    }
    receipt_id = hashlib.sha256(
        json.dumps(
            receipt_identity,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    opportunity_shape = tuple((item.symbol, item.rank, item.score) for item in opportunity.candidates)
    snapshot_shape = tuple((item.symbol, item.source_rank, item.priority_score) for item in snapshot.candidates)
    if (
        dataset_id != stored.dataset_id
        or opportunity.opportunity_id != stored.opportunity_id
        or opportunity.observed_at != observed_at
        or opportunity.observed_at != recorded_at
        or opportunity.observed_at != snapshot.observed_at
        or opportunity_shape != snapshot_shape
        or receipt_id != stored.receipt_id
        or len(source.events) != len(snapshot.candidates)
    ):
        raise UsScannerResearchSourceError


__all__ = (
    "UsScannerResearchSource",
    "UsScannerResearchSourceError",
    "load_latest_us_scanner_research_source",
)
