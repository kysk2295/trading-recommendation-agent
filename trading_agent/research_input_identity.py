"""Deterministic research-input identity bound to a verified canonical replay."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final, override

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay

_ERROR_MESSAGE: Final = "research input identity could not be derived"
_SCOPE_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_IDENTITY_KEYS: Final = (
    "canonical_event_content_sha256",
    "dataset_id",
    "raw_manifest_content_sha256",
    "raw_manifest_id",
    "scope",
)


class ResearchInputIdentityError(ValueError):
    def __init__(self, *_args: object) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "ResearchInputIdentityError()"


@dataclass(frozen=True, slots=True)
class ResearchInputIdentity:
    scope: str
    dataset_id: str
    canonical_event_content_sha256: str
    raw_manifest_id: str
    raw_manifest_content_sha256: str
    identity_sha256: str

    @classmethod
    def from_verified_replay(
        cls,
        scope: str,
        replay: CanonicalDatasetReplay,
    ) -> ResearchInputIdentity:
        if type(scope) is not str or _SCOPE_PATTERN.fullmatch(scope) is None:
            raise ResearchInputIdentityError
        if type(replay) is not CanonicalDatasetReplay:
            raise ResearchInputIdentityError

        dataset_id = replay.dataset_id
        canonical_event_content_sha256 = replay.canonical_event_content_sha256
        raw_manifest_id = replay.raw_manifest_id
        raw_manifest_content_sha256 = replay.raw_manifest_content_sha256
        payload = {
            "canonical_event_content_sha256": canonical_event_content_sha256,
            "dataset_id": dataset_id,
            "raw_manifest_content_sha256": raw_manifest_content_sha256,
            "raw_manifest_id": raw_manifest_id,
            "scope": scope,
        }
        if tuple(sorted(payload)) != _IDENTITY_KEYS:
            raise ResearchInputIdentityError
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            identity_sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        except (TypeError, ValueError) as error:
            raise ResearchInputIdentityError from error

        return cls(
            scope=scope,
            dataset_id=dataset_id,
            canonical_event_content_sha256=canonical_event_content_sha256,
            raw_manifest_id=raw_manifest_id,
            raw_manifest_content_sha256=raw_manifest_content_sha256,
            identity_sha256=identity_sha256,
        )


__all__ = (
    "ResearchInputIdentity",
    "ResearchInputIdentityError",
)
