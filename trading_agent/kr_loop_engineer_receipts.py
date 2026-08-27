from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
_SHA = r"^[a-f0-9]{64}$"
_GIT_SHA = r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$"


class InvalidKrLoopEngineerReceiptError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR Loop Engineer receipt is invalid"


class KrLoopShadowReceipt(BaseModel):
    model_config = _STRICT

    session_date: dt.date
    observed_at: AwareDatetime
    champion_score: Decimal
    challenger_score: Decimal
    error_count: int = Field(ge=0)
    data_eligibility_failures: int = Field(ge=0)
    order_mismatches: int = Field(ge=0)
    research_task_losses: int = Field(ge=0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise InvalidKrLoopEngineerReceiptError
        return self


class KrLoopHealthReceipt(BaseModel):
    model_config = _STRICT

    release_id: str = Field(pattern=_SHA)
    observed_at: AwareDatetime
    error_rate: Decimal = Field(ge=0, le=1)
    data_eligibility_failures: int = Field(ge=0)
    order_mismatches: int = Field(ge=0)
    research_task_losses: int = Field(ge=0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise InvalidKrLoopEngineerReceiptError
        return self


class KrLoopValidationReceipt(BaseModel):
    model_config = _STRICT

    receipt_id: str = Field(pattern=_SHA)
    candidate_id: str = Field(pattern=_SHA)
    candidate_commit: str = Field(pattern=_GIT_SHA)
    verified_at: AwareDatetime
    pytest_passed: bool
    ruff_passed: bool
    basedpyright_passed: bool
    manual_qa_passed: bool
    replay_passed: bool
    lookahead_violations: int = Field(ge=0)
    broker_mutations: int = Field(ge=0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))) or self.receipt_id != _identity(self):
            raise InvalidKrLoopEngineerReceiptError
        return self

    @classmethod
    def build(
        cls,
        *,
        candidate_id: str,
        candidate_commit: str,
        verified_at: dt.datetime,
        pytest_passed: bool,
        ruff_passed: bool,
        basedpyright_passed: bool,
        manual_qa_passed: bool,
        replay_passed: bool,
        lookahead_violations: int,
        broker_mutations: int,
        evidence_refs: tuple[str, ...],
    ) -> KrLoopValidationReceipt:
        draft = cls.model_construct(
            receipt_id="",
            candidate_id=candidate_id,
            candidate_commit=candidate_commit,
            verified_at=verified_at,
            pytest_passed=pytest_passed,
            ruff_passed=ruff_passed,
            basedpyright_passed=basedpyright_passed,
            manual_qa_passed=manual_qa_passed,
            replay_passed=replay_passed,
            lookahead_violations=lookahead_violations,
            broker_mutations=broker_mutations,
            evidence_refs=tuple(sorted(set(evidence_refs))),
        )
        return cls.model_validate(draft.model_copy(update={"receipt_id": _identity(draft)}).model_dump(mode="python"))


def _identity(receipt: KrLoopValidationReceipt) -> str:
    payload = json.dumps(
        receipt.model_dump(mode="json", exclude={"receipt_id"}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


__all__ = (
    "InvalidKrLoopEngineerReceiptError",
    "KrLoopHealthReceipt",
    "KrLoopShadowReceipt",
    "KrLoopValidationReceipt",
)
