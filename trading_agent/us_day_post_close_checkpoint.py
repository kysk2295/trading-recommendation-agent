from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.day_agent_version_models import AgentChangeProposal
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


class InvalidUsDayPostCloseCheckpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "us_day_post_close_checkpoint_invalid"


class PostCloseCheckpointIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tick_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_id: str = Field(pattern=r"^XNYS-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    champion_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class PaperFinalizedCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Literal["paper_finalized"] = "paper_finalized"
    identity: PostCloseCheckpointIdentity
    authority: Literal["host_validated_alpaca_paper"] = "host_validated_alpaca_paper"
    paper_status: str = Field(min_length=1, max_length=64)


class ReportPublishedCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Literal["report_published"] = "report_published"
    identity: PostCloseCheckpointIdentity
    paper_status: str = Field(min_length=1, max_length=64)
    report: MarketCloseReport

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if self.report.payload.agent_version_id != self.identity.champion_version_id:
            raise InvalidUsDayPostCloseCheckpointError
        return self


class LoopProposedCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Literal["loop_proposed"] = "loop_proposed"
    identity: PostCloseCheckpointIdentity
    report_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal: AgentChangeProposal

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if self.proposal.parent_version_id != self.identity.champion_version_id:
            raise InvalidUsDayPostCloseCheckpointError
        return self


@dataclass(frozen=True, slots=True)
class UsDayPostCloseCheckpointState:
    paper: PaperFinalizedCheckpoint | None
    report: ReportPublishedCheckpoint | None
    loop: LoopProposedCheckpoint | None


@dataclass(frozen=True, slots=True)
class UsDayPostCloseCheckpointStore:
    root: Path

    def read(self, identity: PostCloseCheckpointIdentity) -> UsDayPostCloseCheckpointState:
        try:
            directory = self.root / identity.tick_id
            paper = self._read(directory / "paper_finalized.json", PaperFinalizedCheckpoint)
            report = self._read(directory / "report_published.json", ReportPublishedCheckpoint)
            loop = self._read(directory / "loop_proposed.json", LoopProposedCheckpoint)
            checkpoints = tuple(item for item in (paper, report, loop) if item is not None)
            if (
                any(item.identity != identity for item in checkpoints)
                or (report is not None and paper is None)
                or (loop is not None and report is None)
                or (report is not None and paper is not None and report.paper_status != paper.paper_status)
                or (loop is not None and report is not None and loop.report_id != report.report.report_id)
            ):
                raise InvalidUsDayPostCloseCheckpointError
            return UsDayPostCloseCheckpointState(paper, report, loop)
        except InvalidUsDayPostCloseCheckpointError:
            raise
        except (InvalidPrivateImmutableFileError, OSError, ValidationError, ValueError):
            raise InvalidUsDayPostCloseCheckpointError from None

    def publish_paper(self, checkpoint: PaperFinalizedCheckpoint) -> None:
        self._publish(checkpoint.identity, "paper_finalized.json", checkpoint)

    def publish_report(self, checkpoint: ReportPublishedCheckpoint) -> None:
        self._publish(checkpoint.identity, "report_published.json", checkpoint)

    def publish_loop(self, checkpoint: LoopProposedCheckpoint) -> None:
        self._publish(checkpoint.identity, "loop_proposed.json", checkpoint)

    def _publish(
        self,
        identity: PostCloseCheckpointIdentity,
        filename: str,
        checkpoint: PaperFinalizedCheckpoint | ReportPublishedCheckpoint | LoopProposedCheckpoint,
    ) -> None:
        try:
            _ = publish_private_immutable_text(
                self.root / identity.tick_id / filename,
                canonical_experiment_ledger_json(checkpoint) + "\n",
            )
        except InvalidPrivateImmutableFileError:
            raise InvalidUsDayPostCloseCheckpointError from None

    @staticmethod
    def _read[CheckpointT: BaseModel](path: Path, model: type[CheckpointT]) -> CheckpointT | None:
        if not path.exists():
            return None
        return model.model_validate_json(read_private_text(path))


__all__ = (
    "InvalidUsDayPostCloseCheckpointError",
    "LoopProposedCheckpoint",
    "PaperFinalizedCheckpoint",
    "PostCloseCheckpointIdentity",
    "ReportPublishedCheckpoint",
    "UsDayPostCloseCheckpointState",
    "UsDayPostCloseCheckpointStore",
)
