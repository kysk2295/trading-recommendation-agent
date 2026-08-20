from __future__ import annotations

import datetime as dt
import hashlib
import math
from statistics import fmean
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_agent_version_models import (
    AgentPromotionDecision,
    AgentPromotionRecommendation,
    DayAgentVersionStoreError,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


class DayShadowSnapshotScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_date: dt.date
    situation_snapshot_id: str = Field(min_length=8, max_length=160)
    theme_timing: float = Field(ge=0.0, le=1.0)
    leader_rank: float = Field(ge=0.0, le=1.0)
    recommendation_calibration: float = Field(ge=0.0, le=1.0)
    mfe: float
    mae: float
    cost_adjusted_modeled_result: float
    no_trade_quality: float = Field(ge=0.0, le=1.0)
    evidence_fidelity: float = Field(ge=0.0, le=1.0)
    forward_shadow_artifact_ids: tuple[str, ...] = Field(min_length=1)
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_score(self) -> Self:
        values = (self.mfe, self.mae, self.cost_adjusted_modeled_result)
        if (
            not all(math.isfinite(item) for item in values)
            or self.mae > 0.0
            or self.forward_shadow_artifact_ids
            != tuple(sorted(set(self.forward_shadow_artifact_ids)))
        ):
            raise DayAgentVersionStoreError("future_shadow_score_invalid")
        return self


class DayShadowComparisonInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    champion: tuple[DayShadowSnapshotScore, ...] = Field(min_length=1)
    challenger: tuple[DayShadowSnapshotScore, ...] = Field(min_length=1)
    minimum_sessions: int = Field(ge=2, le=20)
    evaluated_at: AwareDatetime


def evaluate_day_agent_challenger(
    comparison: DayShadowComparisonInput,
    store: DayAgentVersionStore,
) -> AgentPromotionRecommendation:
    checked = DayShadowComparisonInput.model_validate(comparison.model_dump(mode="python"))
    champion_ids = {item.version_id for item in checked.champion}
    challenger_ids = {item.version_id for item in checked.challenger}
    if len(champion_ids) != 1 or len(challenger_ids) != 1:
        raise DayAgentVersionStoreError("future_shadow_version_invalid")
    champion_id = next(iter(champion_ids))
    challenger_id = next(iter(challenger_ids))
    champion_by_snapshot = {
        (item.session_date, item.situation_snapshot_id): item for item in checked.champion
    }
    challenger_by_snapshot = {
        (item.session_date, item.situation_snapshot_id): item for item in checked.challenger
    }
    sessions = tuple(sorted({item.session_date for item in checked.challenger}))
    if (
        champion_by_snapshot.keys() != challenger_by_snapshot.keys()
        or len(sessions) < checked.minimum_sessions
    ):
        raise DayAgentVersionStoreError("future_shadow_pairing_invalid")
    stored_champion = store.reader().champion()
    challenger = store.reader().challenger(challenger_id)
    if champion_id != (None if stored_champion is None else stored_champion.version_id) or challenger is None:
        raise DayAgentVersionStoreError("future_shadow_version_invalid")
    if any(session <= challenger.created_session_date for session in sessions):
        raise DayAgentVersionStoreError("future_shadow_pairing_invalid")
    ordered_keys = tuple(sorted(champion_by_snapshot))
    champion_score = fmean(_combined_score(champion_by_snapshot[key]) for key in ordered_keys)
    challenger_score = fmean(_combined_score(challenger_by_snapshot[key]) for key in ordered_keys)
    decision, reasons = _promotion_decision(champion_score, challenger_score)
    payload = {
        "champion_version_id": champion_id,
        "challenger_version_id": challenger_id,
        "decision": decision,
        "evaluated_session_dates": sessions,
        "paired_snapshot_ids": tuple(key[1] for key in ordered_keys),
        "champion_score": champion_score,
        "challenger_score": challenger_score,
        "reason_codes": reasons,
        "evaluated_at": checked.evaluated_at,
        "deployment_authority": False,
    }
    unsigned = AgentPromotionRecommendation(recommendation_id="0" * 64, **payload)
    recommendation_id = hashlib.sha256(canonical_experiment_ledger_json(unsigned).encode()).hexdigest()
    recommendation = unsigned.model_copy(update={"recommendation_id": recommendation_id})
    with store.writer() as writer:
        _ = writer.record_recommendation(recommendation)
    return recommendation


def _combined_score(score: DayShadowSnapshotScore) -> float:
    return fmean(
        (
            score.theme_timing,
            score.leader_rank,
            score.recommendation_calibration,
            score.mfe,
            score.mae,
            score.cost_adjusted_modeled_result,
            score.no_trade_quality,
            score.evidence_fidelity,
        )
    )


def _promotion_decision(
    champion_score: float,
    challenger_score: float,
) -> tuple[AgentPromotionDecision, tuple[str, ...]]:
    margin = challenger_score - champion_score
    if margin >= 0.05:
        return AgentPromotionDecision.PROMOTE, ("challenger_margin_met",)
    if margin <= -0.05:
        return AgentPromotionDecision.ROLLBACK, ("challenger_regressed",)
    return AgentPromotionDecision.REJECT, ("challenger_margin_not_met",)


__all__ = (
    "DayShadowComparisonInput",
    "DayShadowSnapshotScore",
    "evaluate_day_agent_challenger",
)
