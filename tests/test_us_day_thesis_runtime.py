from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import QuoteValidation, SignalActionability
from trading_agent.us_day_thesis_models import (
    DayTradeDecision,
    UsDayChampion,
    UsDayCurrentMarket,
    UsDayPlaybook,
    situation_id_for,
)
from trading_agent.us_day_thesis_runtime import InvalidUsDayThesisError, reason_trade_thesis


def test_reasoner_emits_evidence_bound_human_trader_thesis() -> None:
    situation = _project(_inputs())
    theme = situation.themes[0]
    leader = theme.leaders[0]
    market = _market(
        leader.symbol, next(ref for ref in leader.evidence_refs if ref.namespace == "research/current_bar")
    )
    response = {
        "decision": "recommend",
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_name": "semiconductor_infrastructure",
        "symbol": leader.symbol,
        "entry_price": "100.10",
        "stop_price": "99.50",
        "targets": ({"label": "target_1", "price": "101.00"}, {"label": "target_2", "price": "102.00"}),
        "invalidation_rule": "현재 완료 봉 저가 이탈 시 논리가 무효화된다.",
        "confidence_bps": 7200,
        "observed_at": EVALUATED_AT,
        "valid_until": EVALUATED_AT + dt.timedelta(seconds=20),
        "reason_code": None,
        "theme_rationale": _rationale(
            "반도체 인프라 테마가 현재 세션 촉매로 활성화됐다.", theme.claims[0].evidence_refs
        ),
        "catalyst_rationale": _rationale(
            "검증된 현재 세션 뉴스가 종목군을 연결한다.", theme.catalysts[0].evidence_refs
        ),
        "leader_rationale": _rationale("대장주 순위 1위와 상대강도가 확인됐다.", leader.evidence_refs),
        "flow_rationale": _rationale(
            "관측 수급과 명시된 프록시 추론을 함께 사용했다.", leader.flow.evidence_refs, "inferred"
        ),
    }

    result = reason_trade_thesis(response, _champion(), situation, (market,))

    assert result.decision is DayTradeDecision.RECOMMEND
    assert result.theme_name == "semiconductor_infrastructure"
    assert result.symbol == "NVDA"
    assert result.stop_price < result.entry_price < result.targets[0].price
    assert result.agent_version_id == _champion().version_id
    assert set(result.evidence_refs) <= set(situation.evidence_refs)
    assert result.signal is not None
    assert result.signal.signal_id == result.thesis_id
    assert result.signal.strategy_lane.agent_family is AgentFamily.DAY_TRADING
    assert result.signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
    assert result.signal.quote_validation == market.quote


@pytest.mark.parametrize("decision", ("watch", "no_trade", "insufficient_evidence"))
def test_terminal_decisions_have_stable_reason_and_no_signal(decision: str) -> None:
    situation = _project(_inputs())
    response = {
        "decision": decision,
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_name": "semiconductor_infrastructure",
        "symbol": None,
        "entry_price": None,
        "stop_price": None,
        "targets": (),
        "invalidation_rule": "현재 조건에서는 진입하지 않는다.",
        "confidence_bps": 3000,
        "observed_at": EVALUATED_AT,
        "valid_until": EVALUATED_AT + dt.timedelta(seconds=20),
        "reason_code": "setup_not_confirmed",
        "theme_rationale": None,
        "catalyst_rationale": None,
        "leader_rationale": None,
        "flow_rationale": None,
    }
    result = reason_trade_thesis(response, _champion(), situation, ())
    assert result.thesis.decision.value == decision
    assert result.signal is None


def test_host_rejects_unresolved_prices_and_reasoner_authority_fields() -> None:
    situation = _project(_inputs())
    base = {
        "decision": "recommend",
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_name": "semiconductor_infrastructure",
        "symbol": "NVDA",
        "entry_price": "777.00",
        "stop_price": "99.50",
        "targets": ({"label": "target_1", "price": "101.00"}, {"label": "target_2", "price": "102.00"}),
        "invalidation_rule": "현재 완료 봉 저가 이탈 시 논리가 무효화된다.",
        "confidence_bps": 7200,
        "observed_at": EVALUATED_AT,
        "valid_until": EVALUATED_AT + dt.timedelta(seconds=20),
        "reason_code": None,
        "theme_rationale": _rationale("테마 근거다.", situation.themes[0].claims[0].evidence_refs),
        "catalyst_rationale": _rationale("촉매 근거다.", situation.themes[0].catalysts[0].evidence_refs),
        "leader_rationale": _rationale("대장주 근거다.", situation.themes[0].leaders[0].evidence_refs),
        "flow_rationale": _rationale("수급 근거다.", situation.themes[0].leaders[0].flow.evidence_refs),
    }
    with pytest.raises(InvalidUsDayThesisError):
        bar_ref = next(
            ref for ref in situation.themes[0].leaders[0].evidence_refs if ref.namespace == "research/current_bar"
        )
        reason_trade_thesis(base, _champion(), situation, (_market("NVDA", bar_ref),))
    with pytest.raises(InvalidUsDayThesisError):
        reason_trade_thesis(base | {"quantity": 10}, _champion(), situation, ())


def _rationale(text: str, refs: tuple[object, ...], kind: str = "observed") -> dict[str, object]:
    return {"text": text, "observation_kind": kind, "evidence_refs": refs}


def _champion() -> UsDayChampion:
    lane = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES, agent_family=AgentFamily.DAY_TRADING, strategy_id="leader_breakout"
    )
    return UsDayChampion(
        version_id="a" * 64,
        strategy_version="us-day-leader-v1",
        strategy_lane=lane,
        deployed=True,
        playbooks=(UsDayPlaybook(playbook_id="leader_breakout", title="대장주 돌파", entry_type="stop_trigger"),),
    )


def _market(symbol: str, bar_ref: object) -> UsDayCurrentMarket:
    quote = QuoteValidation(
        bid=Decimal("100.00"),
        ask=Decimal("100.10"),
        observed_at=EVALUATED_AT - dt.timedelta(seconds=2),
        valid_until=EVALUATED_AT + dt.timedelta(seconds=30),
        spread_bps=Decimal("9.995"),
        max_slippage_bps=Decimal("20"),
    )
    return UsDayCurrentMarket(
        symbol=symbol,
        quote=quote,
        quote_ref=next(
            ref
            for ref in _project(_inputs()).evidence_refs
            if ref.namespace == "quote/snapshot" and ref in _project(_inputs()).themes[0].leaders[0].evidence_refs
        ),
        current_bar_ref=bar_ref,
        allowed_prices=(Decimal("99.50"), Decimal("100.00"), Decimal("100.10"), Decimal("101.00"), Decimal("102.00")),
    )
