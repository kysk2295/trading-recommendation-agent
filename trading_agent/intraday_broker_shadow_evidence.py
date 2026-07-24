from __future__ import annotations

from trading_agent.intraday_broker_shadow_models import (
    BROKER_SHADOW_EVIDENCE_VERSION,
    BrokerShadowEvidence,
    BrokerShadowEvidenceRequest,
)
from trading_agent.intraday_broker_shadow_pairing import (
    pair_broker_shadow_trades,
)
from trading_agent.intraday_broker_shadow_statistics import (
    assess_broker_shadow_pairs,
)


def build_broker_shadow_evidence(
    request: BrokerShadowEvidenceRequest,
) -> BrokerShadowEvidence:
    pairing = pair_broker_shadow_trades(request)
    paired_trade_count = len(pairing.pairs)
    paired_session_count = len({pair.session_date for pair in pairing.pairs})
    assessment = assess_broker_shadow_pairs(
        pairing.pairs,
        pairing.unpaired_broker_intent_count,
    )
    return BrokerShadowEvidence(
        evidence_version=BROKER_SHADOW_EVIDENCE_VERSION,
        strategy_version=request.strategy_version,
        execution_snapshot_sha256=request.execution_snapshot_sha256,
        shadow_source_sha256=request.shadow_source_sha256,
        reviewed_at=request.reviewed_at,
        status=assessment.status,
        pairs=pairing.pairs,
        paired_trade_count=paired_trade_count,
        paired_session_count=paired_session_count,
        unpaired_broker_intent_count=pairing.unpaired_broker_intent_count,
        broker_metrics=assessment.broker_metrics,
        shadow_metrics=assessment.shadow_metrics,
        blockers=assessment.blockers,
    )


__all__ = ("build_broker_shadow_evidence",)
