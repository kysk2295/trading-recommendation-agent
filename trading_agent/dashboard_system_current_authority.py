from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Final, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

SYSTEM_CURRENT_AUTHORITY_FILE: Final = "system-current-authority.v2.jsonl"
SYSTEM_CURRENT_AUTHORITY_ROOT: Final = "source_evidence"


class RailwayCurrentAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["system_current_authority"]
    kind: Literal["railway_deployment"]
    key_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    project_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    environment: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    service_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    deployment_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_root_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: AwareDatetime
    nonce: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,100}$")
    sequence: int = Field(ge=1)
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class RelayCurrentAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["system_current_authority"]
    kind: Literal["relay_socket"]
    key_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    project_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    environment: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    service_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    transition_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    socket_owner_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_root_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: AwareDatetime
    nonce: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,100}$")
    sequence: int = Field(ge=1)
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


SystemCurrentAuthority = Annotated[
    RailwayCurrentAuthority | RelayCurrentAuthority,
    Field(discriminator="kind"),
]
_ADAPTER = TypeAdapter(SystemCurrentAuthority)


def canonical_authority_payload(authority: SystemCurrentAuthority) -> bytes:
    payload = authority.model_dump(mode="json", exclude={"signature"})
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


@dataclass(frozen=True, slots=True)
class SystemAuthorityVerifier:
    """Pinned Ed25519 public verifier; signing keys remain outside this process."""

    key_id: str
    project_id: str
    environment: str
    railway_service_id: str
    relay_service_id: str
    _public_key: Ed25519PublicKey = field(repr=False)
    _latest: dict[str, tuple[int, str, str]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_public_bytes(
        cls,
        *,
        key_id: str,
        project_id: str,
        environment: str,
        railway_service_id: str,
        relay_service_id: str,
        public_key: bytes,
    ) -> SystemAuthorityVerifier:
        return cls(
            key_id=key_id,
            project_id=project_id,
            environment=environment,
            railway_service_id=railway_service_id,
            relay_service_id=relay_service_id,
            _public_key=Ed25519PublicKey.from_public_bytes(public_key),
        )

    def verify_batch(
        self,
        authorities: tuple[SystemCurrentAuthority, ...],
    ) -> str | None:
        if len(authorities) != 2 or {item.kind for item in authorities} != {
            "railway_deployment",
            "relay_socket",
        }:
            return "system_current_authority_conflict"
        if len({item.nonce for item in authorities}) != len(authorities):
            return "system_current_authority_replay"
        if len({item.sequence for item in authorities}) != 1:
            return "system_current_authority_sequence_mismatch"
        pending: list[tuple[str, int, str, str]] = []
        for authority in authorities:
            expected_service = (
                self.railway_service_id
                if authority.kind == "railway_deployment"
                else self.relay_service_id
            )
            if authority.key_id != self.key_id:
                return "system_current_authority_key_mismatch"
            if (
                authority.project_id != self.project_id
                or authority.environment != self.environment
                or authority.service_id != expected_service
            ):
                return "system_current_authority_identity_mismatch"
            payload = canonical_authority_payload(authority)
            try:
                signature = base64.urlsafe_b64decode(authority.signature + "==")
                self._public_key.verify(signature, payload)
            except (InvalidSignature, ValueError):
                return "system_current_authority_signature_invalid"
            digest = hashlib.sha256(payload).hexdigest()
            previous = self._latest.get(authority.kind)
            if previous is not None:
                previous_sequence, previous_nonce, previous_digest = previous
                if authority.sequence < previous_sequence or (
                    authority.sequence == previous_sequence
                    and (authority.nonce, digest) != (previous_nonce, previous_digest)
                ):
                    return "system_current_authority_replay"
            pending.append((authority.kind, authority.sequence, authority.nonce, digest))
        for kind, sequence, nonce, digest in pending:
            self._latest[kind] = (sequence, nonce, digest)
        return None


SystemAuthorityVerifierFailureReason = Literal[
    "system_current_authority_verifier_missing",
    "system_current_authority_verifier_invalid",
]


@dataclass(frozen=True, slots=True)
class UnavailableSystemAuthorityVerifier:
    reason: SystemAuthorityVerifierFailureReason


SystemAuthorityVerifierInput = (
    SystemAuthorityVerifier | UnavailableSystemAuthorityVerifier | None
)


def read_system_current_authority(
    path: Path,
    now: dt.datetime,
    *,
    verifier: SystemAuthorityVerifierInput,
) -> tuple[SystemCurrentAuthority, ...] | str:
    if verifier is None:
        return "system_current_authority_verifier_missing"
    if isinstance(verifier, UnavailableSystemAuthorityVerifier):
        return verifier.reason
    if not path.exists():
        return "system_current_authority_missing"
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            return "system_current_authority_permissions_invalid"
        payload = path.read_bytes()
        if not payload or len(payload) > 16 * 1024:
            return "system_current_authority_invalid"
        authorities = tuple(_ADAPTER.validate_json(line) for line in payload.splitlines())
    except (OSError, ValidationError, ValueError):
        return "system_current_authority_invalid"
    if any(item.observed_at > now + dt.timedelta(minutes=5) for item in authorities):
        return "system_current_authority_future"
    if any(now - item.observed_at > dt.timedelta(minutes=5) for item in authorities):
        return "system_current_authority_stale"
    verification_error = verifier.verify_batch(authorities)
    return verification_error if verification_error is not None else authorities


__all__ = (
    "SYSTEM_CURRENT_AUTHORITY_FILE",
    "SYSTEM_CURRENT_AUTHORITY_ROOT",
    "RailwayCurrentAuthority",
    "RelayCurrentAuthority",
    "SystemAuthorityVerifier",
    "SystemAuthorityVerifierFailureReason",
    "SystemAuthorityVerifierInput",
    "SystemCurrentAuthority",
    "UnavailableSystemAuthorityVerifier",
    "canonical_authority_payload",
    "read_system_current_authority",
)
