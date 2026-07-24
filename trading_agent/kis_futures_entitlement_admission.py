from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kis_overseas_futures_models import (
    KisFuturesQuoteRequest,
    KisFuturesQuoteStatus,
)
from trading_agent.kis_overseas_futures_store import KisOverseasFuturesStore
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_CODE = re.compile(rb'"msg_cd"\s*:\s*"([A-Z0-9]{1,12})"')


class KisFuturesAdmissionStatus(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    UNKNOWN = "unknown"


class KisFuturesAdmissionReason(StrEnum):
    BOUNDED_QUOTES_COMPLETE = "bounded_quotes_complete"
    CME_SUB_ENTITLEMENT_MISSING = "cme_sub_entitlement_missing"
    TRANSIENT_OR_MISSING_EVIDENCE = "transient_or_missing_evidence"


class KisFuturesAdmissionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS futures entitlement admission is invalid"


class KisFuturesEntitlementAdmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_request_id: str
    source_run_id: str | None
    evidence_sha256: str | None
    observed_at: dt.datetime
    status: KisFuturesAdmissionStatus
    reason: KisFuturesAdmissionReason
    requested_contract_count: int
    canonical_quote_count: int
    network_access: Literal[0] = 0
    broker_mutation: Literal[0] = 0

    @model_validator(mode="after")
    def validate_admission(self) -> Self:
        ready = (
            self.status is KisFuturesAdmissionStatus.READY
            and self.reason
            is KisFuturesAdmissionReason.BOUNDED_QUOTES_COMPLETE
            and self.source_run_id is not None
            and self.evidence_sha256 is not None
            and self.canonical_quote_count == self.requested_contract_count
        )
        blocked = (
            self.status is KisFuturesAdmissionStatus.BLOCKED
            and self.reason
            is KisFuturesAdmissionReason.CME_SUB_ENTITLEMENT_MISSING
            and self.source_run_id is not None
            and self.evidence_sha256 is not None
            and self.canonical_quote_count == 0
        )
        unknown = (
            self.status is KisFuturesAdmissionStatus.UNKNOWN
            and self.reason
            is KisFuturesAdmissionReason.TRANSIENT_OR_MISSING_EVIDENCE
            and self.canonical_quote_count == 0
        )
        if (
            _SHA256.fullmatch(self.source_request_id) is None
            or (
                self.source_run_id is not None
                and _SHA256.fullmatch(self.source_run_id) is None
            )
            or (
                self.evidence_sha256 is not None
                and _SHA256.fullmatch(self.evidence_sha256) is None
            )
            or not _aware(self.observed_at)
            or not 2 <= self.requested_contract_count <= 8
            or not (ready or blocked or unknown)
        ):
            raise KisFuturesAdmissionError
        return self

    @property
    def admission_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def evaluate_kis_futures_entitlement_admission(
    store: KisOverseasFuturesStore,
    request: KisFuturesQuoteRequest,
    *,
    _clock= lambda: dt.datetime.now(dt.UTC),
) -> KisFuturesEntitlementAdmission:
    run = store.run(request.request_id)
    if run is None:
        return _admission(
            request,
            observed_at=_clock(),
            status=KisFuturesAdmissionStatus.UNKNOWN,
            reason=KisFuturesAdmissionReason.TRANSIENT_OR_MISSING_EVIDENCE,
        )
    if run.status is KisFuturesQuoteStatus.SUCCESS:
        return _admission(
            request,
            source_run_id=run.run_id,
            evidence_sha256=run.run_id,
            observed_at=run.completed_at,
            status=KisFuturesAdmissionStatus.READY,
            reason=KisFuturesAdmissionReason.BOUNDED_QUOTES_COMPLETE,
            canonical_quote_count=len(run.quotes),
        )
    for symbol in request.symbols:
        receipt = store.receipt(request.request_id, symbol)
        if receipt is not None and _provider_code(receipt.raw_payload) == "EGW00550":
            return _admission(
                request,
                source_run_id=run.run_id,
                evidence_sha256=receipt.receipt_id,
                observed_at=receipt.received_at,
                status=KisFuturesAdmissionStatus.BLOCKED,
                reason=KisFuturesAdmissionReason.CME_SUB_ENTITLEMENT_MISSING,
            )
    return _admission(
        request,
        source_run_id=run.run_id,
        evidence_sha256=run.run_id,
        observed_at=run.completed_at,
        status=KisFuturesAdmissionStatus.UNKNOWN,
        reason=KisFuturesAdmissionReason.TRANSIENT_OR_MISSING_EVIDENCE,
    )


def publish_kis_futures_entitlement_admission(
    output_root: Path,
    admission: KisFuturesEntitlementAdmission,
) -> tuple[Path, bool]:
    try:
        checked = KisFuturesEntitlementAdmission.model_validate(
            admission.model_dump()
        )
        path = output_root / (
            f"kis_futures_entitlement_admission_{checked.admission_id}.json"
        )
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except KisFuturesAdmissionError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise KisFuturesAdmissionError from None


def _admission(
    request: KisFuturesQuoteRequest,
    *,
    observed_at: dt.datetime,
    status: KisFuturesAdmissionStatus,
    reason: KisFuturesAdmissionReason,
    source_run_id: str | None = None,
    evidence_sha256: str | None = None,
    canonical_quote_count: int = 0,
) -> KisFuturesEntitlementAdmission:
    return KisFuturesEntitlementAdmission(
        source_request_id=request.request_id,
        source_run_id=source_run_id,
        evidence_sha256=evidence_sha256,
        observed_at=observed_at,
        status=status,
        reason=reason,
        requested_contract_count=len(request.symbols),
        canonical_quote_count=canonical_quote_count,
    )


def _provider_code(payload: bytes) -> str | None:
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError):
        matched = _PROVIDER_CODE.search(payload)
        return None if matched is None else matched.group(1).decode("ascii")
    if not isinstance(decoded, dict):
        return None
    value = decoded.get("msg_cd")
    return value if isinstance(value, str) else None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "KisFuturesAdmissionError",
    "KisFuturesAdmissionReason",
    "KisFuturesAdmissionStatus",
    "KisFuturesEntitlementAdmission",
    "evaluate_kis_futures_entitlement_admission",
    "publish_kis_futures_entitlement_admission",
)
