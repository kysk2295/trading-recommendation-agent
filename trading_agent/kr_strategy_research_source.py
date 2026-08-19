from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

from trading_agent.kis_kr_market_models import (
    KisKrMarketEvidenceError,
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
    KisKrSnapshotProjectionInput,
)
from trading_agent.kis_kr_market_parsing import decimal_value, parse_bar_start, parse_minute_envelope
from trading_agent.kis_kr_market_projection import project_kis_kr_market_snapshot
from trading_agent.kr_intraday_market_gate import KrIntradayGateStatus, assess_kr_shadow_entry
from trading_agent.market_context_models import MarketContextSnapshot, MarketRegimeLabel
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)


class KrStrategyResearchSourceError(ValueError):
    pass


def build_kr_strategy_research_sources(
    opportunity: OpportunitySnapshot,
    receipts: tuple[KisKrMarketReceipt, ...],
    now: dt.datetime,
) -> tuple[OpportunitySnapshot, MarketContextSnapshot]:
    try:
        if len(opportunity.candidates) != 1 or now > opportunity.valid_until:
            raise KrStrategyResearchSourceError("opportunity_not_current")
        symbol = opportunity.candidates[0].symbol
        eligible = tuple(item for item in receipts if item.symbol == symbol and item.received_at <= now)
        minutes = tuple(item for item in eligible if item.kind is KisKrMarketReceiptKind.MINUTE_BARS)
        prices = tuple(item for item in eligible if item.kind is KisKrMarketReceiptKind.PRICE_STATUS)
        quotes = tuple(item for item in eligible if item.kind is KisKrMarketReceiptKind.ORDER_BOOK)
        if not minutes or not prices or not quotes:
            raise KrStrategyResearchSourceError("market_receipt_missing")
        completed_bar_close, completed_bar_end, completed_bar_observed, completed_bar_evidence = _latest_completed_bar(
            minutes, now
        )
        market = project_kis_kr_market_snapshot(
            KisKrSnapshotProjectionInput(
                price_receipt=max(prices, key=lambda item: item.received_at),
                quote_receipt=max(quotes, key=lambda item: item.received_at),
                evaluated_at=now,
            )
        )
        if assess_kr_shadow_entry(market, now).status is not KrIntradayGateStatus.ELIGIBLE:
            raise KrStrategyResearchSourceError("market_gate_blocked")
        if market.bid_price is None or market.ask_price is None:
            raise KrStrategyResearchSourceError("spread_missing")
        midpoint = (market.bid_price + market.ask_price) / Decimal(2)
        spread_bps = (market.ask_price - market.bid_price) / midpoint * Decimal(10_000)
        observed_at = max(opportunity.observed_at, market.observed_at, completed_bar_observed)
        if observed_at >= opportunity.valid_until:
            raise KrStrategyResearchSourceError("opportunity_stale")
        enriched = opportunity.model_copy(
            update={
                "observed_at": observed_at,
                "candidates": (
                    _candidate(
                        opportunity.candidates[0],
                        spread_bps,
                        completed_bar_close,
                        completed_bar_end,
                    ),
                ),
                "evidence_refs": tuple(
                    sorted(
                        {
                            item.canonical_id: item
                            for item in (
                                *opportunity.evidence_refs,
                                *market.evidence_refs,
                                completed_bar_evidence,
                            )
                        }.values(),
                        key=lambda item: item.canonical_id,
                    )
                ),
                "source_coverage": tuple(
                    sorted(
                        (
                            *opportunity.source_coverage,
                            SourceCoverage(
                                source_id="kis_kr_intraday_market",
                                observed_at=market.observed_at,
                                record_count=len(eligible),
                                complete=True,
                            ),
                        ),
                        key=lambda item: item.source_id,
                    )
                ),
            }
        )
        context = MarketContextSnapshot(
            context_id=(
                "kr-context-"
                + hashlib.sha256(f"{enriched.opportunity_id}:{market.observed_at.isoformat()}".encode()).hexdigest()[
                    :24
                ]
            ),
            market_id=enriched.strategy_lane.market_id,
            observed_at=market.observed_at,
            valid_until=min(enriched.valid_until, market.observed_at + dt.timedelta(minutes=3)),
            regime_labels=(MarketRegimeLabel.UNKNOWN,),
            breadth_and_volatility_features=tuple(
                sorted(
                    (
                        FeatureValue(name="completed_bar_close", value=_decimal(completed_bar_close)),
                        FeatureValue(name="spread_bps", value=_decimal(spread_bps)),
                    ),
                    key=lambda item: item.name,
                )
            ),
            macro_and_flow_refs=(),
            coverage=(
                SourceCoverage(
                    source_id="kis_kr_intraday_market",
                    observed_at=market.observed_at,
                    record_count=len(eligible),
                    complete=True,
                ),
            ),
            producer_version="kis-kr-intraday-context-v1",
        )
        return enriched, context
    except KrStrategyResearchSourceError:
        raise
    except (KisKrMarketEvidenceError, TypeError, ValueError):
        raise KrStrategyResearchSourceError("kr_strategy_source_invalid") from None


def _candidate(
    candidate: OpportunityCandidate,
    spread_bps: Decimal,
    completed_bar_close: Decimal,
    completed_bar_end: dt.datetime,
) -> OpportunityCandidate:
    values = {item.name: item.value for item in candidate.features}
    values.update(
        {
            "completed_bar_close": _decimal(completed_bar_close),
            "completed_bar_end_at": completed_bar_end.isoformat(),
            "spread_bps": _decimal(spread_bps),
        }
    )
    return candidate.model_copy(
        update={"features": tuple(FeatureValue(name=name, value=values[name]) for name in sorted(values))}
    )


def _latest_completed_bar(
    receipts: tuple[KisKrMarketReceipt, ...],
    now: dt.datetime,
) -> tuple[Decimal, dt.datetime, dt.datetime, EvidenceRef]:
    completed: list[tuple[dt.datetime, Decimal, KisKrMarketReceipt]] = []
    for receipt in receipts:
        for row in parse_minute_envelope(receipt).output2:
            started_at = parse_bar_start(row)
            if started_at + dt.timedelta(minutes=1) <= min(receipt.received_at, now):
                completed.append((started_at, decimal_value(row.stck_prpr), receipt))
    if not completed:
        raise KrStrategyResearchSourceError("completed_bar_missing")
    started_at, close, receipt = max(completed, key=lambda item: item[0])
    ended_at = started_at + dt.timedelta(minutes=1)
    evidence = EvidenceRef(
        namespace="bar/kis-kr-rest",
        record_id=f"{receipt.payload_sha256}:{started_at.strftime('%Y%m%d%H%M%S')}",
        observed_at=receipt.received_at,
    )
    return close, ended_at, receipt.received_at, evidence


def _decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = ("KrStrategyResearchSourceError", "build_kr_strategy_research_sources")
