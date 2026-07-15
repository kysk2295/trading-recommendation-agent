from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass

from trading_agent.kis_us_quote import (
    KisUsLevelOneQuote,
    KisUsQuoteUnavailableError,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability import (
    QuoteActionabilityAssessment,
    UsQuoteSnapshot,
    assess_us_quote,
    preflight_quote_assessment,
    provider_failed_assessment,
)


@dataclass(frozen=True, slots=True)
class UsQuotePublicationBatch:
    snapshots: tuple[UsQuoteSnapshot, ...]
    assessments: tuple[QuoteActionabilityAssessment, ...]
    derived_publications: tuple[TradeSignalPublication, ...]


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    quote: KisUsLevelOneQuote | None
    evaluated_at: dt.datetime


def evaluate_quote_publications(
    publications: tuple[TradeSignalPublication, ...],
    *,
    exchange_by_symbol: Mapping[str, str],
    fetch_quote: Callable[[str, str], KisUsLevelOneQuote],
    scan_started_at: dt.datetime,
    clock: Callable[[], dt.datetime],
) -> UsQuotePublicationBatch:
    if not publications:
        return UsQuotePublicationBatch((), (), ())

    ordered = tuple(
        sorted(
            publications,
            key=lambda item: (item.signal.symbol, item.signal.signal_id),
        )
    )
    fetched: dict[str, _FetchOutcome] = {}
    snapshots: dict[str, UsQuoteSnapshot] = {}
    assessments: list[QuoteActionabilityAssessment] = []
    derived_publications: list[TradeSignalPublication] = []

    for base in ordered:
        symbol = base.signal.symbol
        outcome = fetched.get(symbol)
        if outcome is None:
            preflight_at = clock()
            preflight = preflight_quote_assessment(
                base,
                scan_started_at=scan_started_at,
                evaluated_at=preflight_at,
            )
            if preflight is not None:
                assessments.append(preflight)
                continue
            exchange = exchange_by_symbol.get(symbol)
            if exchange is None:
                assessments.append(
                    provider_failed_assessment(
                        base,
                        scan_started_at=scan_started_at,
                        evaluated_at=preflight_at,
                    )
                )
                continue

            quote: KisUsLevelOneQuote | None = None
            with suppress(KisUsQuoteUnavailableError):
                quote = fetch_quote(exchange, symbol)
            outcome = _FetchOutcome(quote=quote, evaluated_at=clock())
            fetched[symbol] = outcome

        if outcome.quote is None:
            assessments.append(
                provider_failed_assessment(
                    base,
                    scan_started_at=scan_started_at,
                    evaluated_at=outcome.evaluated_at,
                )
            )
            continue

        decision = assess_us_quote(
            base,
            outcome.quote,
            scan_started_at=scan_started_at,
            evaluated_at=outcome.evaluated_at,
        )
        assessments.append(decision.assessment)
        if decision.snapshot is not None:
            snapshots.setdefault(decision.snapshot.quote_id, decision.snapshot)
        if decision.derived_publication is not None:
            derived_publications.append(decision.derived_publication)

    return UsQuotePublicationBatch(
        snapshots=tuple(snapshots.values()),
        assessments=tuple(assessments),
        derived_publications=tuple(derived_publications),
    )
