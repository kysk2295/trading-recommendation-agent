from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from trading_agent.local_browser_protocol import (
    InvalidLocalBrowserProtocolError,
    require_public_https_url,
)

type BrowserSourceKind = Literal["social", "community", "news", "search", "web"]


@dataclass(frozen=True, slots=True)
class InvalidBrowserSocialEvidenceError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class BrowserSocialEvidenceCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    browser_receipt_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_url: str = Field(min_length=8, max_length=2_048)
    source_kind: BrowserSourceKind
    source_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(default="", max_length=500)
    author_label: str = Field(default="", max_length=200)
    excerpt: str = Field(min_length=1, max_length=2_000)
    published_at: AwareDatetime | None = None
    first_observed_at: AwareDatetime
    captured_at: AwareDatetime
    screenshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("normalized_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        try:
            return require_public_https_url(value)
        except InvalidLocalBrowserProtocolError:
            raise PydanticCustomError("browser_social_url", "browser URL is not public HTTPS") from None

    @field_validator("published_at", "first_observed_at", "captured_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_chronology(self) -> Self:
        publication_is_causal = self.published_at is None or self.published_at <= self.first_observed_at
        if not publication_is_causal or self.first_observed_at > self.captured_at:
            raise PydanticCustomError("browser_social_chronology", "browser evidence chronology is invalid")
        return self


class BrowserSocialEvidence(BrowserSocialEvidenceCapture):
    evidence_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    repost_cluster_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    independent_source_cluster_id: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_derived_lineage(self) -> Self:
        content_sha256 = hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()
        if (
            self.evidence_id != _evidence_id(self.browser_receipt_id, self.normalized_url, self.captured_at)
            or self.content_sha256 != content_sha256
            or self.repost_cluster_id != content_sha256
            or self.independent_source_cluster_id != self.source_identity_sha256
        ):
            raise PydanticCustomError("browser_social_lineage", "browser evidence lineage is invalid")
        return self


def browser_source_identity_sha256(identity: str) -> str:
    if not identity.strip() or len(identity) > 4_096:
        raise InvalidBrowserSocialEvidenceError(reason="browser_source_identity_invalid")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def browser_social_evidence(capture: BrowserSocialEvidenceCapture) -> BrowserSocialEvidence:
    trusted = BrowserSocialEvidenceCapture.model_validate(capture.model_dump(mode="python"))
    content_sha256 = hashlib.sha256(trusted.excerpt.encode("utf-8")).hexdigest()
    return BrowserSocialEvidence(
        **trusted.model_dump(mode="python"),
        evidence_id=_evidence_id(trusted.browser_receipt_id, trusted.normalized_url, trusted.captured_at),
        content_sha256=content_sha256,
        repost_cluster_id=content_sha256,
        independent_source_cluster_id=trusted.source_identity_sha256,
    )


def canonical_browser_social_evidence_json(evidence: BrowserSocialEvidence) -> str:
    validated = BrowserSocialEvidence.model_validate(evidence.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _evidence_id(browser_receipt_id: str, normalized_url: str, captured_at: datetime) -> str:
    identity = json.dumps(
        {
            "browser_receipt_id": browser_receipt_id,
            "captured_at": _canonical_timestamp(captured_at),
            "normalized_url": normalized_url,
            "schema_version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(identity.encode("ascii")).hexdigest()


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = (
    "BrowserSocialEvidence",
    "BrowserSocialEvidenceCapture",
    "BrowserSourceKind",
    "InvalidBrowserSocialEvidenceError",
    "browser_social_evidence",
    "browser_source_identity_sha256",
    "canonical_browser_social_evidence_json",
)
