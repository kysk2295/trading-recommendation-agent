from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast, override

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import (
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
)
from trading_agent.us_day_situation_models import UsDaySituationMap
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
        if thesis.decision is not DayTradeDecision.RECOMMEND:
            return UsDayThesisResult(thesis=thesis, signal=None)
        market = _validate_recommendation(thesis, champion, situation, current_markets)
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


def _validate_recommendation(
    thesis: UsDayTradeThesis,
    champion: UsDayChampion,
    situation: UsDaySituationMap,
    markets: tuple[UsDayCurrentMarket, ...],
) -> UsDayCurrentMarket:
    assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
    theme = next(
        (item for item in situation.themes if any(leader.symbol == thesis.symbol for leader in item.leaders)),
        None,
    )
    leader = None if theme is None else next(item for item in theme.leaders if item.symbol == thesis.symbol)
    market = next((item for item in markets if item.symbol == thesis.symbol), None)
    playbook = next(item for item in champion.playbooks if item.playbook_id == thesis.playbook_id)
    if theme is None or leader is None or market is None:
        raise InvalidUsDayThesisError
    all_situation_refs = {item.canonical_id: item for item in situation.evidence_refs}
    if any(
        ref.canonical_id not in all_situation_refs or all_situation_refs[ref.canonical_id] != ref
        for ref in thesis.evidence_refs
    ):
        raise InvalidUsDayThesisError
    unvalidated_rationales = (
        thesis.theme_rationale,
        thesis.catalyst_rationale,
        thesis.leader_rationale,
        thesis.flow_rationale,
    )
    if any(item is None for item in unvalidated_rationales):
        raise InvalidUsDayThesisError
    rationales = cast(tuple[EvidenceBoundRationale, ...], unvalidated_rationales)
    allowed_rationale_refs = (
        {ref.canonical_id for ref in theme.claims[0].evidence_refs},
        {ref.canonical_id for catalyst in theme.catalysts for ref in catalyst.evidence_refs},
        {ref.canonical_id for ref in leader.evidence_refs},
        {ref.canonical_id for ref in leader.flow.evidence_refs}
        | {ref.canonical_id for inference in leader.inferences for ref in inference.evidence_refs},
    )
    if any(
        not {ref.canonical_id for ref in rationale.evidence_refs} <= allowed
        for rationale, allowed in zip(rationales, allowed_rationale_refs, strict=True)
    ):
        raise InvalidUsDayThesisError
    prices = (thesis.stop_price, thesis.entry_price, *(item.price for item in thesis.targets))
    if (
        any(item not in market.allowed_prices for item in prices)
        or thesis.entry_price != market.quote.ask
        or market.quote_ref not in situation.evidence_refs
        or market.current_bar_ref not in situation.evidence_refs
        or thesis.observed_at < situation.evaluated_at
        or thesis.observed_at < market.quote.observed_at
        or thesis.observed_at > market.quote.valid_until
        or thesis.valid_until > market.quote.valid_until
        or (playbook.entry_type == "limit" and thesis.entry_price != market.quote.bid)
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
