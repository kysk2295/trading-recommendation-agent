from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from trading_agent.browser_social_evidence import BrowserSocialEvidence, canonical_browser_social_evidence_json
from trading_agent.kr_instrument import is_kr_instrument_symbol_v2


class KrSocialVerificationState(StrEnum):
    UNVERIFIED_SOCIAL = "unverified_social"
    MULTI_SOURCE_CORROBORATED = "multi_source_corroborated"


class KrSocialSignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    task_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: str
    theme: str = Field(min_length=1, max_length=160)
    claim_summary: str = Field(min_length=8, max_length=1_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    normalized_at: AwareDatetime

    @field_validator("normalized_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            not self.theme.strip()
            or not self.claim_summary.strip()
            or self.evidence_ids != tuple(sorted(set(self.evidence_ids)))
            or not all(_is_sha256(value) for value in self.evidence_ids)
        ):
            raise PydanticCustomError("kr_social_signal_request", "KR social signal request is invalid")
        return self


class KrSocialSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    signal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: str
    theme: str = Field(min_length=1, max_length=160)
    claim_summary: str = Field(min_length=8, max_length=1_000)
    evidence_ids: tuple[str, ...]
    source_payload_sha256s: tuple[str, ...]
    repost_cluster_ids: tuple[str, ...]
    independent_source_cluster_ids: tuple[str, ...]
    post_count: int = Field(ge=1, le=64)
    repost_cluster_count: int = Field(ge=1, le=64)
    independent_source_count: int = Field(ge=1, le=64)
    verification_state: KrSocialVerificationState
    earliest_published_at: AwareDatetime | None
    first_observed_at: AwareDatetime
    normalized_at: AwareDatetime

    @field_validator("earliest_published_at", "first_observed_at", "normalized_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_signal(self) -> Self:
        state = (
            KrSocialVerificationState.MULTI_SOURCE_CORROBORATED
            if self.independent_source_count >= 2
            else KrSocialVerificationState.UNVERIFIED_SOCIAL
        )
        tuples_are_valid = all(
            values == tuple(sorted(set(values))) and all(_is_sha256(value) for value in values)
            for values in (
                self.evidence_ids,
                self.source_payload_sha256s,
                self.repost_cluster_ids,
                self.independent_source_cluster_ids,
            )
        )
        chronology_is_valid = (
            self.earliest_published_at is None or self.earliest_published_at <= self.first_observed_at
        ) and self.first_observed_at <= self.normalized_at
        if (
            not is_kr_instrument_symbol_v2(self.symbol)
            or not self.theme.strip()
            or not self.claim_summary.strip()
            or not tuples_are_valid
            or len(self.evidence_ids) != self.post_count
            or len(self.source_payload_sha256s) != self.post_count
            or len(self.repost_cluster_ids) != self.repost_cluster_count
            or len(self.independent_source_cluster_ids) != self.independent_source_count
            or self.verification_state is not state
            or not chronology_is_valid
            or self.signal_id != _signal_id(self)
        ):
            raise PydanticCustomError("kr_social_signal", "KR social signal is invalid")
        return self


def normalize_kr_social_signal(
    request: KrSocialSignalRequest, evidence: Sequence[BrowserSocialEvidence]
) -> KrSocialSignal:
    trusted_request = KrSocialSignalRequest.model_validate(request.model_dump(mode="python"))
    selected = tuple(
        BrowserSocialEvidence.model_validate(item.model_dump(mode="python"))
        for item in evidence
        if item.evidence_id in trusted_request.evidence_ids
    )
    ordered = tuple(sorted(selected, key=lambda item: (item.first_observed_at, item.evidence_id)))
    if (
        len({item.evidence_id for item in ordered}) != len(ordered)
        or tuple(item.evidence_id for item in sorted(ordered, key=lambda item: item.evidence_id))
        != trusted_request.evidence_ids
        or any(
            item.first_observed_at > trusted_request.normalized_at
            or (item.published_at is not None and item.published_at > trusted_request.normalized_at)
            for item in ordered
        )
    ):
        raise InvalidKrSocialSignalError()
    payload = {
        "schema_version": 1,
        "task_id": trusted_request.task_id,
        "symbol": trusted_request.symbol,
        "theme": trusted_request.theme,
        "claim_summary": trusted_request.claim_summary,
        "evidence_ids": trusted_request.evidence_ids,
        "source_payload_sha256s": tuple(
            sorted(
                hashlib.sha256(canonical_browser_social_evidence_json(item).encode("ascii")).hexdigest()
                for item in ordered
            )
        ),
        "repost_cluster_ids": tuple(sorted({item.repost_cluster_id for item in ordered})),
        "independent_source_cluster_ids": tuple(sorted({item.independent_source_cluster_id for item in ordered})),
        "post_count": len(ordered),
        "repost_cluster_count": len({item.repost_cluster_id for item in ordered}),
        "independent_source_count": len({item.independent_source_cluster_id for item in ordered}),
        "verification_state": (
            KrSocialVerificationState.MULTI_SOURCE_CORROBORATED
            if len({item.independent_source_cluster_id for item in ordered}) >= 2
            else KrSocialVerificationState.UNVERIFIED_SOCIAL
        ),
        "earliest_published_at": min(
            (item.published_at for item in ordered if item.published_at is not None), default=None
        ),
        "first_observed_at": ordered[0].first_observed_at,
        "normalized_at": trusted_request.normalized_at,
    }
    identity = _signal_id(KrSocialSignal.model_construct(signal_id="", **payload))
    return KrSocialSignal.model_validate(payload | {"signal_id": identity})


def canonical_kr_social_signal_json(signal: KrSocialSignal) -> str:
    trusted = KrSocialSignal.model_validate(signal.model_dump(mode="python"))
    return json.dumps(trusted.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _signal_id(signal: KrSocialSignal) -> str:
    payload = json.dumps(
        signal.model_dump(mode="json", exclude={"signal_id"}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class InvalidKrSocialSignalError(ValueError):
    def __str__(self) -> str:
        return "KR social signal is invalid"


__all__ = (
    "InvalidKrSocialSignalError",
    "KrSocialSignal",
    "KrSocialSignalRequest",
    "KrSocialVerificationState",
    "canonical_kr_social_signal_json",
    "normalize_kr_social_signal",
)
