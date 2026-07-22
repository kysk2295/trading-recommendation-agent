from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alpaca_bars import AlpacaDailyPageRequest
from trading_agent.alpaca_models import AlpacaBarsPayload
from trading_agent.systematic_regime_engine import SYSTEMATIC_REGIME_UNIVERSE
from trading_agent.systematic_regime_source import (
    InvalidSystematicDailySourceError,
    collect_current_systematic_daily_source,
    load_systematic_daily_source,
    validate_current_systematic_collection,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

SESSION = dt.date(2026, 7, 20)
BOUNDS = regular_session_bounds(SESSION)
assert BOUNDS is not None
OBSERVED_AT = BOUNDS[1] + dt.timedelta(minutes=5)


def test_fixture_loads_exact_aligned_systematic_universe(tmp_path: Path) -> None:
    # Given: 201 aligned completed sessions for the fixed ETF universe.
    fixture_root = _write_fixture(tmp_path, SYSTEMATIC_REGIME_UNIVERSE)

    # When: the read-only fixture source is loaded.
    source = load_systematic_daily_source(fixture_root, session_date=SESSION)

    # Then: all required symbols and completed sessions are preserved.
    assert source.symbols == SYSTEMATIC_REGIME_UNIVERSE
    assert len(source.bars_for("SPY")) == 201
    assert source.bars_for("SPY")[-1].session_date == SESSION


def test_fixture_rejects_a_partial_universe(tmp_path: Path) -> None:
    # Given: a structurally valid daily fixture missing one required ETF.
    fixture_root = _write_fixture(tmp_path, SYSTEMATIC_REGIME_UNIVERSE[:-1])

    # When/Then: systematic source parsing fails closed.
    with pytest.raises(InvalidSystematicDailySourceError):
        _ = load_systematic_daily_source(fixture_root, session_date=SESSION)


def test_fixture_rejects_bars_that_do_not_match_manifest_hash(tmp_path: Path) -> None:
    # Given: a valid fixture whose bars change after the manifest was written.
    fixture_root = _write_fixture(tmp_path, SYSTEMATIC_REGIME_UNIVERSE)
    bars_path = fixture_root / "bars.json"
    bars_path.write_bytes(bars_path.read_bytes() + b" ")

    # When/Then: source loading rejects the manifest/content mismatch.
    with pytest.raises(InvalidSystematicDailySourceError):
        _ = load_systematic_daily_source(fixture_root, session_date=SESSION)


def test_production_validation_rejects_preclose_before_transport() -> None:
    # Given: the current session has not completed.
    preclose = dt.datetime(2026, 7, 20, 15, 59, tzinfo=NEW_YORK)

    # When/Then: collection is rejected before a bars client or credential is needed.
    with pytest.raises(InvalidSystematicDailySourceError):
        _ = validate_current_systematic_collection(
            session_date=SESSION,
            observed_at=preclose,
            now=preclose,
        )


def test_production_collects_a_bounded_get_only_history() -> None:
    # Given: a typed bars reader with one complete page.
    requests: list[AlpacaDailyPageRequest] = []
    sessions = _sessions_ending(SESSION, 201)

    class FixtureClient:
        def fetch_daily_page(self, request: AlpacaDailyPageRequest) -> AlpacaBarsPayload:
            requests.append(request)
            return AlpacaBarsPayload.model_validate(
                {
                    "bars": {
                        symbol: tuple(
                            {
                                "t": dt.datetime.combine(session, dt.time(12), tzinfo=dt.UTC),
                                "o": 100.0,
                                "h": 101.0,
                                "l": 99.0,
                                "c": 100.5,
                                "v": 1_000_000 + index,
                                "n": 100,
                            }
                            for index, session in enumerate(sessions)
                        )
                        for symbol in SYSTEMATIC_REGIME_UNIVERSE
                    }
                }
            )

    # When: current completed-day history is collected.
    source = collect_current_systematic_daily_source(
        bars_client=FixtureClient(),
        session_date=SESSION,
        observed_at=OBSERVED_AT,
        now=OBSERVED_AT,
    )

    # Then: the adapter issued one bounded daily request and built the exact source.
    assert len(requests) == 1
    assert requests[0].symbols == SYSTEMATIC_REGIME_UNIVERSE
    assert requests[0].start_date == SESSION - dt.timedelta(days=430)
    assert requests[0].end_date == SESSION
    assert source.bars_for("SPY")[-1].close == Decimal("100.5")


def test_production_collection_caps_unique_pagination_tokens() -> None:
    # Given: a provider that never terminates but emits a fresh token each time.
    requests: list[AlpacaDailyPageRequest] = []

    class EndlessClient:
        def fetch_daily_page(self, request: AlpacaDailyPageRequest) -> AlpacaBarsPayload:
            requests.append(request)
            token = None if len(requests) == 25 else f"token-{len(requests)}"
            return AlpacaBarsPayload(bars={}, next_page_token=token)

    # When/Then: the adapter stops at its own budget instead of trusting unique tokens forever.
    with pytest.raises(InvalidSystematicDailySourceError):
        _ = collect_current_systematic_daily_source(
            bars_client=EndlessClient(),
            session_date=SESSION,
            observed_at=OBSERVED_AT,
            now=OBSERVED_AT,
        )
    assert len(requests) < 25


def _write_fixture(root: Path, symbols: tuple[str, ...]) -> Path:
    fixture = root / "fixture"
    fixture.mkdir()
    sessions = _sessions_ending(SESSION, 201)
    bars = [
        {
            "symbol": symbol,
            "session_date": session.isoformat(),
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100.5",
            "volume": 1_000_000 + index,
        }
        for symbol in symbols
        for index, session in enumerate(sessions)
    ]
    bars_payload = json.dumps(bars).encode()
    (fixture / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_date": SESSION.isoformat(),
                "observed_at": OBSERVED_AT.isoformat(),
                "universe_id": "us_systematic_regime_etf_v1",
                "symbols": symbols,
                "bars_file": "bars.json",
                "bars_sha256": hashlib.sha256(bars_payload).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    (fixture / "bars.json").write_bytes(bars_payload)
    return fixture


def _sessions_ending(end: dt.date, count: int) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = end
    while len(sessions) < count:
        if regular_session_bounds(current) is not None:
            sessions.append(current)
        current -= dt.timedelta(days=1)
    return tuple(reversed(sessions))
