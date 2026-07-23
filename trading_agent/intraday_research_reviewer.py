from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_research_artifacts import IntradayExperimentArtifact
from trading_agent.intraday_research_loop_models import (
    DEMOTION_MIN_SESSIONS,
    DEMOTION_MIN_TRADES,
    PROMOTION_MIN_SESSIONS,
    PROMOTION_MIN_TRADES,
    IntradayReviewerDecision,
    IntradayReviewEvidence,
    intraday_reviewer_decision,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)

INTRADAY_REVIEWER_VERSION: Final = "intraday_historical_reviewer_v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidIntradayResearchReviewError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday independent Reviewer evidence is invalid"


class IntradayReviewPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trial_id: str
    strategy_version: str
    experiment_artifact_id: str
    reviewer_version: Literal["intraday_historical_reviewer_v1"]
    reviewed_at: dt.datetime
    evidence: IntradayReviewEvidence
    decision: IntradayReviewerDecision
    reason_codes: tuple[str, ...]
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if (
            not self.trial_id
            or not self.strategy_version
            or _HEX64.fullmatch(self.experiment_artifact_id) is None
            or not _aware(self.reviewed_at)
            or not self.reason_codes
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
        ):
            raise InvalidIntradayResearchReviewError
        return self


class IntradayReviewArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: IntradayReviewPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.artifact_id != expected:
            raise InvalidIntradayResearchReviewError
        return self


@dataclass(frozen=True, slots=True)
class IntradayReviewRequest:
    ledger: ExperimentLedgerReader
    experiment: IntradayExperimentArtifact
    review_root: Path
    reviewed_at: dt.datetime


def review_intraday_experiment(request: IntradayReviewRequest) -> tuple[IntradayReviewArtifact, bool]:
    review_payload = evaluate_intraday_experiment(
        request.ledger,
        request.experiment,
        request.reviewed_at,
    )
    identity = hashlib.sha256(
        canonical_experiment_ledger_json(review_payload).encode()
    ).hexdigest()
    artifact = IntradayReviewArtifact(
        artifact_id=identity,
        payload=review_payload,
    )
    try:
        created = publish_private_immutable_text(
            request.review_root / f"intraday_research_review_{artifact.artifact_id}.json",
            canonical_experiment_ledger_json(artifact) + "\n",
        )
        return artifact, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidIntradayResearchReviewError from None


def evaluate_intraday_experiment(
    ledger: ExperimentLedgerReader,
    experiment: IntradayExperimentArtifact,
    reviewed_at: dt.datetime,
) -> IntradayReviewPayload:
    payload = experiment.payload
    matching = tuple(
        row
        for row in ledger.trials()
        if row.registration.trial_id == payload.trial_id
    )
    if len(matching) != 1:
        raise InvalidIntradayResearchReviewError
    trial = matching[0].registration
    events = ledger.trial_events(payload.trial_id)
    if (
        trial.strategy_version != payload.strategy_version
        or trial.evaluator_version != payload.evaluator_version
        or trial.data_version != payload.data_version
        or len(events) != 2
        or events[-1].event.event_kind is not TrialEventKind.COMPLETED
        or events[-1].event.artifact_sha256s != (experiment.artifact_id,)
    ):
        raise InvalidIntradayResearchReviewError
    result = payload.result
    evidence = IntradayReviewEvidence(
        observed_sessions=result.observed_sessions,
        trade_count=result.trade_count,
        average_return=result.average_return,
        profit_factor=result.profit_factor,
        mean_ci_low=result.mean_ci_low,
        mean_ci_high=result.mean_ci_high,
    )
    decision = intraday_reviewer_decision(evidence)
    review_payload = IntradayReviewPayload(
        trial_id=payload.trial_id,
        strategy_version=payload.strategy_version,
        experiment_artifact_id=experiment.artifact_id,
        reviewer_version=INTRADAY_REVIEWER_VERSION,
        reviewed_at=reviewed_at,
        evidence=evidence,
        decision=decision,
        reason_codes=_reason_codes(decision, evidence),
    )
    return review_payload


def _reason_codes(
    decision: IntradayReviewerDecision,
    evidence: IntradayReviewEvidence,
) -> tuple[str, ...]:
    match decision:
        case IntradayReviewerDecision.PROMOTE:
            return ("cost_adjusted_oos_promotion_gate_passed",)
        case IntradayReviewerDecision.DEMOTE:
            return ("cost_adjusted_oos_clear_failure",)
        case IntradayReviewerDecision.HOLD:
            reasons: list[str] = []
            if evidence.observed_sessions < DEMOTION_MIN_SESSIONS:
                reasons.append(f"minimum_demotion_sessions:{evidence.observed_sessions}/{DEMOTION_MIN_SESSIONS}")
            if evidence.trade_count < DEMOTION_MIN_TRADES:
                reasons.append(f"minimum_demotion_trades:{evidence.trade_count}/{DEMOTION_MIN_TRADES}")
            if evidence.observed_sessions < PROMOTION_MIN_SESSIONS:
                reasons.append(f"minimum_promotion_sessions:{evidence.observed_sessions}/{PROMOTION_MIN_SESSIONS}")
            if evidence.trade_count < PROMOTION_MIN_TRADES:
                reasons.append(f"minimum_promotion_trades:{evidence.trade_count}/{PROMOTION_MIN_TRADES}")
            if not reasons:
                reasons.append("cost_adjusted_oos_inconclusive")
            return tuple(sorted(reasons))
        case unreachable:
            assert_never(unreachable)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "INTRADAY_REVIEWER_VERSION",
    "IntradayReviewArtifact",
    "IntradayReviewRequest",
    "InvalidIntradayResearchReviewError",
    "evaluate_intraday_experiment",
    "review_intraday_experiment",
)
