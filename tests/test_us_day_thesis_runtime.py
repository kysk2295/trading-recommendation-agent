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
        "theme_id": theme.theme_id,
        "catalyst_event_id": theme.catalysts[0].event_id,
        "flow_inference_kind": None,
        "theme_name": "semiconductor_infrastructure",
        "symbol": leader.symbol,
        "entry_price": "200.05",
        "stop_price": "199.5",
        "targets": ({"label": "target_1", "price": "200.60"}, {"label": "target_2", "price": "201.15"}),
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
        "flow_rationale": _rationale("관측된 원시 수급만 사용했다.", leader.flow.evidence_refs, "observed"),
    }

    result = reason_trade_thesis(response, _champion(), situation, _markets())

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
        "theme_id": situation.themes[0].theme_id,
        "catalyst_event_id": situation.themes[0].catalysts[0].event_id,
        "flow_inference_kind": None,
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
    result = reason_trade_thesis(response, _champion(), situation, _markets())
    assert result.thesis.decision.value == decision
    assert result.signal is None


def test_host_rejects_unresolved_prices_and_reasoner_authority_fields() -> None:
    situation = _project(_inputs())
    base = {
        "decision": "recommend",
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_id": situation.themes[0].theme_id,
        "catalyst_event_id": situation.themes[0].catalysts[0].event_id,
        "flow_inference_kind": None,
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


def _market(symbol: str, _bar_ref: object) -> UsDayCurrentMarket:
    inputs = _inputs()
    tick = next(item for item in inputs.completed_bars if item.bars[-1].symbol == symbol)
    situation = _project(inputs)
    leader = next(item for theme in situation.themes for item in theme.leaders if item.symbol == symbol)
    raw_quote = next(item for item in inputs.quotes if item.symbol == symbol)
    return UsDayCurrentMarket(
        symbol=symbol,
        quote=QuoteValidation(
            bid=raw_quote.bid,
            ask=raw_quote.ask,
            observed_at=raw_quote.evidence_ref.observed_at,
            valid_until=tick.quote.valid_until,
            spread_bps=raw_quote.spread_bps,
            max_slippage_bps=Decimal("20"),
        ),
        quote_ref=raw_quote.evidence_ref,
        current_bar_ref=next(
            ref
            for ref in leader.evidence_refs
            if ref.namespace == "research/current_bar" and ref.record_id == tick.completed_bar_id
        ),
        current_bar=tick.bars[-1],
    )


def _markets() -> tuple[UsDayCurrentMarket, ...]:
    situation = _project(_inputs())
    return tuple(
        _market(leader.symbol, next(ref for ref in leader.evidence_refs if ref.namespace == "research/current_bar"))
        for theme in situation.themes
        for leader in theme.leaders
    )


def test_terminal_decision_rejects_missing_market_and_stale_observation() -> None:
    situation = _project(_inputs())
    theme = situation.themes[0]
    response = {
        "decision": "watch",
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_id": theme.theme_id,
        "catalyst_event_id": theme.catalysts[0].event_id,
        "flow_inference_kind": None,
        "theme_name": "semiconductor_infrastructure",
        "symbol": None,
        "entry_price": None,
        "stop_price": None,
        "targets": (),
        "invalidation_rule": "현재 조건에서는 진입하지 않는다.",
        "confidence_bps": 3000,
        "observed_at": EVALUATED_AT - dt.timedelta(minutes=1),
        "valid_until": EVALUATED_AT + dt.timedelta(seconds=20),
        "reason_code": "setup_not_confirmed",
        "theme_rationale": None,
        "catalyst_rationale": None,
        "leader_rationale": None,
        "flow_rationale": None,
    }
    with pytest.raises(InvalidUsDayThesisError):
        reason_trade_thesis(response, _champion(), situation, ())
    with pytest.raises(InvalidUsDayThesisError):
        reason_trade_thesis(response, _champion(), situation, _markets())


@pytest.mark.parametrize(
    "update",
    (
        {"theme_name": "fabricated_theme"},
        {"theme_name": "semiconductor_fabricated"},
        {"theme_id": "f" * 64},
        {"catalyst_event_id": "e" * 64},
        {"targets": ({"label": "target_1", "price": "300"}, {"label": "target_2", "price": "400"})},
        {"stop_price": "199.4"},
    ),
)
def test_host_rejects_fabricated_bindings_and_non_structural_prices(update: dict[str, object]) -> None:
    situation = _project(_inputs())
    with pytest.raises(InvalidUsDayThesisError):
        reason_trade_thesis(_valid_response() | update, _champion(), situation, _markets())


def test_flow_inference_requires_exact_selected_typed_inference_refs() -> None:
    situation = _project(_inputs())
    leader = situation.themes[0].leaders[0]
    inference = leader.inferences[0]
    response = _valid_response() | {
        "flow_inference_kind": inference.kind.value,
        "flow_rationale": _rationale("선택한 수급 프록시 추론이다.", inference.evidence_refs, "inferred"),
    }
    assert reason_trade_thesis(response, _champion(), situation, _markets()).flow_inference_kind == inference.kind
    with pytest.raises(InvalidUsDayThesisError):
        reason_trade_thesis(
            response | {"flow_rationale": _rationale("잘못된 추론 근거다.", leader.flow.evidence_refs[:1], "inferred")},
            _champion(),
            situation,
            _markets(),
        )


def _valid_response() -> dict[str, object]:
    situation = _project(_inputs())
    theme = situation.themes[0]
    leader = theme.leaders[0]
    return {
        "decision": "recommend",
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_id": theme.theme_id,
        "catalyst_event_id": theme.catalysts[0].event_id,
        "flow_inference_kind": None,
        "theme_name": "semiconductor_infrastructure",
        "symbol": leader.symbol,
        "entry_price": "200.05",
        "stop_price": "199.5",
        "targets": ({"label": "target_1", "price": "200.60"}, {"label": "target_2", "price": "201.15"}),
        "invalidation_rule": "현재 완료 봉 저가 이탈 시 논리가 무효화된다.",
        "confidence_bps": 7200,
        "observed_at": EVALUATED_AT,
        "valid_until": EVALUATED_AT + dt.timedelta(seconds=20),
        "reason_code": None,
        "theme_rationale": _rationale("반도체 인프라 테마다.", theme.claims[0].evidence_refs),
        "catalyst_rationale": _rationale("현재 세션 촉매다.", theme.catalysts[0].evidence_refs),
        "leader_rationale": _rationale("선택한 대장주다.", leader.evidence_refs),
        "flow_rationale": _rationale("관측된 원시 수급이다.", leader.flow.evidence_refs),
    }
