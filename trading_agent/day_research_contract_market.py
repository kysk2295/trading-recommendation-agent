from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from trading_agent.day_hypothesis_models import (
    CostModelDeclaration,
    FreeParameter,
    HypothesisFamily,
    HypothesisVersion,
    SearchBudget,
    TargetHorizon,
)
from trading_agent.day_research_attempt_binding import DayResearchAttemptBinding
from trading_agent.day_research_contract_foundation import (
    MULTIPLE_TESTING_FAMILY,
    NOW,
    SHA_CODE,
    SHA_DATA,
    SHA_EVALUATOR,
    SHA_INPUT,
    SHA_MODEL,
)
from trading_agent.day_strategy_capsule import DayStrategyCapsuleRequest
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsuleAuthorityCeiling,
    CapsuleResourceLimits,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import AttemptStatus, ExpectedDirection


@dataclass(frozen=True, slots=True)
class SyntheticMarketContract:
    version: HypothesisVersion
    attempt: ResearchAttempt
    binding: DayResearchAttemptBinding
    capsule_request: DayStrategyCapsuleRequest


def build_synthetic_market_contract(
    family: HypothesisFamily,
    market_id: MarketId,
    branch_index: int,
) -> SyntheticMarketContract:
    version = _version(family, market_id)
    attempt = _attempt(market_id, branch_index)
    binding = _binding(version, attempt)
    return SyntheticMarketContract(
        version=version,
        attempt=attempt,
        binding=binding,
        capsule_request=_capsule_request(version, binding),
    )


def _version(family: HypothesisFamily, market_id: MarketId) -> HypothesisVersion:
    created_at = NOW
    payload = {
        "hypothesis_version_id": "",
        "family_id": family.family_id,
        "parent_version_id": None,
        "market_id": market_id,
        "universe_snapshot_id": f"synthetic-{market_id.value}-universe-v1",
        "universe_snapshot_at": created_at - dt.timedelta(minutes=2),
        "source_refs": ("fixture:synthetic-contract-only",),
        "methodology_tags": ("contract_smoke", "cross_sectional"),
        "primary_evaluation_owner": "day_research",
        "evaluation_cadence": "each_completed_bar",
        "predictor": "synthetic_completed_bar_score",
        "sampling_timestamp": created_at - dt.timedelta(minutes=1),
        "target": "synthetic_next_completed_bar_return",
        "target_horizon": TargetHorizon(duration=dt.timedelta(minutes=5)),
        "expected_direction": ExpectedDirection.POSITIVE,
        "entry_rule": "enter_next_completed_bar",
        "exit_rule": "exit_at_target_horizon",
        "stop_rule": "stop_first_on_same_bar_collision",
        "invalidation_rule": "invalidate_when_market_partition_is_missing",
        "threshold": Decimal("1"),
        "cost_model": CostModelDeclaration(
            model_id=f"synthetic_{market_id.value}_cost_v1",
            commission_bps=Decimal("1"),
            slippage_bps=Decimal("2"),
        ),
        "free_parameters": (FreeParameter(name="synthetic_threshold", values=(Decimal("1"), Decimal("2"))),),
        "search_budget": SearchBudget(
            max_parameter_combinations=2,
            max_attempts=2,
            max_cpu_seconds=60,
        ),
        "multiple_testing_family": MULTIPLE_TESTING_FAMILY,
        "model_sha256": SHA_MODEL,
        "prompt_sha256": SHA_INPUT,
        "code_sha256": SHA_CODE,
        "data_manifest_sha256": SHA_DATA,
        "protocol_sha256": SHA_INPUT,
        "created_at": created_at,
        "registration_completed_bar_at": created_at + dt.timedelta(minutes=1),
        "first_shadow_eligible_at": created_at + dt.timedelta(minutes=2),
        "trading_authority": False,
        "profitability_claim": False,
    }
    return HypothesisVersion.model_validate(
        payload | {"hypothesis_version_id": HypothesisVersion.canonical_id_for(payload)}
    )


def _attempt(market_id: MarketId, branch_index: int) -> ResearchAttempt:
    started_at = NOW + dt.timedelta(minutes=3 + branch_index * 2)
    return ResearchAttempt(
        attempt_id=f"synthetic-contract-{market_id.value}-attempt-v1",
        hypothesis_id="synthetic-day-contract-hypothesis-v1",
        branch_index=branch_index,
        input_hashes=(SHA_INPUT,),
        code_sha256=SHA_CODE,
        data_manifest_sha256=SHA_DATA,
        started_at=started_at,
        finished_at=started_at + dt.timedelta(minutes=1),
        status=AttemptStatus.SUCCEEDED,
        artifact_refs=(f"artifact://safe/{SHA_CODE}",),
        error_class=None,
        max_cpu_seconds=60,
    )


def _binding(
    version: HypothesisVersion,
    attempt: ResearchAttempt,
) -> DayResearchAttemptBinding:
    if attempt.finished_at is None:
        raise InvalidSyntheticMarketContractError("synthetic_attempt_not_terminal")
    payload = {
        "binding_id": "",
        "attempt_id": attempt.attempt_id,
        "market_id": version.market_id,
        "hypothesis_version_id": version.hypothesis_version_id,
        "artifact_ref": f"artifact://safe/{SHA_CODE}",
        "multiple_testing_family": version.multiple_testing_family,
        "multiple_testing_budget": version.search_budget.max_attempts,
        "search_budget_debit": 1,
        "bound_at": attempt.finished_at + dt.timedelta(minutes=1),
    }
    return DayResearchAttemptBinding.model_validate(
        payload | {"binding_id": DayResearchAttemptBinding.canonical_id_for(payload)}
    )


class InvalidSyntheticMarketContractError(ValueError):
    pass


def _capsule_request(
    version: HypothesisVersion,
    binding: DayResearchAttemptBinding,
) -> DayStrategyCapsuleRequest:
    return DayStrategyCapsuleRequest(
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        market_id=version.market_id,
        artifact_kind=CapsuleArtifactKind.BUILTIN,
        artifact_ref=binding.artifact_ref,
        artifact_sha256=version.code_sha256,
        generated_artifact_id=None,
        evaluation_cadence=version.evaluation_cadence,
        evidence_schema=("synthetic_completed_bar_v1",),
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        target_rule="host_projects_synthetic_target",
        cost_model=version.cost_model,
        slippage_model_id="synthetic-bounded-slippage-v1",
        resource_limits=CapsuleResourceLimits(),
        risk_policy_ref="risk-policy://day-research/contract-smoke-v1",
        protocol_version=1,
        protocol_sha256=version.protocol_sha256,
        evaluator_sha256=SHA_EVALUATOR,
        published_at=binding.bound_at + dt.timedelta(minutes=1),
        authority_ceiling=CapsuleAuthorityCeiling.RESEARCH_ONLY,
    )


__all__ = (
    "InvalidSyntheticMarketContractError",
    "SyntheticMarketContract",
    "build_synthetic_market_contract",
)
