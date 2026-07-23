from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipTradeStreamConfig

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_ADMISSION_SOURCE_ID: Final = "alpaca/sip"


class AlpacaSipEntitlementAdmissionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP entitlement admission evidence is invalid"


class AlpacaSipEntitlementAdmissionStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class AlpacaSipEntitlementAdmissionReason(StrEnum):
    BOUNDED_COMPLETE = "bounded_complete"
    INSUFFICIENT_SUBSCRIPTION = "insufficient_subscription"


class AlpacaSipEntitlementAdmissionArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_id: Literal["alpaca/sip"] = "alpaca/sip"
    symbol: str
    market_date: dt.date
    assessed_at: dt.datetime
    status: AlpacaSipEntitlementAdmissionStatus
    reason_code: AlpacaSipEntitlementAdmissionReason
    evidence_sha256: str
    artifact_id: str

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        aware = self.assessed_at.tzinfo is not None and self.assessed_at.utcoffset() is not None
        aligned = (
            self.status is AlpacaSipEntitlementAdmissionStatus.READY
            and self.reason_code is AlpacaSipEntitlementAdmissionReason.BOUNDED_COMPLETE
        ) or (
            self.status is AlpacaSipEntitlementAdmissionStatus.BLOCKED
            and self.reason_code is AlpacaSipEntitlementAdmissionReason.INSUFFICIENT_SUBSCRIPTION
        )
        if (
            not aware
            or not aligned
            or _SHA256.fullmatch(self.evidence_sha256) is None
            or self.artifact_id
            != _artifact_id(
                symbol=self.symbol,
                market_date=self.market_date,
                assessed_at=self.assessed_at,
                status=self.status,
                reason=self.reason_code,
                evidence_sha256=self.evidence_sha256,
            )
        ):
            raise AlpacaSipEntitlementAdmissionError
        return self


def write_alpaca_sip_entitlement_artifact(
    root: Path,
    artifact: AlpacaSipEntitlementAdmissionArtifact,
) -> tuple[Path, bool]:
    destination: Path | None = None
    created = False
    try:
        checked = AlpacaSipEntitlementAdmissionArtifact.model_validate(artifact.model_dump(mode="python"))
        content = _artifact_bytes(checked)
        artifact_root = require_private_admission_root(root)
        destination = artifact_root / f"alpaca_sip_entitlement_{checked.artifact_id}.json"
        if destination.exists() or destination.is_symlink():
            if _private_file(destination) and destination.read_bytes() == content:
                _ = load_alpaca_sip_entitlement_artifact(destination)
                return destination, False
            raise AlpacaSipEntitlementAdmissionError
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        try:
            _write_all(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory_fd = os.open(artifact_root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination, True
    except AlpacaSipEntitlementAdmissionError:
        raise
    except (OSError, TypeError, ValidationError, ValueError):
        if created and destination is not None:
            destination.unlink(missing_ok=True)
        raise AlpacaSipEntitlementAdmissionError from None


def load_alpaca_sip_entitlement_artifact(
    path: Path,
) -> AlpacaSipEntitlementAdmissionArtifact:
    try:
        candidate = path.expanduser().absolute()
        if not _private_file(candidate):
            raise OSError
        artifact = AlpacaSipEntitlementAdmissionArtifact.model_validate_json(candidate.read_bytes())
        if (
            candidate.name != f"alpaca_sip_entitlement_{artifact.artifact_id}.json"
            or candidate.read_bytes() != _artifact_bytes(artifact)
        ):
            raise ValueError
        return artifact
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise AlpacaSipEntitlementAdmissionError from None


def require_private_admission_root(root: Path) -> Path:
    candidate = root.expanduser().absolute()
    if candidate.is_symlink():
        raise AlpacaSipEntitlementAdmissionError
    if not candidate.exists():
        candidate.mkdir(parents=True, mode=0o700)
        candidate.chmod(0o700)
    metadata = candidate.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise AlpacaSipEntitlementAdmissionError
    return candidate


def build_alpaca_sip_entitlement_artifact(
    *,
    config: AlpacaSipTradeStreamConfig,
    assessed_at: dt.datetime,
    status: AlpacaSipEntitlementAdmissionStatus,
    reason: AlpacaSipEntitlementAdmissionReason,
    evidence_sha256: str,
) -> AlpacaSipEntitlementAdmissionArtifact:
    artifact_id = _artifact_id(
        symbol=config.symbol,
        market_date=config.market_date,
        assessed_at=assessed_at,
        status=status,
        reason=reason,
        evidence_sha256=evidence_sha256,
    )
    return AlpacaSipEntitlementAdmissionArtifact(
        symbol=config.symbol,
        market_date=config.market_date,
        assessed_at=assessed_at,
        status=status,
        reason_code=reason,
        evidence_sha256=evidence_sha256,
        artifact_id=artifact_id,
    )


def _artifact_id(
    *,
    symbol: str,
    market_date: dt.date,
    assessed_at: dt.datetime,
    status: AlpacaSipEntitlementAdmissionStatus,
    reason: AlpacaSipEntitlementAdmissionReason,
    evidence_sha256: str,
) -> str:
    identity = {
        "assessed_at": assessed_at.astimezone(dt.UTC).isoformat(),
        "evidence_sha256": evidence_sha256,
        "market_date": market_date.isoformat(),
        "reason_code": reason.value,
        "schema_version": 1,
        "source_id": _ADMISSION_SOURCE_ID,
        "status": status.value,
        "symbol": symbol,
    }
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _artifact_bytes(artifact: AlpacaSipEntitlementAdmissionArtifact) -> bytes:
    return (
        json.dumps(
            artifact.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def _private_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    metadata = path.lstat()
    return stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid() and stat.S_IMODE(metadata.st_mode) == 0o600


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError
        offset += written


__all__ = (
    "AlpacaSipEntitlementAdmissionArtifact",
    "AlpacaSipEntitlementAdmissionError",
    "AlpacaSipEntitlementAdmissionReason",
    "AlpacaSipEntitlementAdmissionStatus",
    "build_alpaca_sip_entitlement_artifact",
    "load_alpaca_sip_entitlement_artifact",
    "require_private_admission_root",
    "write_alpaca_sip_entitlement_artifact",
)
