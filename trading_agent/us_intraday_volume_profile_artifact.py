from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import override

from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_intraday_volume_profile_models import (
    IntradayVolumeProfileEvidence,
    create_intraday_volume_profile_evidence,
    validate_intraday_volume_profile,
)


class IntradayVolumeProfileArtifactError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday volume profile artifact is invalid"


class IntradayVolumeProfileArtifactStore:
    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = _private_directory(root)

    def append(self, profile: IntradayVolumeProfileEvidence) -> Path:
        try:
            validate_intraday_volume_profile(profile)
            content = _profile_bytes(profile)
            path = self._root / f"profile_{profile.evidence_sha256}.json"
            if path.exists():
                if path.is_symlink() or path.read_bytes() != content:
                    raise IntradayVolumeProfileArtifactError
                _require_private_file(path)
                return path
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.write(descriptor, content)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _require_private_file(path)
            return path
        except (OSError, TypeError, ValueError):
            raise IntradayVolumeProfileArtifactError from None

    def load(self, path: Path) -> IntradayVolumeProfileEvidence:
        try:
            if path.parent != self._root or path.is_symlink():
                raise IntradayVolumeProfileArtifactError
            _require_private_file(path)
            payload = json.loads(path.read_bytes())
            if type(payload) is not dict or set(payload) != _PROFILE_KEYS:
                raise IntradayVolumeProfileArtifactError
            identities = tuple(_identity(item) for item in payload["source_identities"])
            profile = create_intraday_volume_profile_evidence(
                identities,
                payload["instrument_id"],
                dt.date.fromisoformat(payload["target_session_date"]),
                payload["through_minute"],
                tuple(dt.date.fromisoformat(item) for item in payload["source_session_dates"]),
                tuple(payload["session_cumulative_volumes"]),
            )
            if json.loads(_profile_bytes(profile)) != payload or path.name != f"profile_{profile.evidence_sha256}.json":
                raise IntradayVolumeProfileArtifactError
            return profile
        except (AttributeError, OSError, TypeError, ValueError):
            raise IntradayVolumeProfileArtifactError from None


_PROFILE_KEYS = {
    "evidence_sha256",
    "expected_cumulative_volume",
    "instrument_id",
    "schema_version",
    "semantic_version",
    "session_cumulative_volumes",
    "source_identities",
    "source_session_dates",
    "target_session_date",
    "through_minute",
}


def _profile_bytes(profile: IntradayVolumeProfileEvidence) -> bytes:
    return (
        json.dumps(
            _profile_payload(profile),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def _profile_payload(
    profile: IntradayVolumeProfileEvidence,
) -> dict[
    str,
    str | int | tuple[int, ...] | tuple[str, ...] | tuple[dict[str, str], ...],
]:
    return {
        "evidence_sha256": profile.evidence_sha256,
        "expected_cumulative_volume": str(profile.expected_cumulative_volume),
        "instrument_id": profile.instrument_id,
        "schema_version": 1,
        "semantic_version": profile.semantic_version,
        "session_cumulative_volumes": profile.session_cumulative_volumes,
        "source_identities": tuple(_identity_payload(item) for item in profile.source_identities),
        "source_session_dates": tuple(item.isoformat() for item in profile.source_session_dates),
        "target_session_date": profile.target_session_date.isoformat(),
        "through_minute": profile.through_minute,
    }


def _identity_payload(identity: ResearchInputIdentity) -> dict[str, str]:
    return {
        "canonical_event_content_sha256": identity.canonical_event_content_sha256,
        "dataset_id": identity.dataset_id,
        "identity_sha256": identity.identity_sha256,
        "raw_manifest_content_sha256": identity.raw_manifest_content_sha256,
        "raw_manifest_id": identity.raw_manifest_id,
        "scope": identity.scope,
    }


def _identity(value: dict[str, str]) -> ResearchInputIdentity:
    if type(value) is not dict or set(value) != _IDENTITY_KEYS:
        raise IntradayVolumeProfileArtifactError
    identity = ResearchInputIdentity(**value)
    payload = _identity_payload(identity)
    claimed = payload.pop("identity_sha256")
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if claimed != hashlib.sha256(encoded.encode()).hexdigest():
        raise IntradayVolumeProfileArtifactError
    return identity


_IDENTITY_KEYS = {
    "canonical_event_content_sha256",
    "dataset_id",
    "identity_sha256",
    "raw_manifest_content_sha256",
    "raw_manifest_id",
    "scope",
}


def _private_directory(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    try:
        if candidate.is_symlink():
            raise IntradayVolumeProfileArtifactError
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = candidate.lstat()
    except OSError:
        raise IntradayVolumeProfileArtifactError from None
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise IntradayVolumeProfileArtifactError
    return candidate


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise IntradayVolumeProfileArtifactError


__all__ = (
    "IntradayVolumeProfileArtifactError",
    "IntradayVolumeProfileArtifactStore",
)
