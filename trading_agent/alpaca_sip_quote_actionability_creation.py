from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import TypedDict, override

from trading_agent.alpaca_sip_quote_actionability_artifact import AlpacaSipQuoteActionabilityArtifact
from trading_agent.alpaca_sip_quote_actionability_manifest import AlpacaSipQuoteActionabilityManifest

_HEX = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_ARTIFACT_ID = re.compile(r"^us-quote-assessment:[0-9a-f]{64}$", flags=re.ASCII)
_MANIFEST_ID = re.compile(r"^alpaca-sip-actionability-manifest:[0-9a-f]{64}$", flags=re.ASCII)


class AlpacaSipQuoteActionabilityCreationError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP quote actionability creation is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipQuoteActionabilityCreation:
    creation_id: str
    artifact_id: str
    manifest_id: str
    evaluated_at: dt.datetime


@dataclass(frozen=True, slots=True)
class AlpacaSipQuoteActionabilityAppendResult:
    appended: bool
    creation: AlpacaSipQuoteActionabilityCreation


class _CreationPayload(TypedDict):
    artifact_id: str
    creation_id: str
    evaluated_at: str
    manifest_id: str


def build_actionability_creation(
    manifest: AlpacaSipQuoteActionabilityManifest,
    artifact: AlpacaSipQuoteActionabilityArtifact,
) -> AlpacaSipQuoteActionabilityCreation:
    if (
        type(manifest) is not AlpacaSipQuoteActionabilityManifest
        or type(artifact) is not AlpacaSipQuoteActionabilityArtifact
        or artifact.base_publication != manifest.base_publication
        or artifact.assessment.scan_started_at != manifest.scan_started_at
        or artifact.bundle.trade_confirmation.dynamic_plan_id != manifest.plan.plan_id
        or artifact.bundle.trade_confirmation.research_input_identity_sha256
        != manifest.snapshot.identity.identity_sha256
        or artifact.bundle.trade_confirmation.instrument_id != manifest.snapshot.instrument_id
    ):
        raise AlpacaSipQuoteActionabilityCreationError
    provisional = AlpacaSipQuoteActionabilityCreation(
        "0" * 64,
        artifact.artifact_id,
        manifest.manifest_id,
        manifest.snapshot.observed_at,
    )
    creation = replace(provisional, creation_id=_identity(provisional))
    validate_actionability_creation(creation)
    return creation


def validate_actionability_creation(creation: AlpacaSipQuoteActionabilityCreation) -> None:
    if (
        type(creation) is not AlpacaSipQuoteActionabilityCreation
        or _HEX.fullmatch(creation.creation_id) is None
        or _ARTIFACT_ID.fullmatch(creation.artifact_id) is None
        or _MANIFEST_ID.fullmatch(creation.manifest_id) is None
        or not _aware(creation.evaluated_at)
        or creation.creation_id != _identity(creation)
    ):
        raise AlpacaSipQuoteActionabilityCreationError


def actionability_creation_bytes(creation: AlpacaSipQuoteActionabilityCreation) -> bytes:
    validate_actionability_creation(creation)
    return _canonical(_payload(creation))


def actionability_creation_from_bytes(payload: bytes) -> AlpacaSipQuoteActionabilityCreation:
    try:
        value = json.loads(payload)
        if type(value) is not dict or set(value) != {"artifact_id", "creation_id", "evaluated_at", "manifest_id"}:
            raise AlpacaSipQuoteActionabilityCreationError
        creation = AlpacaSipQuoteActionabilityCreation(
            value["creation_id"],
            value["artifact_id"],
            value["manifest_id"],
            dt.datetime.fromisoformat(value["evaluated_at"]),
        )
        validate_actionability_creation(creation)
        if actionability_creation_bytes(creation) != payload:
            raise AlpacaSipQuoteActionabilityCreationError
        return creation
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipQuoteActionabilityCreationError from None


def _identity(creation: AlpacaSipQuoteActionabilityCreation) -> str:
    payload = _payload(creation)
    payload["creation_id"] = ""
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _payload(creation: AlpacaSipQuoteActionabilityCreation) -> _CreationPayload:
    return {
        "artifact_id": creation.artifact_id,
        "creation_id": creation.creation_id,
        "evaluated_at": creation.evaluated_at.isoformat(),
        "manifest_id": creation.manifest_id,
    }


def _canonical(payload: _CreationPayload) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipQuoteActionabilityAppendResult",
    "AlpacaSipQuoteActionabilityCreation",
    "AlpacaSipQuoteActionabilityCreationError",
    "actionability_creation_bytes",
    "actionability_creation_from_bytes",
    "build_actionability_creation",
    "validate_actionability_creation",
)
