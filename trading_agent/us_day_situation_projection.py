from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import deque
from decimal import Decimal
from typing import Final, Never, override

from pydantic import ValidationError

from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.alpaca_news_opportunity_evidence import AlpacaNewsOpportunityEvidenceBundle
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.signal_contract_models import EvidenceRef
from trading_agent.us_day_situation_models import (
    CatalystEvidence,
    EvidenceBoundClaim,
    FlowObservationKind,
    LeaderCandidate,
    ObservableFlow,
    ThemeMap,
    ThemeState,
    UsDaySituationMap,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_forward_shadow_models import UsForwardShadowTick, current_xnys_tick_at
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_quote_actionability_evidence import UsQuotePolicyEvidence

_MAX_SCANNER_AGE: Final = dt.timedelta(minutes=1)
_MAX_NEWS_AGE: Final = dt.timedelta(minutes=5)
_MAX_CONTEXT_AGE: Final = dt.timedelta(minutes=5)
_MAX_QUOTE_AGE: Final = dt.timedelta(seconds=5)
_EMERGING_AGE: Final = dt.timedelta(minutes=30)
_ACTIVE_AGE: Final = dt.timedelta(hours=2)
_TOKEN = re.compile(r"[a-z0-9]+", flags=re.ASCII)
_STOPWORDS: Final = frozenset(
    {"after", "amid", "and", "for", "from", "into", "market", "shares", "stock", "the", "with"}
)


class UsDaySituationProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US day situation projection is invalid"


def project_us_day_situation(
    *,
    scanner: UsOpportunityScannerBundle,
    articles: tuple[AlpacaNewsArticle, ...],
    news_evidence: AlpacaNewsOpportunityEvidenceBundle,
    market_context: MarketContextSnapshot,
    quotes: tuple[UsQuotePolicyEvidence, ...],
    completed_bars: tuple[UsForwardShadowTick, ...],
    evaluated_at: dt.datetime,
) -> UsDaySituationMap:
    try:
        session_date = _validate_inputs(
            scanner,
            articles,
            news_evidence,
            market_context,
            quotes,
            completed_bars,
            evaluated_at,
        )
        quote_by_symbol = {item.symbol: item for item in quotes}
        tick_by_symbol = {item.bars[-1].symbol: item for item in completed_bars}
        observation_refs = _observation_refs(news_evidence)
        themes = tuple(
            sorted(
                (
                    _project_theme(
                        group,
                        scanner,
                        observation_refs,
                        quote_by_symbol,
                        tick_by_symbol,
                        evaluated_at,
                    )
                    for group in _article_groups(articles)
                ),
                key=lambda item: item.theme_id,
            )
        )
        if not themes:
            _fail()
        return UsDaySituationMap(
            session_id=f"XNYS-{session_date.isoformat()}",
            session_date=session_date,
            completed_bar_at=completed_bars[0].bars[-1].timestamp,
            evaluated_at=evaluated_at,
            themes=themes,
            evidence_refs=_refs(
                tuple(ref for theme in themes for ref in theme.evidence_refs) + _context_refs(market_context)
            ),
        )
    except (TypeError, ValidationError, ValueError):
        raise UsDaySituationProjectionError from None


def _validate_inputs(
    scanner: UsOpportunityScannerBundle,
    articles: tuple[AlpacaNewsArticle, ...],
    news: AlpacaNewsOpportunityEvidenceBundle,
    context: MarketContextSnapshot,
    quotes: tuple[UsQuotePolicyEvidence, ...],
    ticks: tuple[UsForwardShadowTick, ...],
    evaluated_at: dt.datetime,
) -> dt.date:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        _fail()
    session_date = evaluated_at.astimezone(NEW_YORK).date()
    bounds = regular_session_bounds(session_date)
    if bounds is None or not bounds[0] < evaluated_at < bounds[1]:
        _fail()
    opportunity = scanner.opportunity
    opportunity_symbols = tuple(item.symbol for item in opportunity.candidates)
    broad_by_symbol = {item.symbol: item for item in scanner.snapshot.candidates}
    if (
        scanner.snapshot.observed_at != opportunity.observed_at
        or set(broad_by_symbol) != set(opportunity_symbols)
        or any(
            broad_by_symbol[item.symbol].source_rank != item.rank
            or broad_by_symbol[item.symbol].priority_score != item.score
            for item in opportunity.candidates
        )
        or opportunity.valid_until < evaluated_at
        or not _fresh(opportunity.observed_at, evaluated_at, _MAX_SCANNER_AGE)
        or any(not item.complete for item in opportunity.source_coverage)
    ):
        _fail()
    symbols = set(opportunity_symbols)
    article_ids = tuple(item.event_id for item in articles)
    if (
        not articles
        or len(article_ids) != len(set(article_ids))
        or not news.assessment.complete
        or tuple(item.symbol for item in news.snapshots) != tuple(sorted(symbols))
        or not _fresh(news.assessment.assessed_at, evaluated_at, _MAX_NEWS_AGE)
        or any(not item.coverage.complete for item in news.snapshots)
        or any(not set(item.symbols).issubset(symbols) for item in articles)
    ):
        _fail()
    article_by_id = {item.event_id: item for item in articles}
    observed_pairs: set[tuple[str, str]] = set()
    for snapshot in news.snapshots:
        for observation in snapshot.observations:
            article = article_by_id.get(observation.event_id)
            if (
                article is None
                or snapshot.symbol not in article.symbols
                or observation.source != article.source
                or observation.provider_created_at != article.created_at
                or observation.provider_updated_at != article.updated_at
                or observation.received_at > evaluated_at
            ):
                _fail()
            observed_pairs.add((observation.event_id, snapshot.symbol))
    if any(
        item.created_at < bounds[0]
        or item.created_at > item.updated_at
        or item.updated_at > evaluated_at
        or any((item.event_id, symbol) not in observed_pairs for symbol in item.symbols)
        for item in articles
    ):
        _fail()
    if (
        context.market_id.value != "us_equities"
        or context.valid_until < evaluated_at
        or not _fresh(context.observed_at, evaluated_at, _MAX_CONTEXT_AGE)
        or any(not item.complete for item in context.coverage)
    ):
        _fail()
    if (
        {item.symbol for item in quotes} != symbols
        or len(quotes) != len(symbols)
        or any(
            item.received_at > evaluated_at
            or item.received_at < item.provider_observed_at
            or not _fresh(item.provider_observed_at, evaluated_at, _MAX_QUOTE_AGE)
            for item in quotes
        )
    ):
        _fail()
    tick_symbols = tuple(item.bars[-1].symbol for item in ticks)
    latest_times = {item.bars[-1].timestamp for item in ticks}
    if (
        set(tick_symbols) != symbols
        or len(tick_symbols) != len(symbols)
        or len(latest_times) != 1
        or any(
            item.candidate is None
            or item.candidate.symbol != item.bars[-1].symbol
            or not current_xnys_tick_at(item, evaluated_at)
            for item in ticks
        )
    ):
        _fail()
    return session_date


def _project_theme(
    articles: tuple[AlpacaNewsArticle, ...],
    scanner: UsOpportunityScannerBundle,
    observation_refs: dict[tuple[str, str], tuple[EvidenceRef, ...]],
    quotes: dict[str, UsQuotePolicyEvidence],
    ticks: dict[str, UsForwardShadowTick],
    evaluated_at: dt.datetime,
) -> ThemeMap:
    symbols = tuple(sorted({symbol for item in articles for symbol in item.symbols}))
    keywords = tuple(sorted({word for item in articles for word in _keywords(item.headline)}))[:12]
    catalysts = tuple(
        CatalystEvidence(
            event_id=item.event_id,
            headline=item.headline,
            source=item.source,
            symbols=item.symbols,
            published_at=item.created_at,
            received_at=max(
                ref.observed_at for symbol in item.symbols for ref in observation_refs[(item.event_id, symbol)]
            ),
            evidence_refs=_refs(
                tuple(ref for symbol in item.symbols for ref in observation_refs[(item.event_id, symbol)])
            ),
        )
        for item in sorted(articles, key=lambda value: (value.created_at, value.event_id))
    )
    scanner_by_symbol = {item.symbol: item for item in scanner.opportunity.candidates}
    changes = {
        symbol: _decimal(ticks[symbol].candidate.change_pct)  # type: ignore[union-attr]
        for symbol in symbols
    }
    raw_leaders: list[tuple[Decimal, Decimal, Decimal, str, ObservableFlow, tuple[EvidenceRef, ...]]] = []
    for symbol in symbols:
        tick = ticks[symbol]
        candidate = tick.candidate
        if candidate is None:
            _fail()
        refs = _refs(
            (
                *tick.evidence_refs,
                quotes[symbol].evidence_ref,
                *scanner.opportunity.evidence_refs,
            )
        )
        relative_volume = _decimal(candidate.relative_volume)
        dollar_volume = _decimal(candidate.cumulative_dollar_volume)
        relative_strength = changes[symbol] - sum(changes.values(), Decimal(0)) / Decimal(len(changes))
        flow = ObservableFlow(
            observation_kind=FlowObservationKind.OBSERVED,
            relative_volume=relative_volume,
            dollar_volume=dollar_volume,
            spread_bps=quotes[symbol].spread_bps,
            bid_size=quotes[symbol].bid_size,
            ask_size=quotes[symbol].ask_size,
            vwap_relation="unavailable",
            breakout_absorption_proxy=None,
            cross_symbol_relative_strength=relative_strength,
            evidence_refs=refs,
        )
        score = relative_volume + changes[symbol] + scanner_by_symbol[symbol].score
        raw_leaders.append((score, relative_volume, dollar_volume, symbol, flow, refs))
    ordered = sorted(raw_leaders, key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    leaders = tuple(
        LeaderCandidate(
            symbol=item[3],
            rank=index,
            leader_score=item[0],
            flow=item[4],
            evidence_refs=item[5],
        )
        for index, item in enumerate(ordered, start=1)
    )
    theme_refs = _refs(
        tuple(ref for item in catalysts for ref in item.evidence_refs)
        + tuple(ref for item in leaders for ref in item.evidence_refs)
    )
    identity_material = {"symbols": symbols, "keywords": keywords, "events": tuple(item.event_id for item in catalysts)}
    newest = max(item.published_at for item in catalysts)
    age = evaluated_at - newest
    state = (
        ThemeState.EMERGING if age <= _EMERGING_AGE else ThemeState.ACTIVE if age <= _ACTIVE_AGE else ThemeState.AGING
    )
    return ThemeMap(
        theme_id=hashlib.sha256(
            json.dumps(identity_material, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        state=state,
        symbols=symbols,
        keywords=keywords,
        catalysts=catalysts,
        leaders=leaders,
        claims=(
            EvidenceBoundClaim(
                text=f"Shared current-session catalyst links {', '.join(symbols)}.",
                observation_kind=FlowObservationKind.OBSERVED,
                evidence_refs=theme_refs,
            ),
        ),
        evidence_refs=theme_refs,
    )


def _article_groups(articles: tuple[AlpacaNewsArticle, ...]) -> tuple[tuple[AlpacaNewsArticle, ...], ...]:
    by_id = {item.event_id: item for item in articles}
    remaining = set(by_id)
    groups: list[tuple[AlpacaNewsArticle, ...]] = []
    while remaining:
        queue = deque((min(remaining),))
        component: set[str] = set()
        while queue:
            event_id = queue.popleft()
            if event_id in component:
                continue
            component.add(event_id)
            current = by_id[event_id]
            current_symbols = set(current.symbols)
            current_keywords = set(_keywords(current.headline))
            for other_id in sorted(remaining - component):
                other = by_id[other_id]
                if current_symbols.intersection(other.symbols) and current_keywords.intersection(
                    _keywords(other.headline)
                ):
                    queue.append(other_id)
        remaining -= component
        groups.append(tuple(by_id[item] for item in sorted(component)))
    return tuple(groups)


def _observation_refs(
    evidence: AlpacaNewsOpportunityEvidenceBundle,
) -> dict[tuple[str, str], tuple[EvidenceRef, ...]]:
    result: dict[tuple[str, str], tuple[EvidenceRef, ...]] = {}
    for snapshot in evidence.snapshots:
        article_ref_by_record = {
            item.record_id: item for item in snapshot.evidence_refs if item.namespace == "alpaca/news/article"
        }
        for observation in snapshot.observations:
            ref = article_ref_by_record.get(observation.observation_id)
            if ref is None:
                _fail()
            result[(observation.event_id, snapshot.symbol)] = (ref,)
    return result


def _context_refs(context: MarketContextSnapshot) -> tuple[EvidenceRef, ...]:
    return tuple(
        EvidenceRef(
            namespace="market/context/coverage",
            record_id=f"{context.context_id}:{item.source_id}",
            observed_at=item.observed_at,
        )
        for item in context.coverage
    )


def _keywords(headline: str) -> tuple[str, ...]:
    return tuple(
        sorted({token for token in _TOKEN.findall(headline.casefold()) if len(token) >= 3 and token not in _STOPWORDS})
    )[:12]


def _refs(refs: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    by_id = {item.canonical_id: item for item in refs}
    return tuple(by_id[item] for item in sorted(by_id))


def _fresh(observed_at: dt.datetime, evaluated_at: dt.datetime, limit: dt.timedelta) -> bool:
    return observed_at <= evaluated_at and evaluated_at - observed_at <= limit


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _fail() -> Never:
    raise UsDaySituationProjectionError


__all__ = ("UsDaySituationProjectionError", "project_us_day_situation")
