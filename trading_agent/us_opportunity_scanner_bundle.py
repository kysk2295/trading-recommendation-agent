from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from trading_agent.canonical_duckdb_replay import replay_canonical_dataset
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.strategy_data_gate import StrategyDataStatus
from trading_agent.us_opportunity_scanner_models import (
    UsOpportunityScannerBundle,
    UsOpportunityScannerProjectionError,
    decode_broad_scanner_snapshot,
)
from trading_agent.us_opportunity_scanner_schema import US_OPPORTUNITY_SCANNER_SCHEMA_VERSION


def load_latest_us_opportunity_scanner_bundle(
    path: Path,
) -> UsOpportunityScannerBundle | None:
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            _require_schema(connection)
            row: tuple[str, bytes, str, bytes, str, str, str, str, bytes] | None = connection.execute(
                "SELECT p.dataset_directory,p.snapshot_payload,p.foundation_manifest_id,"
                "p.foundation_payload,p.opportunity_id,p.recorded_at,r.observed_at,"
                "r.payload_sha256,r.raw_payload FROM us_opportunity_scanner_projections p "
                "JOIN us_opportunity_scanner_raw r ON r.opportunity_id = p.opportunity_id "
                "ORDER BY p.generation DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        replay = replay_canonical_dataset(Path(row[0]))
        snapshot = decode_broad_scanner_snapshot(row[1], replay)
        recorded_at = dt.datetime.fromisoformat(row[5])
        foundation = _decode_foundation(row[3], row[2], recorded_at)
        opportunity = _decode_opportunity(row[8], row[7])
        if (
            opportunity.opportunity_id != row[4]
            or opportunity.observed_at != dt.datetime.fromisoformat(row[6])
            or opportunity.observed_at != recorded_at
            or opportunity.observed_at != snapshot.observed_at
            or tuple(item.symbol for item in opportunity.candidates)
            != tuple(item.symbol for item in snapshot.candidates)
        ):
            raise UsOpportunityScannerProjectionError
        return UsOpportunityScannerBundle(opportunity, snapshot, foundation)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise UsOpportunityScannerProjectionError from None


def _decode_foundation(
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
        raise UsOpportunityScannerProjectionError
    return foundation


def _decode_opportunity(payload: bytes, payload_sha256: str) -> OpportunitySnapshot:
    opportunity = OpportunitySnapshot.model_validate_json(payload)
    canonical = json.dumps(
        opportunity.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if canonical != payload or hashlib.sha256(payload).hexdigest() != payload_sha256:
        raise UsOpportunityScannerProjectionError
    return opportunity


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (US_OPPORTUNITY_SCANNER_SCHEMA_VERSION,):
        raise UsOpportunityScannerProjectionError


__all__ = ("load_latest_us_opportunity_scanner_bundle",)
