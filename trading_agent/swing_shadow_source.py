from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Final, Protocol, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.alpaca_bars import AlpacaDailyPageRequest
from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_models import AlpacaBarsPayload
from trading_agent.swing_shadow_models import (
    SwingDailyBar,
    SwingDailySource,
    swing_daily_source_key,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_MAX_PRODUCTION_SYMBOLS: Final = 50
_LOOKBACK_CALENDAR_DAYS: Final = 45
_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")


class _DailyBarsReader(Protocol):
    def fetch_daily_page(self, request: AlpacaDailyPageRequest) -> AlpacaBarsPayload: ...


class InvalidSwingDailySourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing 일봉 source를 안전하게 확인하지 못했습니다"


class _FixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    session_date: dt.date
    observed_at: dt.datetime
    universe_id: str
    symbols: tuple[str, ...]
    bars_file: str

    @model_validator(mode="after")
    def validate_manifest(self) -> _FixtureManifest:
        path = Path(self.bars_file)
        if (
            self.schema_version != 1
            or path.name != self.bars_file
            or path.suffix != ".json"
        ):
            raise ValueError("invalid swing fixture manifest")
        return self


def load_swing_daily_source(fixture_root: Path, *, session_date: dt.date) -> SwingDailySource:
    try:
        manifest = _FixtureManifest.model_validate_json(
            (fixture_root / "manifest.json").read_text(encoding="utf-8")
        )
        if manifest.session_date != session_date:
            raise InvalidSwingDailySourceError
        raw_bars = json.loads((fixture_root / manifest.bars_file).read_text(encoding="utf-8"))
        if not isinstance(raw_bars, list):
            raise InvalidSwingDailySourceError
        bars = tuple(
            SwingDailyBar.model_validate(raw | {"observed_at": manifest.observed_at})
            for raw in raw_bars
            if isinstance(raw, dict)
        )
        if len(bars) != len(raw_bars):
            raise InvalidSwingDailySourceError
        return SwingDailySource(
            session_date=manifest.session_date,
            observed_at=manifest.observed_at,
            universe_id=manifest.universe_id,
            symbols=manifest.symbols,
            bars=bars,
        )
    except InvalidSwingDailySourceError:
        raise
    except (OSError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
        raise InvalidSwingDailySourceError from None


def collect_current_swing_daily_source(
    *,
    bars_client: _DailyBarsReader,
    symbols: tuple[str, ...],
    session_date: dt.date,
    observed_at: dt.datetime,
    universe_id: str,
    now: dt.datetime,
) -> SwingDailySource:
    try:
        normalized = validate_current_swing_daily_collection(
            symbols=symbols,
            session_date=session_date,
            observed_at=observed_at,
            now=now,
        )
        raw_bars = _collect_bars(
            bars_client,
            symbols=normalized,
            session_date=session_date,
            observed_at=observed_at,
        )
        return SwingDailySource(
            session_date=session_date,
            observed_at=observed_at,
            universe_id=universe_id,
            symbols=normalized,
            bars=raw_bars,
        )
    except InvalidSwingDailySourceError:
        raise
    except (AlpacaApiError, OSError, TypeError, ValueError, ValidationError):
        raise InvalidSwingDailySourceError from None


def validate_current_swing_daily_collection(
    *,
    symbols: tuple[str, ...],
    session_date: dt.date,
    observed_at: dt.datetime,
    now: dt.datetime,
) -> tuple[str, ...]:
    normalized = tuple(sorted(set(symbols)))
    bounds = regular_session_bounds(session_date)
    if (
        not _aware(now)
        or not _aware(observed_at)
        or bounds is None
        or session_date != now.astimezone(NEW_YORK).date()
        or observed_at < bounds[1]
        or now < bounds[1]
        or observed_at > now
        or not 0 < len(normalized) <= _MAX_PRODUCTION_SYMBOLS
        or normalized != symbols
        or not all(_US_SYMBOL.fullmatch(symbol) for symbol in symbols)
    ):
        raise InvalidSwingDailySourceError
    return normalized


def _collect_bars(
    bars_client: _DailyBarsReader,
    *,
    symbols: tuple[str, ...],
    session_date: dt.date,
    observed_at: dt.datetime,
) -> tuple[SwingDailyBar, ...]:
    page_token: str | None = None
    seen_tokens: set[str] = set()
    bars: list[SwingDailyBar] = []
    first_date = session_date - dt.timedelta(days=_LOOKBACK_CALENDAR_DAYS)
    while True:
        payload = bars_client.fetch_daily_page(
            AlpacaDailyPageRequest(
                session_date=session_date,
                symbols=symbols,
                start_date=first_date,
                end_date=session_date,
                page_token=page_token,
            )
        )
        for symbol, symbol_bars in payload.bars.items():
            if symbol not in symbols:
                continue
            for bar in symbol_bars:
                bar_date = bar.timestamp.astimezone(NEW_YORK).date()
                if first_date <= bar_date <= session_date:
                    bars.append(
                        SwingDailyBar(
                            symbol=symbol,
                            session_date=bar_date,
                            observed_at=observed_at,
                            open=Decimal(str(bar.open)),
                            high=Decimal(str(bar.high)),
                            low=Decimal(str(bar.low)),
                            close=Decimal(str(bar.close)),
                            volume=bar.volume,
                        )
                    )
        page_token = payload.next_page_token
        if page_token is None:
            break
        if page_token in seen_tokens:
            raise InvalidSwingDailySourceError
        seen_tokens.add(page_token)
    return tuple(sorted(bars, key=lambda item: (item.symbol, item.session_date)))


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidSwingDailySourceError",
    "collect_current_swing_daily_source",
    "load_swing_daily_source",
    "swing_daily_source_key",
    "validate_current_swing_daily_collection",
)
