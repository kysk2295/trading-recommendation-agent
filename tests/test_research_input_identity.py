from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields

import pytest

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import (
    ResearchInputIdentity,
    ResearchInputIdentityError,
)

_SCOPE = "us_equities.day_trading.orb"
_DATASET_ID = "ds_abc123"
_CANONICAL_EVENT_CONTENT_SHA256 = "a" * 64
_RAW_MANIFEST_ID = "raw_manifest_xyz"
_RAW_MANIFEST_CONTENT_SHA256 = "b" * 64


def _replay() -> CanonicalDatasetReplay:
    return CanonicalDatasetReplay(
        dataset_id=_DATASET_ID,
        event_count=1,
        canonical_event_content_sha256=_CANONICAL_EVENT_CONTENT_SHA256,
        parquet_sha256="c" * 64,
        raw_manifest_id=_RAW_MANIFEST_ID,
        raw_manifest_content_sha256=_RAW_MANIFEST_CONTENT_SHA256,
    )


def _expected_identity_sha256(scope: str, replay: CanonicalDatasetReplay) -> str:
    payload = {
        "canonical_event_content_sha256": replay.canonical_event_content_sha256,
        "dataset_id": replay.dataset_id,
        "raw_manifest_content_sha256": replay.raw_manifest_content_sha256,
        "raw_manifest_id": replay.raw_manifest_id,
        "scope": scope,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_from_verified_replay_populates_lineage_fields() -> None:
    replay = _replay()

    identity = ResearchInputIdentity.from_verified_replay(_SCOPE, replay)

    assert tuple(field.name for field in fields(ResearchInputIdentity)) == (
        "scope",
        "dataset_id",
        "canonical_event_content_sha256",
        "raw_manifest_id",
        "raw_manifest_content_sha256",
        "identity_sha256",
    )
    assert identity.scope == _SCOPE
    assert identity.dataset_id == _DATASET_ID
    assert identity.canonical_event_content_sha256 == _CANONICAL_EVENT_CONTENT_SHA256
    assert identity.raw_manifest_id == _RAW_MANIFEST_ID
    assert identity.raw_manifest_content_sha256 == _RAW_MANIFEST_CONTENT_SHA256
    assert identity.identity_sha256 == _expected_identity_sha256(_SCOPE, replay)
    assert identity.identity_sha256 != replay.parquet_sha256


def test_from_verified_replay_is_deterministic() -> None:
    replay = _replay()

    first = ResearchInputIdentity.from_verified_replay(_SCOPE, replay)
    second = ResearchInputIdentity.from_verified_replay(_SCOPE, replay)

    assert first == second
    assert first.identity_sha256 == second.identity_sha256
    assert first.identity_sha256 == _expected_identity_sha256(_SCOPE, replay)


def test_scope_isolation_changes_identity_digest() -> None:
    replay = _replay()
    other_scope = "us_equities.day_trading.gap_and_go"

    first = ResearchInputIdentity.from_verified_replay(_SCOPE, replay)
    second = ResearchInputIdentity.from_verified_replay(other_scope, replay)

    assert first.dataset_id == second.dataset_id
    assert first.canonical_event_content_sha256 == second.canonical_event_content_sha256
    assert first.raw_manifest_id == second.raw_manifest_id
    assert first.raw_manifest_content_sha256 == second.raw_manifest_content_sha256
    assert first.scope != second.scope
    assert first.identity_sha256 != second.identity_sha256
    assert first.identity_sha256 == _expected_identity_sha256(_SCOPE, replay)
    assert second.identity_sha256 == _expected_identity_sha256(other_scope, replay)


@pytest.mark.parametrize(
    "scope",
    (
        "",
        " bad",
        "bad scope",
        "-leading-dash",
        "_leading_underscore",
        ".leading-dot",
        "has/slash",
        "has@symbol",
        "x" * 129,
        123,
        None,
    ),
)
def test_malformed_scope_raises_sanitized_error(scope: object) -> None:
    with pytest.raises(ResearchInputIdentityError) as captured:
        ResearchInputIdentity.from_verified_replay(scope, _replay())  # type: ignore[arg-type]

    assert str(captured.value) == "research input identity could not be derived"
    assert repr(captured.value) == "ResearchInputIdentityError()"


def test_replay_like_object_is_rejected() -> None:
    @dataclass(frozen=True, slots=True)
    class ReplayLike:
        dataset_id: str
        event_count: int
        canonical_event_content_sha256: str
        parquet_sha256: str
        raw_manifest_id: str
        raw_manifest_content_sha256: str

    lookalike = ReplayLike(
        dataset_id=_DATASET_ID,
        event_count=1,
        canonical_event_content_sha256=_CANONICAL_EVENT_CONTENT_SHA256,
        parquet_sha256="c" * 64,
        raw_manifest_id=_RAW_MANIFEST_ID,
        raw_manifest_content_sha256=_RAW_MANIFEST_CONTENT_SHA256,
    )

    with pytest.raises(ResearchInputIdentityError) as captured:
        ResearchInputIdentity.from_verified_replay(_SCOPE, lookalike)  # type: ignore[arg-type]

    assert str(captured.value) == "research input identity could not be derived"
    assert repr(captured.value) == "ResearchInputIdentityError()"


def test_subclass_of_canonical_dataset_replay_is_rejected() -> None:
    @dataclass(frozen=True, slots=True)
    class SubclassReplay(CanonicalDatasetReplay):
        extra: str = "not-allowed"

    subclass_replay = SubclassReplay(
        dataset_id=_DATASET_ID,
        event_count=1,
        canonical_event_content_sha256=_CANONICAL_EVENT_CONTENT_SHA256,
        parquet_sha256="c" * 64,
        raw_manifest_id=_RAW_MANIFEST_ID,
        raw_manifest_content_sha256=_RAW_MANIFEST_CONTENT_SHA256,
        extra="hostile",
    )

    with pytest.raises(ResearchInputIdentityError) as captured:
        ResearchInputIdentity.from_verified_replay(_SCOPE, subclass_replay)

    assert str(captured.value) == "research input identity could not be derived"
    assert repr(captured.value) == "ResearchInputIdentityError()"
    assert isinstance(captured.value, ValueError)
