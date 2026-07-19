from __future__ import annotations

import json
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.alpaca_sip_dynamic_feature_bundle import AlpacaSipDynamicFeatureBundle
from trading_agent.alpaca_sip_dynamic_quote_actionability import (
    AlpacaSipDynamicQuoteActionabilityDecision,
    alpaca_sip_quote_actionability_artifacts_match,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability_evidence import UsQuotePolicyEvidence
from trading_agent.us_quote_actionability_models import QuoteActionabilityAssessment


class AlpacaSipQuoteActionabilityArtifactError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP quote actionability artifact is invalid"


class AlpacaSipQuoteActionabilityArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    base_publication: TradeSignalPublication
    bundle: AlpacaSipDynamicFeatureBundle
    policy_evidence: UsQuotePolicyEvidence | None
    assessment: QuoteActionabilityAssessment
    derived_publication: TradeSignalPublication | None

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        decision = AlpacaSipDynamicQuoteActionabilityDecision(
            self.bundle,
            self.policy_evidence,
            self.assessment,
            self.derived_publication,
        )
        if self.artifact_id != self.assessment.assessment_id or not alpaca_sip_quote_actionability_artifacts_match(
            self.base_publication,
            decision,
        ):
            raise AlpacaSipQuoteActionabilityArtifactError
        return self


def actionability_artifact(
    base: TradeSignalPublication,
    decision: AlpacaSipDynamicQuoteActionabilityDecision,
) -> AlpacaSipQuoteActionabilityArtifact:
    return AlpacaSipQuoteActionabilityArtifact(
        artifact_id=decision.assessment.assessment_id,
        base_publication=base,
        bundle=decision.bundle,
        policy_evidence=decision.policy_evidence,
        assessment=decision.assessment,
        derived_publication=decision.derived_publication,
    )


def actionability_artifact_bytes(
    artifact: AlpacaSipQuoteActionabilityArtifact,
) -> bytes:
    payload = artifact.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def actionability_artifact_from_bytes(
    payload: bytes,
) -> AlpacaSipQuoteActionabilityArtifact:
    try:
        artifact = AlpacaSipQuoteActionabilityArtifact.model_validate_json(payload)
        if actionability_artifact_bytes(artifact) != payload:
            raise AlpacaSipQuoteActionabilityArtifactError
        return artifact
    except (UnicodeError, ValidationError, ValueError):
        raise AlpacaSipQuoteActionabilityArtifactError from None


__all__ = (
    "AlpacaSipQuoteActionabilityArtifact",
    "AlpacaSipQuoteActionabilityArtifactError",
    "actionability_artifact",
    "actionability_artifact_bytes",
    "actionability_artifact_from_bytes",
)
