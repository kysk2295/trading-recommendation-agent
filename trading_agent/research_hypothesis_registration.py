from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import ExperimentScope


class InvalidResearchHypothesisManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "연구 출처와 가설 사전등록 manifest 계약이 유효하지 않습니다"


class ResearchHypothesisManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    research_sources: tuple[ResearchSource, ...]
    experiment_scope: ExperimentScope
    hypothesis: str
    falsification_rule: str
    research_source_ids: tuple[str, ...]
    economic_mechanism: str
    counterfactual_baseline: str
    ledger_recorded_at: dt.datetime

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        source_ids = tuple(source.source_id for source in self.research_sources)
        if (
            self.schema_version != 1
            or not self.research_sources
            or source_ids != tuple(sorted(set(source_ids)))
            or not self.research_source_ids
            or self.research_source_ids != tuple(sorted(set(self.research_source_ids)))
            or not set(self.research_source_ids).issubset(source_ids)
            or not _canonical_text(self.hypothesis)
            or not _canonical_text(self.falsification_rule)
            or not _canonical_text(self.economic_mechanism)
            or not _canonical_text(self.counterfactual_baseline)
            or not _aware(self.ledger_recorded_at)
            or self.ledger_recorded_at < self.experiment_scope.registered_at
            or any(
                source.ledger_recorded_at > self.experiment_scope.registered_at
                for source in self.research_sources
            )
        ):
            raise ValueError("invalid research hypothesis manifest")
        return self


@dataclass(frozen=True, slots=True)
class ResearchHypothesisRegistrationResult:
    sources_created: int
    cards_created: int
    sources_total: int
    cards_total: int


def register_research_hypothesis_manifest(
    manifest_path: Path,
    ledger: ExperimentLedgerStore,
) -> ResearchHypothesisRegistrationResult:
    manifest = load_research_hypothesis_manifest(manifest_path)
    hypothesis = HypothesisRegistration(
        hypothesis_id=manifest.experiment_scope.hypothesis_id,
        experiment_scope=manifest.experiment_scope,
        experiment_scope_key=experiment_scope_key(manifest.experiment_scope),
        primary_lane=manifest.experiment_scope.primary_lane,
        hypothesis=manifest.hypothesis,
        falsification_rule=manifest.falsification_rule,
        source_registered_at=manifest.experiment_scope.registered_at,
        ledger_recorded_at=manifest.ledger_recorded_at,
    )
    sources_by_id = {source.source_id: source for source in manifest.research_sources}
    card = ResearchHypothesisCard(
        hypothesis=hypothesis,
        research_source_keys=tuple(
            sorted(str(research_source_key(sources_by_id[source_id])) for source_id in manifest.research_source_ids)
        ),
        economic_mechanism=manifest.economic_mechanism,
        counterfactual_baseline=manifest.counterfactual_baseline,
    )
    try:
        with ledger.writer() as writer:
            sources_created = sum(writer.register_research_source(source) for source in manifest.research_sources)
            cards_created = int(writer.register_research_hypothesis(card))
    except (ExperimentLedgerConflictError, InvalidExperimentLedgerSourceError, ValueError) as error:
        raise InvalidResearchHypothesisManifestError from error
    return ResearchHypothesisRegistrationResult(
        sources_created=sources_created,
        cards_created=cards_created,
        sources_total=len(manifest.research_sources),
        cards_total=1,
    )


def load_research_hypothesis_manifest(path: Path) -> ResearchHypothesisManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ResearchHypothesisManifest.model_validate(payload)
    except (json.JSONDecodeError, OSError, UnicodeError, ValidationError, ValueError) as error:
        raise InvalidResearchHypothesisManifestError from error


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()
