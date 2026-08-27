from __future__ import annotations

from tests.test_kr_autonomous_market_service import NOW
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration
from trading_agent.kr_autonomous_trade_models import KrAutonomousSetupKind, KrAutonomousTradeThesis, thesis_id
from trading_agent.kr_social_signal_models import KrSocialSignal


def thesis(signal: KrSocialSignal, market: KrAutonomousMarketCorroboration) -> KrAutonomousTradeThesis:
    draft = KrAutonomousTradeThesis.model_construct(
        thesis_id="",
        task_id=signal.task_id,
        symbol=market.symbol,
        theme="Semiconductor demand",
        hypothesis="Current independent evidence supports a bounded continuation setup.",
        counterevidence=("The observed response may lose its completed-bar low.",),
        setup_kind=KrAutonomousSetupKind.MOMENTUM_RECLAIM,
        social_signal_id=signal.signal_id,
        market_corroboration_id=market.corroboration_id,
        evidence_refs=tuple(sorted({*signal.evidence_ids, *market.evidence_ids})),
        submitted_at=NOW,
    )
    return KrAutonomousTradeThesis.model_validate(
        draft.model_copy(update={"thesis_id": thesis_id(draft)}).model_dump(mode="python")
    )
