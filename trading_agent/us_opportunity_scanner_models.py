from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Literal, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_subscription_models import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
)
from trading_agent.us_subscription_validation import validate_subscription_policy_inputs

_ERROR_MESSAGE: Final = "US opportunity scanner projection is invalid"


class UsOpportunityScannerProjectionError(ValueError):
    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "UsOpportunityScannerProjectionError()"


@dataclass(frozen=True, slots=True)
class StoredUsOpportunityRaw:
    generation: int
    receipt_id: str
    opportunity_id: str
    observed_at: dt.datetime
    payload_sha256: str
    raw_payload: bytes = field(repr=False)


class _IdentityPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: str
    dataset_id: str
    canonical_event_content_sha256: str
    raw_manifest_id: str
    raw_manifest_content_sha256: str
    identity_sha256: str


class _CandidatePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    symbol: str
    priority_score: Decimal
    source_rank: int


class _SnapshotPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    identity: _IdentityPayload
    observed_at: dt.datetime
    candidates: tuple[_CandidatePayload, ...]

    @model_validator(mode="after")
    def validate_observed_at(self) -> _SnapshotPayload:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise UsOpportunityScannerProjectionError
        return self


def encode_broad_scanner_snapshot(snapshot: BroadScannerSnapshot) -> bytes:
    try:
        _validate_snapshot(snapshot)
        payload = _snapshot_payload(snapshot)
        return json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError):
        raise UsOpportunityScannerProjectionError from None


def decode_broad_scanner_snapshot(
    payload: bytes,
    replay: CanonicalDatasetReplay,
) -> BroadScannerSnapshot:
    try:
        parsed = _SnapshotPayload.model_validate_json(payload)
        identity = ResearchInputIdentity.from_verified_replay(parsed.identity.scope, replay)
        if parsed.identity != _identity_payload(identity):
            raise ValueError
        snapshot = BroadScannerSnapshot(
            identity,
            parsed.observed_at,
            tuple(
                BroadScannerCandidate(
                    item.instrument_id,
                    item.symbol,
                    item.priority_score,
                    item.source_rank,
                )
                for item in parsed.candidates
            ),
        )
        _validate_snapshot(snapshot)
        return snapshot
    except (TypeError, ValidationError, ValueError):
        raise UsOpportunityScannerProjectionError from None


def _snapshot_payload(snapshot: BroadScannerSnapshot) -> _SnapshotPayload:
    return _SnapshotPayload(
        identity=_identity_payload(snapshot.identity),
        observed_at=snapshot.observed_at,
        candidates=tuple(
            _CandidatePayload(
                instrument_id=item.instrument_id,
                symbol=item.symbol,
                priority_score=item.priority_score,
                source_rank=item.source_rank,
            )
            for item in snapshot.candidates
        ),
    )


def _identity_payload(identity: ResearchInputIdentity) -> _IdentityPayload:
    return _IdentityPayload(
        scope=identity.scope,
        dataset_id=identity.dataset_id,
        canonical_event_content_sha256=identity.canonical_event_content_sha256,
        raw_manifest_id=identity.raw_manifest_id,
        raw_manifest_content_sha256=identity.raw_manifest_content_sha256,
        identity_sha256=identity.identity_sha256,
    )


def _validate_snapshot(snapshot: BroadScannerSnapshot) -> None:
    if type(snapshot) is not BroadScannerSnapshot:
        raise ValueError
    validate_subscription_policy_inputs(
        snapshot,
        snapshot.observed_at,
        (),
        (),
        SubscriptionPolicyConfig(
            capacity=max(1, len(snapshot.candidates)),
            max_candidate_age=dt.timedelta(minutes=1),
            minimum_residency=dt.timedelta(minutes=1),
            eviction_cooldown=dt.timedelta(minutes=1),
        ),
    )


__all__ = (
    "StoredUsOpportunityRaw",
    "UsOpportunityScannerProjectionError",
    "decode_broad_scanner_snapshot",
    "encode_broad_scanner_snapshot",
)
