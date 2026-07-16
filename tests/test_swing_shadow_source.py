from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Never

import pytest

from trading_agent.alpaca_bars import AlpacaDailyPageRequest
from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_models import AlpacaBar, AlpacaBarsPayload
from trading_agent.swing_shadow_source import (
    InvalidSwingDailySourceError,
    collect_current_swing_daily_source,
    load_swing_daily_source,
    swing_daily_source_key,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

SESSION = dt.date(2026, 7, 15)
OBSERVED_AT = dt.datetime(2026, 7, 15, 16, 5, tzinfo=NEW_YORK)


def test_fixture_source_loads_exact_completed_symbol_histories(tmp_path: Path) -> None:
    fixture_root = _write_fixture(tmp_path, symbols=("ACME", "BETA"))

    source = load_swing_daily_source(fixture_root, session_date=SESSION)

    assert source.session_date == SESSION
    assert source.observed_at == OBSERVED_AT
    assert source.symbols == ("ACME", "BETA")
    assert len(source.bars_for("ACME")) == 21
    assert source.bars_for("ACME")[-1].session_date == SESSION
    assert source.source_key == swing_daily_source_key(source)


@pytest.mark.parametrize("fault", ("duplicate", "missing", "future", "naive"))
def test_fixture_source_fails_closed_for_invalid_evidence(
    tmp_path: Path,
    fault: str,
) -> None:
    fixture_root = _write_fixture(tmp_path, symbols=("ACME", "BETA"))
    manifest = _read_json(fixture_root / "manifest.json")
    bars = _read_json(fixture_root / "daily-bars.json")
    assert isinstance(manifest, dict)
    assert isinstance(bars, list)
    if fault == "duplicate":
        bars.append(dict(bars[0]))
    elif fault == "missing":
        manifest["symbols"] = ["ACME", "BETA", "MISSING"]
    elif fault == "future":
        bars[-1]["session_date"] = (SESSION + dt.timedelta(days=1)).isoformat()
    else:
        manifest["observed_at"] = OBSERVED_AT.replace(tzinfo=None).isoformat()
    _write_json(fixture_root / "manifest.json", manifest)
    _write_json(fixture_root / "daily-bars.json", bars)

    with pytest.raises(InvalidSwingDailySourceError):
        _ = load_swing_daily_source(fixture_root, session_date=SESSION)


def test_production_historical_or_preclose_request_opens_no_data_client() -> None:
    calls = 0

    class UnexpectedClient:
        def fetch_daily_page(self, request: AlpacaDailyPageRequest) -> Never:
            _ = request
            nonlocal calls
            calls += 1
            raise AssertionError("collector must reject before an Alpaca request")

    historical_now = dt.datetime(2026, 7, 16, 16, 5, tzinfo=NEW_YORK)
    with pytest.raises(InvalidSwingDailySourceError):
        _ = collect_current_swing_daily_source(
            bars_client=UnexpectedClient(),
            symbols=("ACME",),
            session_date=SESSION,
            observed_at=historical_now,
            universe_id="fixture-universe-v1",
            now=historical_now,
        )

    preclose_now = dt.datetime(2026, 7, 15, 15, 59, tzinfo=NEW_YORK)
    with pytest.raises(InvalidSwingDailySourceError):
        _ = collect_current_swing_daily_source(
            bars_client=UnexpectedClient(),
            symbols=("ACME",),
            session_date=SESSION,
            observed_at=preclose_now,
            universe_id="fixture-universe-v1",
            now=preclose_now,
        )

    assert calls == 0


def test_production_collects_bounded_completed_daily_bars() -> None:
    requests: list[AlpacaDailyPageRequest] = []
    sessions = _sessions_ending(SESSION, count=21)

    class FixtureClient:
        def fetch_daily_page(
            self,
            request: AlpacaDailyPageRequest,
        ) -> AlpacaBarsPayload:
            requests.append(request)
            return AlpacaBarsPayload(
                bars={
                    symbol: tuple(
                        AlpacaBar.model_validate(
                            {
                                "t": dt.datetime.combine(
                                    session_date,
                                    dt.time(0),
                                    tzinfo=NEW_YORK,
                                ),
                                "o": 10.0,
                                "h": 10.2,
                                "l": 9.9,
                                "c": 10.1,
                                "v": 100_000 + index,
                                "n": 100,
                            }
                        )
                        for index, session_date in enumerate(sessions)
                    )
                    for symbol in ("ACME", "BETA")
                },
            )

    source = collect_current_swing_daily_source(
        bars_client=FixtureClient(),
        symbols=("ACME", "BETA"),
        session_date=SESSION,
        observed_at=OBSERVED_AT,
        universe_id="fixture-universe-v1",
        now=OBSERVED_AT,
    )

    assert len(requests) == 1
    assert requests[0].start_date == SESSION - dt.timedelta(days=45)
    assert requests[0].end_date == SESSION
    assert source.bars_for("ACME")[-1].close == Decimal("10.1")
    assert source.bars_for("BETA")[-1].volume == 100_020


def test_production_hides_alpaca_api_error() -> None:
    class FailingClient:
        def fetch_daily_page(self, request: AlpacaDailyPageRequest) -> Never:
            _ = request
            raise AlpacaApiError(500, "PRIVATE_PROVIDER_DETAIL")

    with pytest.raises(InvalidSwingDailySourceError) as error:
        _ = collect_current_swing_daily_source(
            bars_client=FailingClient(),
            symbols=("ACME",),
            session_date=SESSION,
            observed_at=OBSERVED_AT,
            universe_id="fixture-universe-v1",
            now=OBSERVED_AT,
        )

    assert "PRIVATE_PROVIDER_DETAIL" not in str(error.value)


def _write_fixture(root: Path, *, symbols: tuple[str, ...]) -> Path:
    fixture_root = root / "fixture"
    fixture_root.mkdir()
    sessions = _sessions_ending(SESSION, count=21)
    bars = [
        {
            "symbol": symbol,
            "session_date": session_date.isoformat(),
            "open": "10.00",
            "high": "10.20",
            "low": "9.90",
            "close": "10.10",
            "volume": 100_000 + index,
        }
        for symbol in symbols
        for index, session_date in enumerate(sessions)
    ]
    _write_json(
        fixture_root / "manifest.json",
        {
            "schema_version": 1,
            "session_date": SESSION.isoformat(),
            "observed_at": OBSERVED_AT.isoformat(),
            "universe_id": "fixture-universe-v1",
            "symbols": list(symbols),
            "bars_file": "daily-bars.json",
        },
    )
    _write_json(fixture_root / "daily-bars.json", bars)
    return fixture_root


def _sessions_ending(end: dt.date, *, count: int) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = end
    while len(sessions) < count:
        if regular_session_bounds(current) is not None:
            sessions.append(current)
        current -= dt.timedelta(days=1)
    return tuple(reversed(sessions))


def _read_json(path: Path) -> dict[str, object] | list[dict[str, object]]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, (dict, list))
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
