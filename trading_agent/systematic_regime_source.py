from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Final, Protocol, override

from pydantic import ValidationError

from trading_agent.alpaca_bars import AlpacaDailyPageRequest
from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_models import AlpacaBarsPayload
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.swing_shadow_source import InvalidSwingDailySourceError, load_swing_daily_source
from trading_agent.systematic_regime_engine import SYSTEMATIC_REGIME_UNIVERSE
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

SYSTEMATIC_LOOKBACK_CALENDAR_DAYS: Final = 430
SYSTEMATIC_UNIVERSE_ID: Final = "us_systematic_regime_etf_v1"
_MINIMUM_SESSIONS: Final = 201


class _DailyBarsReader(Protocol):
    def fetch_daily_page(self, request: AlpacaDailyPageRequest) -> AlpacaBarsPayload: ...


class InvalidSystematicDailySourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US systematic completed-daily source is invalid"


def load_systematic_daily_source(
    fixture_root: Path,
    *,
    session_date: dt.date,
) -> SwingDailySource:
    try:
        return _validate_source(
            load_swing_daily_source(fixture_root, session_date=session_date)
        )
    except (InvalidSwingDailySourceError, InvalidSystematicDailySourceError):
        raise InvalidSystematicDailySourceError from None


def validate_current_systematic_collection(
    *,
    session_date: dt.date,
    observed_at: dt.datetime,
    now: dt.datetime,
) -> None:
    bounds = regular_session_bounds(session_date)
    if (
        not _aware(now)
        or not _aware(observed_at)
        or bounds is None
        or session_date != now.astimezone(NEW_YORK).date()
        or observed_at < bounds[1]
        or observed_at > now
    ):
        raise InvalidSystematicDailySourceError


def collect_current_systematic_daily_source(
    *,
    bars_client: _DailyBarsReader,
    session_date: dt.date,
    observed_at: dt.datetime,
    now: dt.datetime,
) -> SwingDailySource:
    try:
        validate_current_systematic_collection(
            session_date=session_date,
            observed_at=observed_at,
            now=now,
        )
        bars = _collect_bars(
            bars_client,
            session_date=session_date,
            observed_at=observed_at,
        )
        return _validate_source(
            SwingDailySource(
                session_date=session_date,
                observed_at=observed_at,
                universe_id=SYSTEMATIC_UNIVERSE_ID,
                symbols=SYSTEMATIC_REGIME_UNIVERSE,
                bars=bars,
            )
        )
    except InvalidSystematicDailySourceError:
        raise
    except (AlpacaApiError, ArithmeticError, TypeError, ValidationError, ValueError):
        raise InvalidSystematicDailySourceError from None


def _collect_bars(
    bars_client: _DailyBarsReader,
    *,
    session_date: dt.date,
    observed_at: dt.datetime,
) -> tuple[SwingDailyBar, ...]:
    first_date = session_date - dt.timedelta(days=SYSTEMATIC_LOOKBACK_CALENDAR_DAYS)
    page_token: str | None = None
    seen_tokens: set[str] = set()
    bars: list[SwingDailyBar] = []
    while True:
        payload = bars_client.fetch_daily_page(
            AlpacaDailyPageRequest(
                session_date=session_date,
                symbols=SYSTEMATIC_REGIME_UNIVERSE,
                start_date=first_date,
                end_date=session_date,
                page_token=page_token,
            )
        )
        for symbol, items in payload.bars.items():
            if symbol not in SYSTEMATIC_REGIME_UNIVERSE:
                raise InvalidSystematicDailySourceError
            for item in items:
                bar_date = item.timestamp.astimezone(NEW_YORK).date()
                if first_date <= bar_date <= session_date:
                    bars.append(
                        SwingDailyBar(
                            symbol=symbol,
                            session_date=bar_date,
                            observed_at=observed_at,
                            open=Decimal(str(item.open)),
                            high=Decimal(str(item.high)),
                            low=Decimal(str(item.low)),
                            close=Decimal(str(item.close)),
                            volume=item.volume,
                        )
                    )
        page_token = payload.next_page_token
        if page_token is None:
            return tuple(sorted(bars, key=lambda item: (item.symbol, item.session_date)))
        if page_token in seen_tokens:
            raise InvalidSystematicDailySourceError
        seen_tokens.add(page_token)


def _validate_source(source: SwingDailySource) -> SwingDailySource:
    histories = tuple(source.bars_for(symbol) for symbol in SYSTEMATIC_REGIME_UNIVERSE)
    dates = tuple(bar.session_date for bar in source.bars_for("SPY"))
    if (
        source.symbols != SYSTEMATIC_REGIME_UNIVERSE
        or source.universe_id != SYSTEMATIC_UNIVERSE_ID
        or len(dates) < _MINIMUM_SESSIONS
        or dates[-1] != source.session_date
        or any(tuple(bar.session_date for bar in history) != dates for history in histories)
    ):
        raise InvalidSystematicDailySourceError
    return source


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "SYSTEMATIC_LOOKBACK_CALENDAR_DAYS",
    "SYSTEMATIC_UNIVERSE_ID",
    "InvalidSystematicDailySourceError",
    "collect_current_systematic_daily_source",
    "load_systematic_daily_source",
    "validate_current_systematic_collection",
)
