from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.day_agent_challenger_publisher import (
    DayAgentFutureShadowSession,
    DayAgentGeneratedCapsulePublisher,
)
from trading_agent.day_agent_loop_engineer import DayAgentLoopServices, ProposedAgentChange
from trading_agent.day_agent_version_models import AgentVersion
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.day_learning_report_models import DayDecisionStage
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import (
    GeneratedStrategyRuntimeIdentity,
    require_generated_strategy_runtime,
)
from trading_agent.models import BarInput
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, read_private_text
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.researcher_agent import ProposedHypothesis
from trading_agent.us_day_agent_service import (
    StoreBackedUsDayClosePayloadReader,
    UsDayCloseBindings,
    UsDayStrategyBinding,
)
from trading_agent.us_day_post_close_checkpoint import UsDayPostCloseCheckpointStore
from trading_agent.us_day_thesis_models import UsDayPlaybook
from trading_agent.us_day_thesis_store import UsDayThesisStore
from trading_agent.us_forward_shadow_artifacts import UsForwardShadowArtifactStore
from trading_agent.us_forward_shadow_services import UsForwardShadowServices


class _InvalidReviewedStrategyManifestError(ValueError):
    pass


class ProductionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: Path
    review_ledger: Path
    experiment_ledger: Path
    strategy_manifest: Path
    lane_registry: Path
    arm_database: Path
    arm_signing_key: Path
    execution_database: Path
    delivery_database: Path
    session_root: Path
    safety_arm_request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_artifact_root: Path
    forward_shadow_artifact_root: Path
    loop_task_root: Path
    loop_inputs: Path
    patch_model_response: Path


class ReviewedUsDayStrategyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_lane: StrategyLaneRef
    playbook: UsDayPlaybook
    reviewed: Literal[True]

    @model_validator(mode="after")
    def validate_lane(self) -> Self:
        if (
            self.strategy_lane.market_id is not MarketId.US_EQUITIES
            or self.strategy_lane.agent_family is not AgentFamily.DAY_TRADING
            or self.strategy_lane.strategy_id != self.capsule_id
            or self.playbook.playbook_id != self.capsule_id
        ):
            raise _InvalidReviewedStrategyManifestError("strategy_manifest_lineage_invalid")
        return self


class LoopInputBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: GeneratedStrategyRuntimeIdentity
    proposal_template: ProposedHypothesis
    replay_bars: tuple[BarInput, ...] = Field(min_length=1)
    future_sessions: tuple[DayAgentFutureShadowSession, ...] = Field(min_length=2)


class ProductionBindingError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class _BoundPatchAuthor:
    response: ProposedAgentChange

    def propose(self, stage: DayDecisionStage, champion: AgentVersion) -> ProposedAgentChange:
        del stage, champion
        return self.response


def load_production_manifest(path: Path) -> ProductionManifest:
    try:
        return ProductionManifest.model_validate_json(read_private_text(path))
    except (InvalidPrivateImmutableFileError, ValidationError, ValueError):
        raise ProductionBindingError("production_manifest_invalid") from None


def resolve_strategy_binding(
    manifest_path: Path,
    experiment_ledger_path: Path,
    champion: AgentVersion,
) -> UsDayStrategyBinding:
    try:
        manifest = ReviewedUsDayStrategyManifest.model_validate_json(read_private_text(manifest_path))
        stored = ExperimentLedgerStore(experiment_ledger_path).reader().day_strategy_capsule(manifest.capsule_id)
        if (
            champion.playbook_ids != (manifest.capsule_id,)
            or stored is None
            or stored.capsule.capsule_id != manifest.capsule_id
            or stored.capsule.market_id is not MarketId.US_EQUITIES
        ):
            raise ProductionBindingError("champion_strategy_lineage_invalid")
        return UsDayStrategyBinding(
            stored.capsule.capsule_id,
            manifest.strategy_lane,
            (manifest.playbook,),
        )
    except ProductionBindingError:
        raise
    except (InvalidPrivateImmutableFileError, OSError, RuntimeError, ValidationError, ValueError):
        raise ProductionBindingError("champion_strategy_lineage_invalid") from None


def build_close_bindings(
    manifest: ProductionManifest,
    outputs: Path,
    thesis_store: UsDayThesisStore,
    version_store: DayAgentVersionStore,
    session_date: dt.date,
) -> UsDayCloseBindings:
    try:
        inputs = LoopInputBundle.model_validate_json(read_private_text(manifest.loop_inputs))
        future_dates = tuple(item.session_date for item in inputs.future_sessions)
        if (
            not inputs.replay_bars
            or len(inputs.future_sessions) < 2
            or future_dates != tuple(sorted(set(future_dates)))
            or any(item.session_date <= session_date for item in inputs.future_sessions)
            or any(item.effective_at.astimezone(dt.UTC).date() != item.session_date for item in inputs.future_sessions)
        ):
            raise ProductionBindingError("production_close_bindings_invalid")
        runtime = require_generated_strategy_runtime(inputs.runtime)
        author = _BoundPatchAuthor(
            ProposedAgentChange.model_validate_json(read_private_text(manifest.patch_model_response))
        )
        services = UsForwardShadowServices(
            ledger=ExperimentLedgerStore(manifest.experiment_ledger),
            generated_artifacts=GeneratedStrategyArtifactStore(manifest.generated_artifact_root, runtime),
            shadow_artifacts=UsForwardShadowArtifactStore(manifest.forward_shadow_artifact_root),
            task_root=manifest.loop_task_root,
        )
        return UsDayCloseBindings(
            StoreBackedUsDayClosePayloadReader(outputs, thesis_store),
            DayAgentLoopServices(
                version_store,
                author,
                DayAgentGeneratedCapsulePublisher(
                    services,
                    inputs.proposal_template,
                    inputs.replay_bars,
                    inputs.future_sessions,
                ),
            ),
            UsDayPostCloseCheckpointStore(outputs / "us_day" / "post_close_checkpoints"),
        )
    except ProductionBindingError:
        raise
    except (InvalidPrivateImmutableFileError, OSError, RuntimeError, ValidationError, ValueError):
        raise ProductionBindingError("production_close_bindings_invalid") from None


__all__ = (
    "LoopInputBundle",
    "ProductionBindingError",
    "ProductionManifest",
    "ReviewedUsDayStrategyManifest",
    "build_close_bindings",
    "load_production_manifest",
    "resolve_strategy_binding",
)
