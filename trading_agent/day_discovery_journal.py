from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.experiment_ledger_models import ResearchHypothesisCard, ResearchSource
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    LlmCallReceipt,
    ProposedHypothesis,
)
from trading_agent.strategy_research_models import PreregistrationManifest


@dataclass(frozen=True, slots=True)
class InvalidDayDiscoveryJournalError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class PreparedLlmReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int | None
    temperature: float
    called_at: dt.datetime

    def receipt(self) -> LlmCallReceipt:
        return LlmCallReceipt(**self.model_dump(mode="python"))


class PreparedStrategyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_code: str
    free_parameters: tuple[str, ...]
    methodology_tags: tuple[str, ...]

    def draft(self) -> CandidateStrategyDraft:
        return CandidateStrategyDraft(**self.model_dump(mode="python"))


class DayDiscoveryPreparedBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_index: int = Field(ge=0, le=2)
    proposal_card: ResearchHypothesisCard | None
    cited_sources: tuple[ResearchSource, ...]
    llm_receipt: PreparedLlmReceipt
    strategy_draft: PreparedStrategyDraft
    terminal_reason: str | None
    family: HypothesisFamily
    version: HypothesisVersion
    preregistration: PreregistrationManifest
    attempt_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_started_at: dt.datetime
    attempt_finished_at: dt.datetime
    bound_at: dt.datetime
    published_at: dt.datetime
    search_budget_debit: int = Field(ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_prepared_identity(self) -> Self:
        if (
            self.version.family_id != self.family.family_id
            or self.preregistration.hypothesis.hypothesis_id
            != self.version.hypothesis_version_id
            or self.attempt_started_at > self.attempt_finished_at
            or self.attempt_finished_at >= self.bound_at
            or self.bound_at >= self.published_at
            or (self.terminal_reason is None) != (self.proposal_card is not None)
        ):
            raise InvalidDayDiscoveryJournalError("prepared_branch_identity_invalid")
        return self

    def proposal(self) -> ProposedHypothesis:
        if self.proposal_card is None:
            raise InvalidDayDiscoveryJournalError("prepared_branch_proposal_missing")
        return ProposedHypothesis(
            card=self.proposal_card,
            cited_sources=self.cited_sources,
            llm_receipt=self.llm_receipt.receipt(),
            strategy_draft=self.strategy_draft.draft(),
        )


def prepared_branch_path(root: Path, cycle_id: str, branch_index: int) -> Path:
    return root / f"{cycle_id}.prepared.{branch_index}.json"


def publish_prepared_branch(path: Path, prepared: DayDiscoveryPreparedBranch) -> None:
    try:
        _ = publish_private_immutable_text(path, _canonical(prepared))
    except InvalidPrivateImmutableFileError:
        raise InvalidDayDiscoveryJournalError("prepared_branch_publication_failed") from None


def read_prepared_branch(
    path: Path,
    cycle_id: str,
    evidence_sha256: str,
    branch_index: int,
) -> DayDiscoveryPreparedBranch | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        raw = read_private_text(path)
        prepared = DayDiscoveryPreparedBranch.model_validate_json(raw)
    except (
        InvalidDayDiscoveryJournalError,
        InvalidPrivateImmutableFileError,
        ValueError,
    ):
        raise InvalidDayDiscoveryJournalError("prepared_branch_invalid") from None
    if raw != _canonical(prepared):
        raise InvalidDayDiscoveryJournalError("prepared_branch_noncanonical")
    if (
        prepared.cycle_id != cycle_id
        or prepared.evidence_sha256 != evidence_sha256
        or prepared.branch_index != branch_index
    ):
        raise InvalidDayDiscoveryJournalError("prepared_branch_identity_conflict")
    return prepared


def _canonical(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = (
    "DayDiscoveryPreparedBranch",
    "InvalidDayDiscoveryJournalError",
    "PreparedLlmReceipt",
    "PreparedStrategyDraft",
    "prepared_branch_path",
    "publish_prepared_branch",
    "read_prepared_branch",
)
