from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Final, Protocol, override

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.alpaca_bars import AlpacaDailyPageRequest
from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_models import AlpacaBarsPayload
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.systematic_regime_engine import SYSTEMATIC_REGIME_UNIVERSE
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

SYSTEMATIC_LOOKBACK_CALENDAR_DAYS: Final = 430
SYSTEMATIC_UNIVERSE_ID: Final = "us_systematic_regime_etf_v1"
_MINIMUM_SESSIONS: Final = 201
_MAX_PAGES: Final = 20
_MAX_BARS: Final = 4_000


class _SystematicFixtureManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    session_date: dt.date
    observed_at: dt.datetime
    universe_id: str
    symbols: tuple[str, ...]
    bars_file: str
    bars_sha256: str


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
        manifest = _SystematicFixtureManifest.model_validate_json(
            (fixture_root / "manifest.json").read_text(encoding="utf-8")
        )
        bars_path = fixture_root / manifest.bars_file
        bars_payload = bars_path.read_bytes()
        raw_bars = json.loads(bars_payload)
        if (
            manifest.schema_version != 1
            or manifest.session_date != session_date
            or Path(manifest.bars_file).name != manifest.bars_file
            or Path(manifest.bars_file).suffix != ".json"
            or len(manifest.bars_sha256) != 64
            or hashlib.sha256(bars_payload).hexdigest() != manifest.bars_sha256
            or not isinstance(raw_bars, list)
            or any(not isinstance(raw, dict) for raw in raw_bars)
        ):
            raise InvalidSystematicDailySourceError
        bars = tuple(
            SwingDailyBar.model_validate(raw | {"observed_at": manifest.observed_at})
            for raw in raw_bars
        )
        return _validate_source(
            SwingDailySource(
                session_date=manifest.session_date,
                observed_at=manifest.observed_at,
                universe_id=manifest.universe_id,
                symbols=manifest.symbols,
                bars=bars,
            )
        )
    except InvalidSystematicDailySourceError:
        raise
    except (OSError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
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
    for _ in range(_MAX_PAGES):
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
                    if len(bars) > _MAX_BARS:
                        raise InvalidSystematicDailySourceError
        page_token = payload.next_page_token
        if page_token is None:
            return tuple(sorted(bars, key=lambda item: (item.symbol, item.session_date)))
        if page_token in seen_tokens:
            raise InvalidSystematicDailySourceError
        seen_tokens.add(page_token)
    raise InvalidSystematicDailySourceError


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
