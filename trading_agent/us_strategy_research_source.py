from __future__ import annotations

import datetime as dt
import hashlib
import math
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_models import AlpacaBar
from trading_agent.market_context_models import MarketContextSnapshot, MarketRegimeLabel
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class UsStrategyResearchSourceError(ValueError):
    pass


class UsLatestQuote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    bid: float = Field(gt=0, allow_inf_nan=False)
    ask: float = Field(gt=0, allow_inf_nan=False)
    observed_at: dt.datetime

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None or self.ask < self.bid:
            raise UsStrategyResearchSourceError("latest_quote_invalid")
        return self


def build_us_strategy_research_sources(
    bars_by_symbol: dict[str, tuple[AlpacaBar, ...]],
    quotes_by_symbol: dict[str, UsLatestQuote],
    now: dt.datetime,
) -> tuple[OpportunitySnapshot, MarketContextSnapshot]:
    try:
        bounds = _current_session_bounds(now)
        completed = _completed_bars(bars_by_symbol, now, bounds[0])
        if not completed:
            raise UsStrategyResearchSourceError("completed_bar_missing")
        ranked = tuple(
            sorted(
                completed.items(),
                key=lambda item: (-_continuation_score(item[1]), item[0]),
            )
        )
        symbol, bars = ranked[0]
        quote = quotes_by_symbol.get(symbol)
        if quote is None or quote.symbol != symbol:
            raise UsStrategyResearchSourceError("latest_quote_missing")
        if not dt.timedelta(0) <= now - quote.observed_at <= dt.timedelta(minutes=2):
            raise UsStrategyResearchSourceError("latest_quote_stale")
        last_bar = bars[-1]
        ended_at = last_bar.timestamp + dt.timedelta(minutes=1)
        observed_at = max(ended_at, quote.observed_at)
        if observed_at > now:
            raise UsStrategyResearchSourceError("source_time_future")
        spread_bps = (quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2.0) * 10_000.0
        if not math.isfinite(spread_bps):
            raise UsStrategyResearchSourceError("latest_quote_invalid")
        source_hash = _sha(
            ":".join(
                (
                    symbol,
                    *(bar.model_dump_json(by_alias=True) for bar in bars),
                    quote.model_dump_json(),
                )
            )
        )
        opportunity_id = f"us-momentum-{observed_at.strftime('%Y%m%dT%H%M%S')}-{source_hash[:16]}"
        evidence = (
            EvidenceRef(
                namespace="bar/alpaca-sip",
                record_id=f"{source_hash}:{last_bar.timestamp.isoformat()}",
                observed_at=ended_at,
            ),
            EvidenceRef(
                namespace="quote/alpaca-sip",
                record_id=f"{source_hash}:{quote.observed_at.isoformat()}",
                observed_at=quote.observed_at,
            ),
        )
        coverage = (
            SourceCoverage(
                source_id="alpaca_sip_completed_bars",
                observed_at=ended_at,
                record_count=sum(len(item) for item in completed.values()),
                complete=True,
            ),
            SourceCoverage(
                source_id="alpaca_sip_latest_quote",
                observed_at=quote.observed_at,
                record_count=1,
                complete=True,
            ),
        )
        opportunity = OpportunitySnapshot(
            opportunity_id=opportunity_id,
            strategy_lane=StrategyLaneRef(
                market_id=MarketId.US_EQUITIES,
                agent_family=AgentFamily.OPPORTUNITY_MANAGER,
                strategy_id="us_intraday_momentum",
            ),
            producer_strategy_version="alpaca-sip-current-session-momentum-v1",
            observed_at=observed_at,
            valid_until=observed_at + dt.timedelta(minutes=1),
            candidates=(
                OpportunityCandidate(
                    symbol=symbol,
                    rank=1,
                    score=Decimal(str(_continuation_score(bars))),
                    features=_features(bars, quote, spread_bps),
                ),
            ),
            evidence_refs=tuple(sorted(evidence, key=lambda item: item.canonical_id)),
            source_coverage=tuple(sorted(coverage, key=lambda item: item.source_id)),
        )
        context = MarketContextSnapshot(
            context_id=f"us-context-{source_hash[:24]}",
            market_id=MarketId.US_EQUITIES,
            observed_at=observed_at,
            valid_until=observed_at + dt.timedelta(minutes=1),
            regime_labels=(MarketRegimeLabel.UNKNOWN,),
            breadth_and_volatility_features=(
                FeatureValue(name="candidate_count", value=str(len(completed))),
                FeatureValue(name="spread_bps", value=_number(spread_bps)),
            ),
            macro_and_flow_refs=(),
            coverage=tuple(sorted(coverage, key=lambda item: item.source_id)),
            producer_version="alpaca-sip-current-session-context-v1",
        )
        return opportunity, context
    except UsStrategyResearchSourceError:
        raise
    except (ArithmeticError, KeyError, TypeError, ValueError):
        raise UsStrategyResearchSourceError("us_strategy_source_invalid") from None


def _current_session_bounds(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise UsStrategyResearchSourceError("source_time_invalid")
    bounds = regular_session_bounds(now.astimezone(NEW_YORK).date())
    if bounds is None or not bounds[0] < now < bounds[1]:
        raise UsStrategyResearchSourceError("session_closed")
    return bounds


def _completed_bars(
    bars_by_symbol: dict[str, tuple[AlpacaBar, ...]],
    now: dt.datetime,
    session_open: dt.datetime,
) -> dict[str, tuple[AlpacaBar, ...]]:
    result: dict[str, tuple[AlpacaBar, ...]] = {}
    for symbol, bars in bars_by_symbol.items():
        ordered = tuple(
            sorted(
                (
                    bar
                    for bar in bars
                    if session_open <= bar.timestamp and bar.timestamp + dt.timedelta(minutes=1) <= now
                ),
                key=lambda bar: bar.timestamp,
            )
        )
        if len(ordered) < 2:
            continue
        ended_at = ordered[-1].timestamp + dt.timedelta(minutes=1)
        if now - ended_at > dt.timedelta(minutes=10):
            continue
        result[symbol] = ordered[-6:]
    return result


def _continuation_score(bars: tuple[AlpacaBar, ...]) -> float:
    first, last = bars[0].close, bars[-1].close
    if first <= 0:
        raise UsStrategyResearchSourceError("completed_bar_invalid")
    return last / first - 1.0


def _features(
    bars: tuple[AlpacaBar, ...],
    quote: UsLatestQuote,
    spread_bps: float,
) -> tuple[FeatureValue, ...]:
    last = bars[-1]
    values = {
        "completed_bar_close": _number(last.close),
        "completed_bar_end_at": (last.timestamp + dt.timedelta(minutes=1)).isoformat(),
        "continuation_return": _number(_continuation_score(bars)),
        "spread_bps": _number(spread_bps),
        "quote_observed_at": quote.observed_at.isoformat(),
    }
    return tuple(FeatureValue(name=name, value=values[name]) for name in sorted(values))


def _number(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = (
    "UsLatestQuote",
    "UsStrategyResearchSourceError",
    "build_us_strategy_research_sources",
)
