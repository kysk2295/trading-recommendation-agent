from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import deque
from collections.abc import Mapping
from decimal import Decimal
from typing import Final, Literal, Never, override

from pydantic import ValidationError

from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.alpaca_news_opportunity_evidence import AlpacaNewsOpportunityEvidenceBundle
from trading_agent.generated_strategy_protocol import BarFrame
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.signal_contract_models import EvidenceRef, OpportunitySnapshot
from trading_agent.us_day_situation_models import (
    CatalystClaimEvent,
    CatalystEvidence,
    EvidenceBoundClaim,
    FlowInference,
    FlowInferenceKind,
    FlowObservationKind,
    LeaderCandidate,
    ObservableFlow,
    SituationClaimKind,
    ThemeMap,
    ThemeState,
    UsDaySituationMap,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_forward_shadow_models import UsForwardShadowTick, completed_bar_id, current_xnys_tick_at
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_quote_actionability_evidence import UsQuotePolicyEvidence
from trading_agent.us_strategy_day_input import (
    UsStrategyDayCandidateEvidence,
    UsStrategyDayInput,
)
from trading_agent.us_strategy_day_input import (
    spread_bps as strategy_spread_bps,
)

_MAX_SCANNER_AGE: Final = dt.timedelta(minutes=1)
_MAX_NEWS_AGE: Final = dt.timedelta(minutes=5)
_MAX_CONTEXT_AGE: Final = dt.timedelta(minutes=5)
_MAX_QUOTE_AGE: Final = dt.timedelta(seconds=5)
_EMERGING_AGE: Final = dt.timedelta(minutes=30)
_ACTIVE_AGE: Final = dt.timedelta(hours=2)
_REGULAR_SESSION_MINUTES: Final = Decimal(390)
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
                        scanner.opportunity,
                        observation_refs,
                        quote_by_symbol,
                        tick_by_symbol,
                        evaluated_at,
                    )
                    for group in _article_groups(articles, set(quote_by_symbol))
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
                (
                    *(ref for theme in themes for ref in theme.evidence_refs),
                    _context_ref(market_context),
                )
            ),
        )
    except (TypeError, ValidationError, ValueError):
        raise UsDaySituationProjectionError from None


def project_us_strategy_day_situation(source: UsStrategyDayInput, evaluated_at: dt.datetime) -> UsDaySituationMap:
    try:
        checked = UsStrategyDayInput.model_validate(source.model_dump(mode="python"))
        session_date = _validate_strategy_input(checked, evaluated_at)
        evidence_by_symbol = {item.symbol: item for item in checked.candidates}
        observation_refs = _observation_refs(checked.news_evidence)
        themes = tuple(
            sorted(
                (
                    _project_theme(
                        group,
                        checked.opportunity,
                        observation_refs,
                        evidence_by_symbol,
                        evidence_by_symbol,
                        evaluated_at,
                    )
                    for group in _article_groups(checked.articles, set(evidence_by_symbol))
                ),
                key=lambda item: item.theme_id,
            )
        )
        if not themes:
            _fail()
        return UsDaySituationMap(
            session_id=f"XNYS-{session_date.isoformat()}",
            session_date=session_date,
            completed_bar_at=checked.candidates[0].bars[-1].timestamp,
            evaluated_at=evaluated_at,
            themes=themes,
            evidence_refs=_refs(
                (
                    *(ref for theme in themes for ref in theme.evidence_refs),
                    _context_ref(checked.market_context),
                )
            ),
        )
    except (TypeError, ValidationError, ValueError):
        raise UsDaySituationProjectionError from None


def _validate_strategy_input(source: UsStrategyDayInput, evaluated_at: dt.datetime) -> dt.date:
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        _fail()
    session_date = evaluated_at.astimezone(NEW_YORK).date()
    bounds = regular_session_bounds(session_date)
    opportunity = source.opportunity
    symbols = {item.symbol for item in opportunity.candidates}
    articles = source.articles
    article_by_id = {item.event_id: item for item in articles}
    observed_pairs = {
        (observation.event_id, snapshot.symbol)
        for snapshot in source.news_evidence.snapshots
        for observation in snapshot.observations
    }
    if (
        bounds is None
        or not bounds[0] < evaluated_at < bounds[1]
        or opportunity.valid_until < evaluated_at
        or not _fresh(opportunity.observed_at, evaluated_at, _MAX_SCANNER_AGE)
        or any(not item.complete for item in opportunity.source_coverage)
        or not articles
        or len(article_by_id) != len(articles)
        or not source.news_evidence.assessment.complete
        or not _fresh(source.news_evidence.assessment.assessed_at, evaluated_at, _MAX_NEWS_AGE)
        or tuple(item.symbol for item in source.news_evidence.snapshots) != tuple(sorted(symbols))
        or any(not item.coverage.complete for item in source.news_evidence.snapshots)
        or source.market_context.valid_until < evaluated_at
        or not _fresh(source.market_context.observed_at, evaluated_at, _MAX_CONTEXT_AGE)
        or {item.symbol for item in source.candidates} != symbols
        or any(
            not _fresh(item.quote.observed_at, evaluated_at, _MAX_QUOTE_AGE)
            or item.received_at > evaluated_at
            or item.bars[-1].timestamp
            != evaluated_at.astimezone(dt.UTC).replace(second=0, microsecond=0) - dt.timedelta(minutes=1)
            for item in source.candidates
        )
    ):
        _fail()
    for article in articles:
        if (
            article.created_at < bounds[0]
            or article.updated_at > evaluated_at
            or not set(article.symbols).intersection(symbols)
            or any(
                (article.event_id, symbol) not in observed_pairs
                for symbol in set(article.symbols).intersection(symbols)
            )
        ):
            _fail()
    return session_date


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
        or any(not set(item.symbols).intersection(symbols) for item in articles)
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
        or any(
            (item.event_id, symbol) not in observed_pairs
            for symbol in set(item.symbols).intersection(symbols)
        )
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
            item.evidence_ref != _quote_ref(item)
            or item.received_at > evaluated_at
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
            or item.evidence_refs != (_completed_bar_ref(item),)
            or not current_xnys_tick_at(item, evaluated_at)
            for item in ticks
        )
    ):
        _fail()
    return session_date


def _project_theme(
    articles: tuple[AlpacaNewsArticle, ...],
    opportunity: OpportunitySnapshot,
    observation_refs: dict[tuple[str, str], tuple[EvidenceRef, ...]],
    quotes: Mapping[str, UsQuotePolicyEvidence | UsStrategyDayCandidateEvidence],
    ticks: Mapping[str, UsForwardShadowTick | UsStrategyDayCandidateEvidence],
    evaluated_at: dt.datetime,
) -> ThemeMap:
    opportunity_symbols = {item.symbol for item in opportunity.candidates}
    symbols = tuple(
        sorted({symbol for item in articles for symbol in set(item.symbols).intersection(opportunity_symbols)})
    )
    keywords = tuple(sorted({word for item in articles for word in _keywords(item.headline)}))[:12]
    catalysts = tuple(
        CatalystEvidence(
            event_id=item.event_id,
            headline=item.headline,
            source=item.source,
            symbols=tuple(sorted(set(item.symbols).intersection(opportunity_symbols))),
            published_at=item.created_at,
            received_at=max(
                ref.observed_at
                for symbol in set(item.symbols).intersection(opportunity_symbols)
                for ref in observation_refs[(item.event_id, symbol)]
            ),
            evidence_refs=_refs(
                tuple(
                    ref
                    for symbol in set(item.symbols).intersection(opportunity_symbols)
                    for ref in observation_refs[(item.event_id, symbol)]
                )
            ),
        )
        for item in sorted(articles, key=lambda value: (value.created_at, value.event_id))
    )
    scanner_by_symbol = {item.symbol: item for item in opportunity.candidates}
    changes = {symbol: _change_pct(ticks[symbol]) for symbol in symbols}
    theme_bar_refs = tuple(_completed_bar_ref(ticks[item]) for item in symbols)
    raw_leaders: list[
        tuple[
            Decimal,
            Decimal,
            Decimal,
            str,
            ObservableFlow,
            tuple[EvidenceRef, ...],
            tuple[FlowInference, ...],
        ]
    ] = []
    for symbol in symbols:
        tick = ticks[symbol]
        if isinstance(tick, UsForwardShadowTick) and tick.candidate is None:
            _fail()
        flow_refs = _refs((_completed_bar_ref(tick), _quote_ref(quotes[symbol])))
        relative_volume = _relative_volume(tick)
        dollar_volume = _dollar_volume(tick)
        relative_strength = changes[symbol] - sum(changes.values(), Decimal(0)) / Decimal(len(changes))
        absorption_value = _breakout_absorption_proxy(tick, quotes[symbol])
        inferences = tuple(
            sorted(
                (
                    *(
                        (
                            FlowInference(
                                kind=FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY,
                                value=absorption_value,
                                rule="bar_quote_absorption_proxy_v1",
                                evidence_refs=flow_refs,
                            ),
                        )
                        if absorption_value is not None
                        else ()
                    ),
                    *(
                        (
                            FlowInference(
                                kind=FlowInferenceKind.CROSS_SYMBOL_RELATIVE_STRENGTH,
                                value=relative_strength,
                                rule="cross_symbol_relative_strength_v1",
                                evidence_refs=_refs(theme_bar_refs),
                            ),
                        )
                        if len(theme_bar_refs) >= 2
                        else ()
                    ),
                ),
                key=lambda item: item.kind.value,
            )
        )
        leader_refs = _refs(
            (
                *flow_refs,
                *(ref for item in inferences for ref in item.evidence_refs),
                _opportunity_ref(opportunity),
            )
        )
        flow = ObservableFlow(
            observation_kind=FlowObservationKind.OBSERVED,
            relative_volume=relative_volume,
            dollar_volume=dollar_volume,
            spread_bps=_spread_bps(quotes[symbol]),
            bid_size=_bid_size(quotes[symbol]),
            ask_size=_ask_size(quotes[symbol]),
            vwap_relation=_vwap_relation(tick),
            evidence_refs=flow_refs,
        )
        score = relative_volume + changes[symbol] + scanner_by_symbol[symbol].score
        raw_leaders.append((score, relative_volume, dollar_volume, symbol, flow, leader_refs, inferences))
    ordered = sorted(raw_leaders, key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    leaders = tuple(
        LeaderCandidate(
            symbol=item[3],
            rank=index,
            leader_score=item[0],
            flow=item[4],
            inferences=item[6],
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
    claim_events = tuple(
        CatalystClaimEvent(
            event_id=item.event_id,
            symbols=item.symbols,
            evidence_refs=item.evidence_refs,
        )
        for item in sorted(catalysts, key=lambda value: value.event_id)
    )
    claim_refs = _refs(tuple(ref for item in claim_events for ref in item.evidence_refs))
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
                kind=SituationClaimKind.SHARED_CURRENT_SESSION_CATALYST,
                events=claim_events,
                observation_kind=FlowObservationKind.OBSERVED,
                inference_rule=None,
                evidence_refs=claim_refs,
            ),
        ),
        evidence_refs=theme_refs,
    )


def _article_groups(
    articles: tuple[AlpacaNewsArticle, ...],
    requested_symbols: set[str],
) -> tuple[tuple[AlpacaNewsArticle, ...], ...]:
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
            current_symbols = set(current.symbols).intersection(requested_symbols)
            current_keywords = set(_keywords(current.headline))
            for other_id in sorted(remaining - component):
                other = by_id[other_id]
                linked_symbols = current_symbols.intersection(set(other.symbols).intersection(requested_symbols))
                if linked_symbols and current_keywords.intersection(_keywords(other.headline)):
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


def _quote_ref(quote: UsQuotePolicyEvidence | UsStrategyDayCandidateEvidence) -> EvidenceRef:
    if isinstance(quote, UsStrategyDayCandidateEvidence):
        source = next(item for item in quote.evidence_refs if item.namespace == "quote/alpaca-sip")
        return EvidenceRef(
            namespace="quote/snapshot",
            record_id=f"us-quote:{hashlib.sha256(source.canonical_id.encode()).hexdigest()}",
            observed_at=source.observed_at,
        )
    return EvidenceRef(
        namespace="quote/snapshot",
        record_id=quote.quote_id,
        observed_at=quote.provider_observed_at,
    )


def _completed_bar_ref(tick: UsForwardShadowTick | UsStrategyDayCandidateEvidence) -> EvidenceRef:
    if isinstance(tick, UsStrategyDayCandidateEvidence):
        return EvidenceRef(
            namespace="research/current_bar",
            record_id=completed_bar_id(_strategy_bar_frame(tick)),
            observed_at=tick.bars[-1].timestamp,
        )
    latest = tick.bars[-1]
    return EvidenceRef(
        namespace="research/current_bar",
        record_id=tick.completed_bar_id,
        observed_at=latest.timestamp,
    )


def _opportunity_ref(opportunity: OpportunitySnapshot) -> EvidenceRef:
    return EvidenceRef(
        namespace="scanner/opportunity",
        record_id=opportunity.opportunity_id,
        observed_at=opportunity.observed_at,
    )


def _context_ref(context: MarketContextSnapshot) -> EvidenceRef:
    return EvidenceRef(
        namespace="market/context",
        record_id=context.context_id,
        observed_at=context.observed_at,
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


def _change_pct(tick: UsForwardShadowTick | UsStrategyDayCandidateEvidence) -> Decimal:
    latest = tick.bars[-1]
    close = _decimal(latest.close)
    if isinstance(tick, UsStrategyDayCandidateEvidence):
        prior_close = _decimal(tick.bars[0].open)
    else:
        prior_close = _decimal(tick.bars[-1].prior_close)
    return (close - prior_close) / prior_close * Decimal(100)


def _dollar_volume(tick: UsForwardShadowTick | UsStrategyDayCandidateEvidence) -> Decimal:
    return sum((_decimal(item.close) * item.volume for item in tick.bars), Decimal(0))


def _relative_volume(tick: UsForwardShadowTick | UsStrategyDayCandidateEvidence) -> Decimal:
    """Latest minute volume / (latest average daily volume / fixed 390-minute XNYS session)."""
    latest = tick.bars[-1]
    if isinstance(tick, UsStrategyDayCandidateEvidence):
        prior = tick.bars[:-1]
        expected_minute_volume = sum((Decimal(item.volume) for item in prior), Decimal(0)) / Decimal(len(prior))
    else:
        expected_minute_volume = Decimal(tick.bars[-1].average_daily_volume) / _REGULAR_SESSION_MINUTES
    if expected_minute_volume == 0:
        return Decimal(0)
    return Decimal(latest.volume) / expected_minute_volume


def _vwap_relation(
    tick: UsForwardShadowTick | UsStrategyDayCandidateEvidence,
) -> Literal["above", "below", "crossing", "unavailable"]:
    total_volume = sum(item.volume for item in tick.bars)
    if total_volume == 0:
        return "unavailable"
    vwap = _dollar_volume(tick) / Decimal(total_volume)
    latest = tick.bars[-1]
    current = _decimal(latest.close) - vwap
    previous_close = tick.bars[-2].close if len(tick.bars) > 1 else latest.open
    previous = _decimal(previous_close) - vwap
    if current == 0 or previous == 0 or (current < 0 < previous) or (previous < 0 < current):
        return "crossing"
    return "above" if current > 0 else "below"


def _breakout_absorption_proxy(
    tick: UsForwardShadowTick | UsStrategyDayCandidateEvidence,
    quote: UsQuotePolicyEvidence | UsStrategyDayCandidateEvidence,
) -> Decimal | None:
    latest = tick.bars[-1]
    bar_range = _decimal(latest.high) - _decimal(latest.low)
    displayed_size = _bid_size(quote) + _ask_size(quote)
    if bar_range == 0 or displayed_size == 0:
        return None
    directional_fraction = abs(_decimal(latest.close) - _decimal(latest.open)) / bar_range
    remaining_fraction = max(Decimal(0), Decimal(1) - directional_fraction)
    balanced_size_fraction = Decimal(min(_bid_size(quote), _ask_size(quote))) / Decimal(displayed_size)
    return Decimal(latest.volume) * remaining_fraction * balanced_size_fraction


def _spread_bps(quote: UsQuotePolicyEvidence | UsStrategyDayCandidateEvidence) -> Decimal:
    return strategy_spread_bps(quote) if isinstance(quote, UsStrategyDayCandidateEvidence) else quote.spread_bps


def _bid_size(quote: UsQuotePolicyEvidence | UsStrategyDayCandidateEvidence) -> int:
    return quote.quote.bid_size if isinstance(quote, UsStrategyDayCandidateEvidence) else quote.bid_size


def _ask_size(quote: UsQuotePolicyEvidence | UsStrategyDayCandidateEvidence) -> int:
    return quote.quote.ask_size if isinstance(quote, UsStrategyDayCandidateEvidence) else quote.ask_size


def _strategy_bar_frame(evidence: UsStrategyDayCandidateEvidence) -> BarFrame:
    latest = evidence.bars[-1]
    prior = evidence.bars[:-1]
    average_minute_volume = max(1, sum(item.volume for item in prior) // len(prior))
    return BarFrame(
        symbol=evidence.symbol,
        timestamp=latest.timestamp,
        open=latest.open,
        high=latest.high,
        low=latest.low,
        close=latest.close,
        volume=latest.volume,
        prior_close=evidence.bars[0].open,
        average_daily_volume=average_minute_volume * 390,
        spread_bps=float(strategy_spread_bps(evidence)),
        catalyst="",
    )


def _fail() -> Never:
    raise UsDaySituationProjectionError


__all__ = (
    "UsDaySituationProjectionError",
    "project_us_day_situation",
    "project_us_strategy_day_situation",
)
