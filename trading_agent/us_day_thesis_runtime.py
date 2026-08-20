from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, override

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import (
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
)
from trading_agent.us_day_situation_models import FlowObservationKind, ThemeMap, UsDaySituationMap
from trading_agent.us_day_thesis_models import (
    DayTradeDecision,
    EvidenceBoundRationale,
    TradeTarget,
    UsDayChampion,
    UsDayCurrentMarket,
    UsDayTradeThesis,
    situation_id_for,
)


class InvalidUsDayThesisError(ValueError):
    @override
    def __str__(self) -> str:
        return "US day thesis response is invalid"


class Reasoner(Protocol):
    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]: ...


class _ThesisSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    decision: DayTradeDecision
    situation_id: str
    agent_version_id: str
    playbook_id: str
    theme_id: str
    catalyst_event_id: str
    flow_inference_kind: str | None
    theme_name: str
    symbol: str | None
    entry_price: object | None
    stop_price: object | None
    targets: tuple[TradeTarget, ...]
    invalidation_rule: str
    confidence_bps: int
    observed_at: object
    valid_until: object
    reason_code: str | None
    theme_rationale: EvidenceBoundRationale | None
    catalyst_rationale: EvidenceBoundRationale | None
    leader_rationale: EvidenceBoundRationale | None
    flow_rationale: EvidenceBoundRationale | None


@dataclass(frozen=True, slots=True)
class UsDayThesisResult:
    thesis: UsDayTradeThesis
    signal: TradeSignalEnvelope | None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.thesis, name)


def build_trade_thesis_request(
    situation: UsDaySituationMap,
    champion: UsDayChampion,
    analogous_outcomes: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    if len(analogous_outcomes) > 20:
        raise InvalidUsDayThesisError
    return {
        "instruction": (
            "Use only supplied evidence. Label observed flow separately from inferred flow. "
            "Never output quantity, notional, account risk, broker, endpoint, or order authority."
        ),
        "situation": situation.model_dump(mode="json"),
        "champion": champion.model_dump(mode="json"),
        "analogous_outcomes": tuple(dict(item) for item in analogous_outcomes),
    }


def generate_trade_thesis(
    reasoner: Reasoner,
    champion: UsDayChampion,
    situation: UsDaySituationMap,
    current_markets: tuple[UsDayCurrentMarket, ...],
    analogous_outcomes: Sequence[Mapping[str, object]] = (),
) -> UsDayThesisResult:
    response = reasoner(build_trade_thesis_request(situation, champion, analogous_outcomes))
    return reason_trade_thesis(response, champion, situation, current_markets)


def reason_trade_thesis(
    response: Mapping[str, object],
    champion: UsDayChampion,
    situation: UsDaySituationMap,
    current_markets: tuple[UsDayCurrentMarket, ...] = (),
) -> UsDayThesisResult:
    try:
        submission = _ThesisSubmission.model_validate(response)
        _validate_authority(submission, champion, situation)
        evidence_refs = tuple(
            sorted(
                {
                    ref.canonical_id: ref
                    for rationale in (
                        submission.theme_rationale,
                        submission.catalyst_rationale,
                        submission.leader_rationale,
                        submission.flow_rationale,
                    )
                    if rationale is not None
                    for ref in rationale.evidence_refs
                }.values(),
                key=lambda item: item.canonical_id,
            )
        )
        thesis = UsDayTradeThesis.create(
            **submission.model_dump(mode="python"),
            evidence_refs=evidence_refs,
        )
        theme, markets = _validate_current_context(thesis, situation, current_markets)
        if thesis.decision is not DayTradeDecision.RECOMMEND:
            return UsDayThesisResult(thesis=thesis, signal=None)
        market = _validate_recommendation(thesis, champion, theme, markets)
        return UsDayThesisResult(thesis=thesis, signal=_project_signal(thesis, champion, market))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidUsDayThesisError from None


def _validate_authority(
    submission: _ThesisSubmission,
    champion: UsDayChampion,
    situation: UsDaySituationMap,
) -> None:
    if (
        not champion.deployed
        or champion.strategy_lane.market_id is not MarketId.US_EQUITIES
        or champion.strategy_lane.agent_family is not AgentFamily.DAY_TRADING
        or submission.situation_id != situation_id_for(situation)
        or submission.agent_version_id != champion.version_id
        or submission.playbook_id not in {item.playbook_id for item in champion.playbooks}
    ):
        raise InvalidUsDayThesisError


def _validate_current_context(
    thesis: UsDayTradeThesis,
    situation: UsDaySituationMap,
    markets: tuple[UsDayCurrentMarket, ...],
) -> tuple[ThemeMap, dict[str, UsDayCurrentMarket]]:
    theme = next((item for item in situation.themes if item.theme_id == thesis.theme_id), None)
    if theme is None:
        raise InvalidUsDayThesisError
    catalyst = next((item for item in theme.catalysts if item.event_id == thesis.catalyst_event_id), None)
    labels = set(thesis.theme_name.split("_"))
    meaningful_keywords = set(theme.keywords) - {"active", "demand", "market", "stock", "theme"}
    market_by_symbol = {item.symbol: item for item in markets}
    leader_by_symbol = {item.symbol: item for item in theme.leaders}
    all_leaders = {leader.symbol: leader for item in situation.themes for leader in item.leaders}
    if (
        catalyst is None
        or not labels & meaningful_keywords
        or len(market_by_symbol) != len(markets)
        or set(market_by_symbol) != set(all_leaders)
    ):
        raise InvalidUsDayThesisError
    for symbol, market in market_by_symbol.items():
        leader = all_leaders[symbol]
        if (
            market.current_bar.timestamp != situation.completed_bar_at
            or market.current_bar_ref not in leader.evidence_refs
            or market.quote_ref not in leader.evidence_refs
            or thesis.observed_at < market.quote.observed_at
            or thesis.observed_at > market.quote.valid_until
            or thesis.valid_until > market.quote.valid_until
        ):
            raise InvalidUsDayThesisError
    if (
        thesis.observed_at < situation.evaluated_at
        or any(ref.observed_at > thesis.observed_at for ref in situation.evidence_refs)
        or (thesis.symbol is not None and thesis.symbol not in leader_by_symbol)
    ):
        raise InvalidUsDayThesisError
    return theme, market_by_symbol


def _validate_recommendation(
    thesis: UsDayTradeThesis,
    champion: UsDayChampion,
    theme: ThemeMap,
    markets: dict[str, UsDayCurrentMarket],
) -> UsDayCurrentMarket:
    assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
    leader = next(item for item in theme.leaders if item.symbol == thesis.symbol)
    catalyst = next(item for item in theme.catalysts if item.event_id == thesis.catalyst_event_id)
    market = markets[thesis.symbol]
    playbook = next(item for item in champion.playbooks if item.playbook_id == thesis.playbook_id)
    rationales = (
        thesis.theme_rationale,
        thesis.catalyst_rationale,
        thesis.leader_rationale,
        thesis.flow_rationale,
    )
    if any(item is None for item in rationales):
        raise InvalidUsDayThesisError
    theme_rationale, catalyst_rationale, leader_rationale, flow_rationale = rationales
    assert theme_rationale and catalyst_rationale and leader_rationale and flow_rationale
    expected_flow_refs = leader.flow.evidence_refs
    if flow_rationale.observation_kind is FlowObservationKind.OBSERVED:
        if thesis.flow_inference_kind is not None:
            raise InvalidUsDayThesisError
    else:
        inference = next(
            (item for item in leader.inferences if item.kind == thesis.flow_inference_kind),
            None,
        )
        if inference is None:
            raise InvalidUsDayThesisError
        expected_flow_refs = inference.evidence_refs
    exact_bindings = (
        (theme_rationale.evidence_refs, theme.claims[0].evidence_refs),
        (catalyst_rationale.evidence_refs, catalyst.evidence_refs),
        (leader_rationale.evidence_refs, leader.evidence_refs),
        (flow_rationale.evidence_refs, expected_flow_refs),
    )
    if any(actual != expected for actual, expected in exact_bindings):
        raise InvalidUsDayThesisError
    entry = market.quote.ask if playbook.entry_type == "stop_trigger" else market.quote.bid
    structure = {
        Decimal(str(market.current_bar.low)),
        Decimal(str(market.current_bar.open)),
        Decimal(str(market.current_bar.prior_close)),
    }
    risk = entry - thesis.stop_price
    expected_targets = tuple(entry + risk * Decimal(index) for index in range(1, len(thesis.targets) + 1))
    if (
        thesis.entry_price != entry
        or thesis.stop_price not in structure
        or risk <= 0
        or tuple(item.price for item in thesis.targets) != expected_targets
    ):
        raise InvalidUsDayThesisError
    return market


def _project_signal(
    thesis: UsDayTradeThesis,
    champion: UsDayChampion,
    market: UsDayCurrentMarket,
) -> TradeSignalEnvelope:
    assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
    playbook = next(item for item in champion.playbooks if item.playbook_id == thesis.playbook_id)
    return TradeSignalEnvelope(
        signal_id=thesis.thesis_id,
        strategy_lane=champion.strategy_lane,
        producer_strategy_version=champion.strategy_version,
        symbol=thesis.symbol,
        observed_at=thesis.observed_at,
        valid_until=thesis.valid_until,
        side=SignalSide.LONG,
        entry_type=SignalEntryType(playbook.entry_type),
        entry_price=thesis.entry_price,
        stop_price=thesis.stop_price,
        targets=thesis.targets,
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule=thesis.invalidation_rule,
        rationale=thesis.rationale,
        evidence_refs=thesis.evidence_refs,
        quote_validation=market.quote,
        opportunity_id=thesis.situation_id,
    )


__all__ = (
    "InvalidUsDayThesisError",
    "UsDayThesisResult",
    "build_trade_thesis_request",
    "generate_trade_thesis",
    "reason_trade_thesis",
)
