from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import assert_never

from trading_agent.day_agent_challenger_builders import (
    DayAgentFutureShadowSession,
    DerivedManifestRequest,
    DerivedProposalRequest,
    DerivedSourceRequest,
    DerivedVersionRequest,
    FuturePolicyRequest,
    build_derived_manifest,
    build_derived_proposal,
    build_derived_version,
    build_future_policy,
    render_derived_source,
)
from trading_agent.day_agent_version_models import AgentVersion, AgentVersionPatch, DayAgentVersionStoreError
from trading_agent.day_learning_policy import ExplorationPolicy
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_research_attempt_binding import DayResearchAttemptBinding
from trading_agent.day_strategy_capsule import (
    DayStrategyCapsuleRequest,
    GeneratedCapsuleVerification,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
    publish_day_strategy_capsule,
)
from trading_agent.day_strategy_capsule_models import CapsuleArtifactKind, CapsuleAuthorityCeiling, StrategyCapsule
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.models import BarInput
from trading_agent.private_immutable_file import read_private_text
from trading_agent.researcher_agent import ProposedHypothesis
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import AttemptStatus
from trading_agent.us_forward_shadow_services import UsForwardShadowServices


@dataclass(frozen=True, slots=True)
class DayAgentChallengerPublicationRequest:
    report: MarketCloseReport
    champion: AgentVersion
    patch: AgentVersionPatch


@dataclass(frozen=True, slots=True)
class PublishedDayAgentChallenger:
    capsule: StrategyCapsule
    policies: tuple[ExplorationPolicy, ...]


@dataclass(frozen=True, slots=True)
class DayAgentGeneratedCapsulePublisher:
    services: UsForwardShadowServices
    proposal_template: ProposedHypothesis
    replay_bars: tuple[BarInput, ...]
    future_sessions: tuple[DayAgentFutureShadowSession, ...]

    def publish(self, request: DayAgentChallengerPublicationRequest) -> PublishedDayAgentChallenger:
        report = request.report.payload
        if (
            len(request.champion.playbook_ids) != 1
            or not self.replay_bars
            or len(self.future_sessions) < 2
            or tuple(sorted(request.champion.playbook_ids)) != report.next_session.active_capsule_ids
            or any(item.session_date <= report.session_date for item in self.future_sessions)
            or any(item.effective_at.astimezone(dt.UTC).date() != item.session_date for item in self.future_sessions)
            or tuple(item.session_date for item in self.future_sessions)
            != tuple(sorted({item.session_date for item in self.future_sessions}))
        ):
            raise DayAgentVersionStoreError("challenger_publication_invalid")
        reader = self.services.ledger.reader()
        parent_stored = reader.day_strategy_capsule(request.champion.playbook_ids[0])
        if parent_stored is None:
            raise DayAgentVersionStoreError("challenger_parent_capsule_missing")
        parent_capsule = parent_stored.capsule
        parent_version_stored = reader.day_hypothesis_version(parent_capsule.hypothesis_version_id)
        attempts = reader.day_attempts_for_review(parent_capsule.market_id, parent_capsule.hypothesis_version_id)
        parent_attempt = next(
            (item for item in attempts if item.binding.binding_id == parent_capsule.attempt_binding_id),
            None,
        )
        if parent_version_stored is None or parent_attempt is None:
            raise DayAgentVersionStoreError("challenger_parent_lineage_invalid")
        match parent_capsule:
            case StrategyCapsule(
                artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
                authority_ceiling=CapsuleAuthorityCeiling.RESEARCH_ONLY,
                generated_artifact_id=str() as generated_artifact_id,
            ):
                pass
            case StrategyCapsule():
                raise DayAgentVersionStoreError("challenger_parent_lineage_invalid")
            case unreachable:
                assert_never(unreachable)
        manifests = tuple(
            item
            for item in reader.strategy_research_preregistrations()
            if item.hypothesis.hypothesis_id == parent_attempt.attempt.hypothesis_id
        )
        if len(manifests) != 1:
            raise DayAgentVersionStoreError("challenger_parent_lineage_invalid")
        patch_sha256 = hashlib.sha256(canonical_experiment_ledger_json(request.patch).encode()).hexdigest()
        binding_sha256 = hashlib.sha256(
            f"{request.champion.version_id}:{patch_sha256}".encode()
        ).hexdigest()
        parent_artifact = self.services.generated_artifacts.load(generated_artifact_id)
        parent_source = read_private_text(
            self.services.generated_artifacts.root / parent_artifact.artifact_id / "strategy.py"
        )
        source = render_derived_source(
            DerivedSourceRequest(
                request.champion,
                parent_capsule,
                request.patch,
                binding_sha256,
                parent_source,
            )
        )
        source_sha256 = hashlib.sha256(source.encode()).hexdigest()
        hypothesis_id = f"day-loop-{binding_sha256[:32]}"
        proposed = build_derived_proposal(
            DerivedProposalRequest(
                self.proposal_template,
                hypothesis_id,
                source,
                binding_sha256,
                source_sha256,
                report.finalized_at,
            )
        )
        published = self.services.generated_artifacts.publish(proposed)
        manifest = build_derived_manifest(
            DerivedManifestRequest(
                manifests[0],
                hypothesis_id,
                source_sha256,
                binding_sha256,
                report.finalized_at,
            )
        )
        version = build_derived_version(
            DerivedVersionRequest(
                parent_version_stored.version,
                source_sha256,
                binding_sha256,
                report.finalized_at,
            )
        )
        if any(item.effective_at <= version.first_shadow_eligible_at for item in self.future_sessions):
            raise DayAgentVersionStoreError("challenger_future_eligibility_invalid")
        attempt = ResearchAttempt(
            attempt_id=f"day-loop-attempt-{binding_sha256}",
            hypothesis_id=hypothesis_id,
            branch_index=0,
            input_hashes=tuple(sorted((request.champion.version_id, patch_sha256))),
            code_sha256=source_sha256,
            data_manifest_sha256=version.data_manifest_sha256,
            started_at=report.finalized_at,
            finished_at=report.finalized_at + dt.timedelta(seconds=30),
            status=AttemptStatus.SUCCEEDED,
            artifact_refs=(f"artifact://safe/{source_sha256}",),
            error_class=None,
            max_cpu_seconds=version.search_budget.max_cpu_seconds,
        )
        binding_payload = {
            "binding_id": "",
            "attempt_id": attempt.attempt_id,
            "market_id": parent_capsule.market_id,
            "hypothesis_version_id": version.hypothesis_version_id,
            "artifact_ref": attempt.artifact_refs[0],
            "multiple_testing_family": version.multiple_testing_family,
            "multiple_testing_budget": version.search_budget.max_attempts,
            "search_budget_debit": 1,
            "bound_at": report.finalized_at + dt.timedelta(minutes=1, seconds=30),
        }
        binding = DayResearchAttemptBinding.model_validate(
            binding_payload
            | {"binding_id": DayResearchAttemptBinding.canonical_id_for(binding_payload)}
        )
        with self.services.ledger.writer() as writer:
            _ = writer.register_strategy_research(manifest)
            _ = writer.register_day_hypothesis_version(version)
            _ = writer.append_strategy_research_attempt(attempt)
            _ = writer.register_day_research_attempt_binding(binding)
        limits = parent_capsule.resource_limits
        sandbox = GeneratedStrategySandbox(
            self.services.generated_artifacts.runtime,
            self.services.task_root / f"challenger-preflight-{binding_sha256}",
            limits.to_generated_limits(),
        )
        capsule, _ = publish_day_strategy_capsule(
            self.services.ledger,
            DayStrategyCapsuleRequest(
                hypothesis_version_id=version.hypothesis_version_id,
                attempt_binding_id=binding.binding_id,
                market_id=parent_capsule.market_id,
                artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
                artifact_ref=binding.artifact_ref,
                artifact_sha256=source_sha256,
                generated_artifact_id=published.artifact.artifact_id,
                evaluation_cadence=version.evaluation_cadence,
                evidence_schema=parent_capsule.evidence_schema,
                entry_rule=version.entry_rule,
                exit_rule=version.exit_rule,
                stop_rule=version.stop_rule,
                target_rule=parent_capsule.target_rule,
                cost_model=version.cost_model,
                slippage_model_id=parent_capsule.slippage_model_id,
                resource_limits=limits,
                risk_policy_ref=parent_capsule.risk_policy_ref,
                protocol_version=1,
                protocol_sha256=generated_protocol_bundle_sha256(),
                evaluator_sha256=generated_evaluator_bundle_sha256(),
                published_at=report.finalized_at + dt.timedelta(minutes=1, seconds=31),
                authority_ceiling=CapsuleAuthorityCeiling.RESEARCH_ONLY,
                generated_verification=GeneratedCapsuleVerification(
                    self.services.generated_artifacts,
                    sandbox,
                    self.replay_bars,
                ),
            ),
        )
        policies = tuple(
            build_future_policy(FuturePolicyRequest(request.report, request.champion, capsule, item))
            for item in self.future_sessions
        )
        with self.services.ledger.writer() as writer:
            for policy in policies:
                _ = writer.record_day_exploration_policy(policy)
        return PublishedDayAgentChallenger(capsule, policies)


__all__ = (
    "DayAgentChallengerPublicationRequest",
    "DayAgentFutureShadowSession",
    "DayAgentGeneratedCapsulePublisher",
    "PublishedDayAgentChallenger",
)
