from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    ResearchHypothesisCard,
)
from trading_agent.experiment_scope_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    read_private_text,
)
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    ProposedHypothesis,
    ResearcherContext,
)
from trading_agent.researcher_llm_contracts import (
    LlmHypothesisDraft,
    LlmProposalClient,
    ResearcherContextInput,
    ResearcherLlmError,
    ResearcherLlmPlan,
    ResearcherRawCompletion,
)
from trading_agent.researcher_llm_prompt import _prompt
from trading_agent.researcher_receipt_store import ResearcherReceiptStore


@dataclass(frozen=True, slots=True)
class StructuredHypothesisGenerator:
    client: LlmProposalClient
    receipts: ResearcherReceiptStore
    clock: Callable[[], dt.datetime]

    def propose(self, context: ResearcherContext) -> ProposedHypothesis:
        plan = self.plan(context)
        completion = self.invoke_raw(plan)
        return self.parse_raw(plan, completion, context)

    def plan(self, context: ResearcherContext) -> ResearcherLlmPlan:
        prompt = _prompt(context)
        prompt_bytes = prompt.encode()
        creator = "structured_hypothesis_generator_v1"
        return ResearcherLlmPlan(
            prompt=prompt,
            prompt_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
            prompt_bytes_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
            model_id=self.client.model_id,
            seed=self.client.seed,
            temperature=self.client.temperature,
            protocol_sha256=_protocol_sha256(),
            creator=creator,
            creator_sha256=hashlib.sha256(creator.encode()).hexdigest(),
            planned_at=self.clock(),
        )

    def invoke_raw(self, plan: ResearcherLlmPlan) -> ResearcherRawCompletion:
        invocation_started_at = self.clock()
        response = self.client.complete(plan.prompt)
        received_at = self.clock()
        return ResearcherRawCompletion(
            response=response,
            response_sha256=hashlib.sha256(response).hexdigest(),
            response_length=len(response),
            invocation_started_at=invocation_started_at,
            received_at=received_at,
        )

    def parse_raw(
        self,
        plan: ResearcherLlmPlan,
        completion: ResearcherRawCompletion,
        context: ResearcherContext,
    ) -> ProposedHypothesis:
        receipt = self.receipts.record_call(
            model_id=plan.model_id,
            prompt=plan.prompt,
            response=completion.response,
            seed=plan.seed,
            temperature=plan.temperature,
            called_at=completion.invocation_started_at,
        )
        try:
            _validate_completion(plan, completion)
            draft = LlmHypothesisDraft.model_validate_json(completion.response)
            sources_by_id = {source.source_id: source for source in context.sources}
            cited_sources = tuple(sources_by_id[source_id] for source_id in draft.cited_source_ids)
            called_at = completion.invocation_started_at
            if (
                called_at.tzinfo is None
                or called_at.utcoffset() is None
                or any(source.ledger_recorded_at > called_at for source in cited_sources)
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
                    research_source_keys=tuple(sorted(str(research_source_key(source)) for source in cited_sources)),
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


def _protocol_sha256() -> str:
    return hashlib.sha256(
        json.dumps(
            LlmHypothesisDraft.model_json_schema(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _validate_completion(plan: ResearcherLlmPlan, completion: ResearcherRawCompletion) -> None:
    if (
        plan.prompt_sha256 != hashlib.sha256(plan.prompt.encode()).hexdigest()
        or plan.prompt_bytes_sha256 != hashlib.sha256(plan.prompt.encode()).hexdigest()
        or plan.creator_sha256 != hashlib.sha256(plan.creator.encode()).hexdigest()
        or plan.protocol_sha256 != _protocol_sha256()
        or completion.response_sha256 != hashlib.sha256(completion.response).hexdigest()
        or completion.response_length != len(completion.response)
        or plan.planned_at > completion.invocation_started_at
        or completion.invocation_started_at > completion.received_at
    ):
        raise ResearcherLlmError


def load_researcher_context_input(path: Path) -> ResearcherContextInput:
    try:
        return ResearcherContextInput.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ResearcherLlmError, ValidationError, ValueError) as error:
        raise ResearcherLlmError from error


def load_private_canonical_researcher_context(path: Path) -> ResearcherContextInput:
    return _load_private_canonical_model(path, ResearcherContextInput)


def load_private_canonical_llm_response(path: Path) -> bytes:
    return _private_canonical_text(path, LlmHypothesisDraft).encode()


def _load_private_canonical_model[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
) -> ModelT:
    return model_type.model_validate_json(_private_canonical_text(path, model_type))


def _private_canonical_text[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> str:
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


__all__ = (
    "StructuredHypothesisGenerator",
    "load_private_canonical_llm_response",
    "load_private_canonical_researcher_context",
    "load_researcher_context_input",
)
