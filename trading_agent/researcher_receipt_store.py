from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from trading_agent.critic_agent import CritiqueReport
from trading_agent.experiment_ledger_keys import research_hypothesis_card_key
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.researcher_agent import LlmCallReceipt, ProposedHypothesis


class ResearcherReceiptStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "researcher append-only receipt publication failed"


class LlmCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    call_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(min_length=1, max_length=128)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int | None
    temperature: float = Field(ge=0.0, le=2.0)
    called_at: AwareDatetime


class CritiqueRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    card_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_blocked: bool
    objections: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class VerifiedLlmCall:
    record: LlmCallRecord
    prompt: str
    response: bytes


class ResearcherReceiptStore:
    __slots__ = ("root",)

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)

    def record_call(
        self,
        *,
        model_id: str,
        prompt: str,
        response: bytes,
        seed: int | None,
        temperature: float,
        called_at: dt.datetime,
    ) -> LlmCallReceipt:
        prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        response_sha256 = hashlib.sha256(response).hexdigest()
        call_id = _call_id(
            model_id=model_id,
            prompt_sha256=prompt_sha256,
            response_sha256=response_sha256,
            seed=seed,
            temperature=temperature,
            called_at=called_at,
        )
        record = LlmCallRecord(
            call_id=call_id,
            model_id=model_id,
            prompt_sha256=prompt_sha256,
            response_sha256=response_sha256,
            seed=seed,
            temperature=temperature,
            called_at=called_at,
        )
        try:
            _ = publish_private_immutable_text(self.root / "prompts" / f"{prompt_sha256}.txt", prompt)
            _ = publish_private_immutable_text(
                self.root / "responses" / f"{response_sha256}.txt",
                base64.b64encode(response).decode("ascii"),
            )
            _ = publish_private_immutable_text(
                self.root / "calls" / f"{call_id}.json",
                record.model_dump_json(),
            )
        except (OSError, TypeError, ValueError) as error:
            raise ResearcherReceiptStoreError from error
        return LlmCallReceipt(
            model_id=record.model_id,
            prompt_sha256=record.prompt_sha256,
            response_sha256=record.response_sha256,
            seed=record.seed,
            temperature=record.temperature,
            called_at=record.called_at,
        )

    def require_call(self, receipt: LlmCallReceipt) -> VerifiedLlmCall:
        call_id = _call_id(
            model_id=receipt.model_id,
            prompt_sha256=receipt.prompt_sha256,
            response_sha256=receipt.response_sha256,
            seed=receipt.seed,
            temperature=receipt.temperature,
            called_at=receipt.called_at,
        )
        try:
            record = LlmCallRecord.model_validate_json(
                read_private_text(self.root / "calls" / f"{call_id}.json")
            )
            prompt = read_private_text(self.root / "prompts" / f"{record.prompt_sha256}.txt")
            encoded_response = read_private_text(
                self.root / "responses" / f"{record.response_sha256}.txt"
            )
            response = base64.b64decode(encoded_response, validate=True)
            expected_receipt = LlmCallReceipt(
                model_id=record.model_id,
                prompt_sha256=record.prompt_sha256,
                response_sha256=record.response_sha256,
                seed=record.seed,
                temperature=record.temperature,
                called_at=record.called_at,
            )
            if (
                record.call_id != call_id
                or expected_receipt != receipt
                or hashlib.sha256(prompt.encode()).hexdigest() != record.prompt_sha256
                or hashlib.sha256(response).hexdigest() != record.response_sha256
            ):
                raise ResearcherReceiptStoreError
        except (
            InvalidPrivateImmutableFileError,
            ResearcherReceiptStoreError,
            ValidationError,
            binascii.Error,
            TypeError,
            ValueError,
        ) as error:
            if isinstance(error, ResearcherReceiptStoreError):
                raise
            raise ResearcherReceiptStoreError from error
        return VerifiedLlmCall(record=record, prompt=prompt, response=response)

    def record_critique(
        self,
        proposal: ProposedHypothesis,
        report: CritiqueReport,
    ) -> Path:
        record = CritiqueRecord(
            response_sha256=proposal.llm_receipt.response_sha256,
            card_key=str(research_hypothesis_card_key(proposal.card)),
            is_blocked=report.is_blocked,
            objections=tuple(
                (objection.kind.value, objection.severity.value, objection.evidence)
                for objection in report.objections
            ),
        )
        path = self.root / "critiques" / f"{record.response_sha256}.json"
        try:
            _ = publish_private_immutable_text(path, record.model_dump_json())
        except (OSError, TypeError, ValueError) as error:
            raise ResearcherReceiptStoreError from error
        return path


def _call_id(
    *,
    model_id: str,
    prompt_sha256: str,
    response_sha256: str,
    seed: int | None,
    temperature: float,
    called_at: dt.datetime,
) -> str:
    return hashlib.sha256(
        (
            f"{model_id}\0{prompt_sha256}\0{response_sha256}\0{seed}\0"
            f"{temperature}\0{called_at.isoformat()}"
        ).encode()
    ).hexdigest()


__all__ = (
    "CritiqueRecord",
    "LlmCallRecord",
    "ResearcherReceiptStore",
    "ResearcherReceiptStoreError",
    "VerifiedLlmCall",
)
