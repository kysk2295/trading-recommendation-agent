from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import Literal, assert_never

import pytest

from tests.day_strategy_capsule_support import builtin_request
from trading_agent.day_forward_probe_bridge import (
    DayCompletedBarLineage,
    DaySignalBlocked,
    DayTargetProjectionPolicy,
    DayTargetRule,
    DayTradeSignalProjection,
    DayTradeSignalProjectionRequest,
)
from trading_agent.day_strategy_capsule import (
    build_strategy_capsule,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
)
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.generated_strategy_protocol import BarFrame
from trading_agent.models import StrategySignal
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import EvidenceRef, QuoteValidation, TradeSignalEnvelope

BAR_AT = dt.datetime(2026, 8, 20, 14, 5, tzinfo=dt.UTC)
OBSERVED_AT = BAR_AT + dt.timedelta(minutes=1)
type BarMutation = Literal["future", "stale", "symbol", "timestamp", "invalid_symbol"]
type CandidateMutation = Literal["nonfinite", "direction", "rationale"]
type CapsuleMutation = Literal["market", "future", "bundle"]
type EvidenceMutation = Literal["unsorted", "future", "reserved"]
type QuoteMutation = Literal["stale", "spread"]


def projection_request(market_id: MarketId) -> DayTradeSignalProjectionRequest:
    match market_id:
        case MarketId.US_EQUITIES:
            symbol, entry, stop, bid, ask, spread = (
                "TEST",
                10.5,
                10.0,
                "10.49",
                "10.51",
                "19.047619",
            )
        case MarketId.KR_EQUITIES:
            symbol, entry, stop, bid, ask, spread = (
                "005930",
                70000.0,
                69000.0,
                "69990",
                "70010",
                "2.857143",
            )
        case unreachable:
            assert_never(unreachable)
    bar = BarFrame(
        symbol=symbol,
        timestamp=BAR_AT,
        open=entry,
        high=entry * 1.01,
        low=stop,
        close=entry,
        volume=100_000,
        prior_close=stop,
        average_daily_volume=1_000_000,
        spread_bps=float(spread),
        catalyst="filing",
    )
    return DayTradeSignalProjectionRequest(
        capsule=capsule(market_id),
        candidate=StrategySignal(
            symbol=symbol,
            timestamp=BAR_AT,
            strategy="generated-python:fixture",
            entry=entry,
            stop=stop,
            rationale="completed-bar momentum confirmed",
        ),
        completed_bar=DayCompletedBarLineage(
            market_id=market_id,
            bar=bar,
            valid_until=BAR_AT + dt.timedelta(minutes=5),
            record_id=f"session-20260820:{symbol}:{BAR_AT.isoformat()}",
        ),
        observed_at=OBSERVED_AT,
        quote_validation=QuoteValidation(
            bid=Decimal(bid),
            ask=Decimal(ask),
            observed_at=OBSERVED_AT - dt.timedelta(seconds=1),
            valid_until=OBSERVED_AT + dt.timedelta(minutes=1),
            spread_bps=Decimal(spread),
            max_slippage_bps=Decimal("25"),
        ),
        target_policy=DayTargetProjectionPolicy(
            rules=(
                DayTargetRule(label="1r", reward_risk_multiple=Decimal("1")),
                DayTargetRule(label="2r", reward_risk_multiple=Decimal("2")),
            ),
            valid_for=dt.timedelta(seconds=30),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="source/news",
                record_id=f"news:{symbol}:1",
                observed_at=BAR_AT - dt.timedelta(minutes=1),
            ),
        ),
    )


def capsule(
    market_id: MarketId,
    *,
    published_at: dt.datetime = BAR_AT - dt.timedelta(minutes=1),
    bundle_valid: bool = True,
) -> StrategyCapsule:
    request = builtin_request(market_id=market_id)
    return build_strategy_capsule(
        replace(
            request,
            protocol_sha256=(generated_protocol_bundle_sha256() if bundle_valid else "f" * 64),
            evaluator_sha256=generated_evaluator_bundle_sha256(),
            published_at=published_at,
        )
    )


def mutated_bar_request(mutation: BarMutation) -> DayTradeSignalProjectionRequest:
    request = projection_request(MarketId.US_EQUITIES)
    match mutation:
        case "future":
            return request.model_copy(update={"observed_at": BAR_AT - dt.timedelta(seconds=1)})
        case "stale":
            observed_at = request.completed_bar.valid_until + dt.timedelta(seconds=1)
            return request.model_copy(update={"observed_at": observed_at})
        case "symbol":
            candidate = replace(request.candidate, symbol="OTHER")
            return request.model_copy(update={"candidate": candidate})
        case "timestamp":
            candidate = replace(request.candidate, timestamp=BAR_AT + dt.timedelta(minutes=1))
            return request.model_copy(update={"candidate": candidate})
        case "invalid_symbol":
            bar = request.completed_bar.bar.model_copy(update={"symbol": "INVALID!"})
            lineage = request.completed_bar.model_copy(update={"bar": bar})
            candidate = replace(request.candidate, symbol="INVALID!")
            return request.model_copy(update={"candidate": candidate, "completed_bar": lineage})
        case unreachable:
            assert_never(unreachable)


def mutated_capsule_request(mutation: CapsuleMutation) -> DayTradeSignalProjectionRequest:
    request = projection_request(MarketId.US_EQUITIES)
    match mutation:
        case "market":
            changed = capsule(MarketId.KR_EQUITIES)
        case "future":
            changed = capsule(MarketId.US_EQUITIES, published_at=BAR_AT + dt.timedelta(seconds=1))
        case "bundle":
            changed = capsule(MarketId.US_EQUITIES, bundle_valid=False)
        case unreachable:
            assert_never(unreachable)
    return request.model_copy(update={"capsule": changed})


def mutated_candidate_request(mutation: CandidateMutation) -> DayTradeSignalProjectionRequest:
    request = projection_request(MarketId.US_EQUITIES)
    match mutation:
        case "nonfinite":
            candidate = replace(request.candidate, entry=float("nan"))
        case "direction":
            candidate = replace(request.candidate, stop=request.candidate.entry)
        case "rationale":
            candidate = replace(request.candidate, rationale="invalid\nrationale")
        case unreachable:
            assert_never(unreachable)
    return request.model_copy(update={"candidate": candidate})


def mutated_quote_request(mutation: QuoteMutation) -> DayTradeSignalProjectionRequest:
    request = projection_request(MarketId.US_EQUITIES)
    match mutation:
        case "stale":
            quote = request.quote_validation.model_copy(
                update={"valid_until": OBSERVED_AT - dt.timedelta(microseconds=1)}
            )
        case "spread":
            quote = request.quote_validation.model_copy(update={"max_slippage_bps": Decimal("10")})
        case unreachable:
            assert_never(unreachable)
    return request.model_copy(update={"quote_validation": quote})


def mutated_evidence_request(mutation: EvidenceMutation) -> DayTradeSignalProjectionRequest:
    request = projection_request(MarketId.US_EQUITIES)
    earlier = EvidenceRef(namespace="source/filing", record_id="filing:TEST:1", observed_at=BAR_AT)
    match mutation:
        case "unsorted":
            references = (request.evidence_refs[0], earlier)
        case "future":
            references = (
                EvidenceRef(
                    namespace="source/news",
                    record_id="news:TEST:2",
                    observed_at=OBSERVED_AT + dt.timedelta(seconds=1),
                ),
            )
        case "reserved":
            references = (
                EvidenceRef(namespace="day/cost_model", record_id="spoofed", observed_at=BAR_AT),
            )
        case unreachable:
            assert_never(unreachable)
    return request.model_copy(update={"evidence_refs": references})


def require_signal(result: DayTradeSignalProjection) -> TradeSignalEnvelope:
    match result:
        case TradeSignalEnvelope() as signal:
            return signal
        case DaySignalBlocked(reason=reason):
            pytest.fail(f"projection blocked: {reason.value}")
        case unreachable:
            assert_never(unreachable)
