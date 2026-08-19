from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
)
from trading_agent.experiment_scope_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_identity_models import LaneId
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    read_private_text,
)
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    ProposedHypothesis,
    ResearcherContext,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore

_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_FREE_PARAMETERS: Final = 4
_PROVIDER_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")


class ResearcherLlmError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "structured researcher LLM call failed closed"


class ResearcherContextInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    lane_id: LaneId
    sources: tuple[ResearchSource, ...]
    regime_context: str = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        source_ids = tuple(source.source_id for source in self.sources)
        if not source_ids or source_ids != tuple(sorted(set(source_ids))):
            raise ResearcherLlmError
        return self


class LlmHypothesisDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    hypothesis_id: str = Field(min_length=1, max_length=128)
    hypothesis: str = Field(min_length=1, max_length=4_096)
    falsification_rule: str = Field(min_length=1, max_length=4_096)
    cited_source_ids: tuple[str, ...]
    economic_mechanism: str = Field(min_length=1, max_length=4_096)
    counterfactual_baseline: str = Field(min_length=1, max_length=4_096)
    strategy_source: str = Field(min_length=1, max_length=64 * 1024)
    free_parameters: tuple[str, ...]
    methodology_tags: tuple[str, ...] = ()

    @field_validator("cited_source_ids", "free_parameters", "methodology_tags", mode="after")
    @classmethod
    def canonicalize_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_citations(self) -> Self:
        if not self.cited_source_ids:
            raise ResearcherLlmError
        return self


class LlmProposalClient(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def seed(self) -> int | None: ...

    @property
    def temperature(self) -> float: ...

    def complete(self, prompt: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class FixtureLlmProposalClient:
    response: bytes
    model_id: str = "fixture-researcher-v1"
    seed: int | None = 7
    temperature: float = 0.0

    def complete(self, prompt: str) -> bytes:
        del prompt
        return self.response


@dataclass(frozen=True, slots=True)
class HermesCliProposalClient:
    executable: Path
    model_id: str
    provider_id: str
    seed: int | None = None
    temperature: float = 0.2
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if _PROVIDER_ID.fullmatch(self.provider_id) is None:
            raise ResearcherLlmError

    def complete(self, prompt: str) -> bytes:
        try:
            executable = self.executable.resolve(strict=True)
            metadata = executable.stat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or not os.access(executable, os.X_OK)
            ):
                raise ResearcherLlmError
            completed = subprocess.run(
                (
                    str(executable),
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--provider",
                    self.provider_id,
                    "-m",
                    self.model_id,
                    "-t",
                    "",
                    "-z",
                    prompt,
                ),
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > _MAX_RESPONSE_BYTES:
                raise ResearcherLlmError
            return completed.stdout
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ResearcherLlmError from error


@dataclass(frozen=True, slots=True)
class StructuredHypothesisGenerator:
    client: LlmProposalClient
    receipts: ResearcherReceiptStore
    clock: Callable[[], dt.datetime]

    def propose(self, context: ResearcherContext) -> ProposedHypothesis:
        prompt = _prompt(context)
        response = self.client.complete(prompt)
        called_at = self.clock()
        receipt = self.receipts.record_call(
            model_id=self.client.model_id,
            prompt=prompt,
            response=response,
            seed=self.client.seed,
            temperature=self.client.temperature,
            called_at=called_at,
        )
        try:
            draft = LlmHypothesisDraft.model_validate_json(response)
            sources_by_id = {source.source_id: source for source in context.sources}
            cited_sources = tuple(sources_by_id[source_id] for source_id in draft.cited_source_ids)
            if called_at.tzinfo is None or called_at.utcoffset() is None or any(
                source.ledger_recorded_at > called_at for source in cited_sources
            ):
                raise ResearcherLlmError
            scope = ExperimentScope(
                scope_kind=ExperimentScopeKind.SINGLE_LANE,
                hypothesis_id=draft.hypothesis_id,
                primary_lane=context.lane_id,
                lanes=(context.lane_id,),
                registered_at=called_at,
            )
            registration = HypothesisRegistration(
                hypothesis_id=draft.hypothesis_id,
                experiment_scope=scope,
                experiment_scope_key=experiment_scope_key(scope),
                primary_lane=context.lane_id,
                hypothesis=draft.hypothesis,
                falsification_rule=draft.falsification_rule,
                source_registered_at=called_at,
                ledger_recorded_at=called_at,
            )
            return ProposedHypothesis(
                card=ResearchHypothesisCard(
                    hypothesis=registration,
                    research_source_keys=tuple(
                        sorted(str(research_source_key(source)) for source in cited_sources)
                    ),
                    economic_mechanism=draft.economic_mechanism,
                    counterfactual_baseline=draft.counterfactual_baseline,
                ),
                cited_sources=cited_sources,
                llm_receipt=receipt,
                strategy_draft=CandidateStrategyDraft(
                    source_code=draft.strategy_source,
                    free_parameters=draft.free_parameters,
                    methodology_tags=draft.methodology_tags,
                ),
            )
        except (KeyError, ResearcherLlmError, TypeError, ValidationError, ValueError) as error:
            raise ResearcherLlmError from error


def load_researcher_context_input(path: Path) -> ResearcherContextInput:
    try:
        return ResearcherContextInput.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ResearcherLlmError, ValidationError, ValueError) as error:
        raise ResearcherLlmError from error


def load_private_canonical_researcher_context(path: Path) -> ResearcherContextInput:
    return _load_private_canonical_model(path, ResearcherContextInput)


def load_private_canonical_llm_response(path: Path) -> bytes:
    raw = _private_canonical_text(path, LlmHypothesisDraft)
    return raw.encode()


def _load_private_canonical_model[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
) -> ModelT:
    raw = _private_canonical_text(path, model_type)
    return model_type.model_validate_json(raw)


def _private_canonical_text[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
) -> str:
    try:
        raw = read_private_text(path.expanduser().absolute())
        model = model_type.model_validate_json(raw)
        canonical = json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if raw != canonical:
            raise ResearcherLlmError
        return raw
    except (
        InvalidPrivateImmutableFileError,
        OSError,
        ResearcherLlmError,
        TypeError,
        UnicodeError,
        ValidationError,
        ValueError,
    ) as error:
        raise ResearcherLlmError from error


def _prompt(context: ResearcherContext) -> str:
    payload = {
        "contract": {
            "counterfactual_baseline": "existing_approved_strategy",
            "economic_mechanism": "derive_only_from_cited_source_claims",
            "falsification_rule": "specific_measurable_thresholds",
            "maximum_free_parameters": _MAX_FREE_PARAMETERS,
            "only_raw_json": True,
            "output_json_schema": LlmHypothesisDraft.model_json_schema(),
            "strategy_entrypoint": {
                "factory": "create_strategy(context)",
                "method": "observe(bar, candidate)",
            },
            "strategy_source_contract": {
                "content": "complete_syntactically_valid_python_source_only",
                "factory": "define create_strategy(context) returning a stateful object with observe",
                "no_markdown_or_prose": True,
                "observe_inputs": {
                    "bar_keys": (
                        "symbol",
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "prior_close",
                        "average_daily_volume",
                        "spread_bps",
                        "catalyst",
                    ),
                    "candidate": "null or a dict with symbol,timestamp,price,gap_pct,change_pct,"
                    "relative_volume,cumulative_dollar_volume,spread_bps,catalyst",
                },
                "observe_return": {
                    "no_signal": None,
                    "signal_constraints": "echo bar symbol and timestamp; finite entry greater than stop",
                    "signal_exact_keys": ("symbol", "timestamp", "entry", "stop", "rationale"),
                },
            },
        },
        "existing_hypothesis_texts": context.existing_hypothesis_texts,
        "failure_digest": {
            "censored_reasons": context.failure_digest.censored_reasons,
            "failed_falsifications": context.failure_digest.failed_falsifications,
            "rejected_hypothesis_texts": context.failure_digest.rejected_hypothesis_texts,
            "reviewer_decisions": context.failure_digest.reviewer_decisions,
        },
        "lane_id": context.lane_id.value,
        "regime_context": context.regime_context,
        "sources": tuple(
            {
                "claim": source.claim,
                "limitations": source.limitations,
                "source_id": source.source_id,
                "source_kind": source.source_kind.value,
                "title": source.title,
            }
            for source in context.sources
        ),
    }
    if context.bounded_day_discovery_json is not None:
        try:
            bounded = json.loads(context.bounded_day_discovery_json)
        except (TypeError, ValueError):
            raise ResearcherLlmError from None
        if not isinstance(bounded, dict) or len(context.bounded_day_discovery_json.encode()) > 48 * 1024:
            raise ResearcherLlmError
        payload["day_discovery"] = bounded
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = (
    "FixtureLlmProposalClient",
    "HermesCliProposalClient",
    "LlmHypothesisDraft",
    "LlmProposalClient",
    "ResearcherContextInput",
    "ResearcherLlmError",
    "StructuredHypothesisGenerator",
    "load_private_canonical_llm_response",
    "load_private_canonical_researcher_context",
    "load_researcher_context_input",
)
