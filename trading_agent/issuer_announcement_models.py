from __future__ import annotations

import base64
import datetime as dt
import hashlib
import ipaddress
import re
from enum import StrEnum
from typing import Final, Literal, Self, override
from urllib.parse import SplitResult, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.data_capability_models import (
    DataCorrectionPolicy,
    DataRetentionPolicy,
    DataSourceId,
    DataUse,
    RedistributionPolicy,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES: Final = 2 * 1024 * 1024
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ISSUER_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class IssuerAnnouncementContractError(ValueError):
    @override
    def __str__(self) -> str:
        return "issuer announcement contract is invalid"


class IssuerAnnouncementFeedFormat(StrEnum):
    RSS2 = "rss2"
    ATOM = "atom"


class IssuerAnnouncementOnboarding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    onboarding_id: str
    source_id: DataSourceId
    issuer_id: str
    symbols: tuple[str, ...] = Field(min_length=1, max_length=8)
    endpoint: str
    allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=8)
    terms_url: str
    feed_format: IssuerAnnouncementFeedFormat
    automated_access_permitted: bool
    license_reviewed_at: dt.datetime
    effective_from: dt.datetime
    effective_to: dt.datetime | None
    permitted_uses: tuple[DataUse, ...]
    redistribution_policy: RedistributionPolicy
    raw_retention_days: int = Field(ge=0, le=365)
    derived_retention_days: int = Field(ge=1, le=3_650)
    deletion_required: bool
    correction_policy: DataCorrectionPolicy
    max_requests_per_minute: int = Field(ge=1, le=60)
    freshness_slo_seconds: int = Field(ge=60, le=86_400)
    max_items: int = Field(ge=1, le=100)

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def normalize_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(item.lower() for item in value)))

    @field_validator("permitted_uses", mode="before")
    @classmethod
    def normalize_uses(
        cls,
        value: tuple[DataUse | str, ...],
    ) -> tuple[DataUse, ...]:
        return tuple(
            sorted(
                {DataUse(item) for item in value},
                key=lambda item: item.value,
            )
        )

    @field_validator("license_reviewed_at", "effective_from", "effective_to")
    @classmethod
    def normalize_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return value.astimezone(dt.UTC) if value is not None and _aware(value) else value

    @model_validator(mode="after")
    def validate_onboarding(self) -> Self:
        endpoint = _safe_https_url(self.endpoint)
        terms = _safe_https_url(self.terms_url)
        if (
            _OPAQUE_ID.fullmatch(self.onboarding_id) is None
            or self.source_id.provider != "issuer_direct"
            or _ISSUER_ID.fullmatch(self.issuer_id) is None
            or any(_SYMBOL.fullmatch(symbol) is None for symbol in self.symbols)
            or endpoint is None
            or terms is None
            or endpoint.hostname not in self.allowed_hosts
            or any(not _safe_host(host) for host in self.allowed_hosts)
            or not self.automated_access_permitted
            or not _aware(self.license_reviewed_at)
            or not _aware(self.effective_from)
            or self.license_reviewed_at > self.effective_from
            or (
                self.effective_to is not None
                and (not _aware(self.effective_to) or self.effective_to <= self.effective_from)
            )
            or self.permitted_uses
            != (DataUse.HISTORICAL_RESEARCH, DataUse.SHADOW_FORWARD)
            or self.redistribution_policy is not RedistributionPolicy.NONE
            or self.derived_retention_days < self.raw_retention_days
            or not self.deletion_required
            or self.correction_policy is not DataCorrectionPolicy.APPEND_CORRECTION
        ):
            raise IssuerAnnouncementContractError
        return self

    @property
    def retention(self) -> DataRetentionPolicy:
        return DataRetentionPolicy(
            raw_retention_days=self.raw_retention_days,
            derived_retention_days=self.derived_retention_days,
            deletion_required=self.deletion_required,
            correction_policy=self.correction_policy,
        )


class IssuerAnnouncementRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    onboarding: IssuerAnnouncementOnboarding = Field(repr=False)
    requested_at: dt.datetime

    @field_validator("requested_at")
    @classmethod
    def normalize_requested_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _OPAQUE_ID.fullmatch(self.collection_id) is None
            or not _aware(self.requested_at)
            or self.requested_at < self.onboarding.effective_from
            or self.requested_at - self.onboarding.license_reviewed_at
            > dt.timedelta(days=365)
            or (
                self.onboarding.effective_to is not None
                and self.requested_at >= self.onboarding.effective_to
            )
        ):
            raise IssuerAnnouncementContractError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class IssuerAnnouncementRawReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    received_at: dt.datetime
    status_code: int = Field(ge=100, le=599)
    content_type: str
    raw_payload_sha256: str
    raw_payload_base64: str = Field(repr=False)

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        try:
            raw = base64.b64decode(self.raw_payload_base64, validate=True)
        except (ValueError, TypeError):
            raise IssuerAnnouncementContractError from None
        if (
            _HEX64.fullmatch(self.request_id) is None
            or not _aware(self.received_at)
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or _HEX64.fullmatch(self.raw_payload_sha256) is None
            or type(raw) is not bytes
            or len(raw) > ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES
            or hashlib.sha256(raw).hexdigest() != self.raw_payload_sha256
        ):
            raise IssuerAnnouncementContractError
        return self

    @property
    def raw_payload(self) -> bytes:
        return base64.b64decode(self.raw_payload_base64, validate=True)

    @property
    def receipt_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()

    @classmethod
    def from_raw(
        cls,
        *,
        request_id: str,
        received_at: dt.datetime,
        status_code: int,
        content_type: str,
        raw_payload: bytes,
    ) -> Self:
        if type(raw_payload) is not bytes:
            raise IssuerAnnouncementContractError
        return cls(
            request_id=request_id,
            received_at=received_at,
            status_code=status_code,
            content_type=content_type,
            raw_payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
            raw_payload_base64=base64.b64encode(raw_payload).decode("ascii"),
        )


class IssuerAnnouncementEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_id: DataSourceId
    issuer_id: str
    provider_event_id: str = Field(repr=False)
    symbols: tuple[str, ...]
    published_at: dt.datetime
    url: str = Field(repr=False)
    title_sha256: str
    raw_receipt_id: str

    @field_validator("published_at")
    @classmethod
    def normalize_published_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        parsed = _safe_https_url(self.url)
        if (
            _ISSUER_ID.fullmatch(self.issuer_id) is None
            or not 1 <= len(self.provider_event_id) <= 512
            or any(character < " " for character in self.provider_event_id)
            or not self.symbols
            or any(_SYMBOL.fullmatch(symbol) is None for symbol in self.symbols)
            or not _aware(self.published_at)
            or parsed is None
            or _HEX64.fullmatch(self.title_sha256) is None
            or _HEX64.fullmatch(self.raw_receipt_id) is None
        ):
            raise IssuerAnnouncementContractError
        return self

    @property
    def event_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class IssuerAnnouncementRunStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class IssuerAnnouncementFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"


class IssuerAnnouncementTerminal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    completed_at: dt.datetime
    status: IssuerAnnouncementRunStatus
    failure_code: IssuerAnnouncementFailure | None
    receipt_id: str | None
    announcement_count: int = Field(ge=0, le=100)
    event_ids: tuple[str, ...] = Field(max_length=100)
    latest_published_at: dt.datetime | None

    @field_validator("completed_at", "latest_published_at")
    @classmethod
    def normalize_optional_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return value.astimezone(dt.UTC) if value is not None and _aware(value) else value

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        success = self.status is IssuerAnnouncementRunStatus.SUCCESS
        if (
            _HEX64.fullmatch(self.request_id) is None
            or not _aware(self.completed_at)
            or success != (self.failure_code is None)
            or (success and self.receipt_id is None)
            or (
                self.failure_code is not None
                and self.failure_code is not IssuerAnnouncementFailure.TRANSPORT
                and self.receipt_id is None
            )
            or (
                self.receipt_id is not None
                and _HEX64.fullmatch(self.receipt_id) is None
            )
            or self.announcement_count != len(self.event_ids)
            or len(self.event_ids) != len(set(self.event_ids))
            or any(_HEX64.fullmatch(identity) is None for identity in self.event_ids)
            or (self.announcement_count == 0) != (self.latest_published_at is None)
            or (
                self.latest_published_at is not None
                and (
                    not _aware(self.latest_published_at)
                    or self.latest_published_at > self.completed_at
                )
            )
        ):
            raise IssuerAnnouncementContractError
        return self


def _safe_https_url(value: str) -> SplitResult | None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
        or not parsed.path.startswith("/")
        or not _safe_host(parsed.hostname)
    ):
        return None
    return parsed


def _safe_host(value: str) -> bool:
    if (
        value != value.lower()
        or len(value) > 253
        or value == "localhost"
        or value.endswith(".local")
        or "." not in value
    ):
        return False
    try:
        _ = ipaddress.ip_address(value)
    except ValueError:
        return all(
            label
            and len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(character.isalnum() or character == "-" for character in label)
            for label in value.split(".")
        )
    return False


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES",
    "IssuerAnnouncementContractError",
    "IssuerAnnouncementEvent",
    "IssuerAnnouncementFailure",
    "IssuerAnnouncementFeedFormat",
    "IssuerAnnouncementOnboarding",
    "IssuerAnnouncementRawReceipt",
    "IssuerAnnouncementRequest",
    "IssuerAnnouncementRunStatus",
    "IssuerAnnouncementTerminal",
)
