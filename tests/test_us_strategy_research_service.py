from __future__ import annotations

import datetime as dt
import json
import plistlib
import shutil
import stat
from pathlib import Path

import httpx2
import pytest

import run_us_strategy_research_live_cycle as live_cycle
import run_us_strategy_research_service as service
from trading_agent.us_strategy_day_input import UsStrategyDayInput
from trading_agent.us_strategy_research_service_config import (
    US_STRATEGY_RESEARCH_SERVICE_LABEL,
    load_us_strategy_research_service_config,
    verify_us_strategy_research_launch_agent,
)

ROOT = Path(__file__).resolve().parents[1]


def test_provision_writes_private_secret_free_two_minute_launch_agent(tmp_path: Path) -> None:
    config_path = tmp_path / "private" / f"service-{'a' * 40}.json"
    plist_path = tmp_path / "private" / "service.plist"

    result = service.main(_provision_args(tmp_path, config_path, plist_path))

    config = load_us_strategy_research_service_config(config_path)
    payload = plistlib.loads(plist_path.read_bytes())
    assert result == 0
    assert config.label == US_STRATEGY_RESEARCH_SERVICE_LABEL
    assert payload["StartInterval"] == 120
    assert payload["RunAtLoad"] is True
    assert "KeepAlive" not in payload
    assert "EnvironmentVariables" not in payload
    assert payload["ProgramArguments"][-3:] == ["tick", "--config", str(config_path)]
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600
    assert verify_us_strategy_research_launch_agent(config_path, plist_path).ready


def test_closed_session_exits_before_credentials_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        live_cycle,
        "load_alpaca_credentials",
        lambda *_: calls.append("credentials") or pytest.fail("credentials must not be read"),
    )

    result = live_cycle.main(
        _cycle_args(tmp_path),
        clock=lambda: dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.UTC),
    )

    assert result == 0
    assert calls == []
    assert not (tmp_path / "live").exists()


def test_service_help_and_bad_config_fail_without_market_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(live_cycle, "main", lambda argv, **_: calls.append(tuple(argv)) or 0)

    assert service.main(("--help",)) == 0
    assert service.main(("tick", "--config", str(tmp_path / "missing.json"))) == 2
    assert calls == []


def test_live_cycle_emits_producer_owned_day_input_from_get_only_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = dt.datetime(2026, 8, 19, 13, 42, tzinfo=dt.UTC)
    credentials = tmp_path / "alpaca.env"
    credentials.write_text("APCA_API_KEY_ID=test\nAPCA_API_SECRET_KEY=test\n")
    credentials.chmod(0o600)
    requests: list[httpx2.Request] = []

    def market_handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/v2/stocks/bars":
            bars = {
                symbol: [
                    {
                        "t": f"2026-08-19T13:{minute}:00Z",
                        "o": 100,
                        "h": close + 1,
                        "l": 99,
                        "c": close,
                        "v": 1000,
                        "n": 10,
                        "vw": close,
                    }
                    for minute, close in ((39, 100), (40, 103 if symbol == "DIA" else 101))
                ]
                for symbol in live_cycle.SYMBOLS
            }
            return httpx2.Response(200, json={"bars": bars, "next_page_token": None})
        quotes = {symbol: {"ap": 103.01, "bp": 102.99, "t": "2026-08-19T13:41:58Z"} for symbol in live_cycle.SYMBOLS}
        return httpx2.Response(200, json={"quotes": quotes})

    def news_handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = {
            "news": [
                {
                    "id": 1,
                    "headline": "Dow leaders extend verified session momentum",
                    "source": "benzinga",
                    "symbols": ["DIA"],
                    "created_at": "2026-08-19T13:35:00Z",
                    "updated_at": "2026-08-19T13:36:00Z",
                    "url": "https://example.invalid/news/1",
                }
            ],
            "next_page_token": None,
        }
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            stream=httpx2.ByteStream(json.dumps(payload).encode()),
        )

    market = httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(market_handler),
    )
    news = httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(news_handler),
        follow_redirects=False,
    )
    monkeypatch.setattr(live_cycle, "create_alpaca_client", lambda: market)
    monkeypatch.setattr(live_cycle, "create_alpaca_news_http_client", lambda: news)

    result = live_cycle.main(
        (
            "--credentials-path",
            str(credentials),
            "--live-session-root",
            str(tmp_path / "live"),
            "--market-context-root",
            str(tmp_path / "context"),
            "--day-source-root",
            str(tmp_path / "day-source"),
            "--news-database",
            str(tmp_path / "news.sqlite3"),
        ),
        clock=lambda: now,
    )

    artifact = next((tmp_path / "day-source/20260819").glob("*.day-input.json"))
    day_input = UsStrategyDayInput.model_validate_json(artifact.read_text())
    assert result == 0
    assert day_input.opportunity.candidates[0].symbol == "DIA"
    assert day_input.articles[0].headline.startswith("Dow leaders")
    assert [request.method for request in requests] == ["GET", "GET", "GET"]


def _provision_args(tmp_path: Path, config_path: Path, plist_path: Path) -> tuple[str, ...]:
    uv = Path(shutil.which("uv") or "/bin/false").resolve()
    return (
        "provision",
        "--project-root",
        str(ROOT),
        "--uv-path",
        str(uv),
        "--expected-commit",
        "a" * 40,
        "--credentials-path",
        str(tmp_path / "alpaca.env"),
        "--live-session-root",
        str(tmp_path / "live"),
        "--market-context-root",
        str(tmp_path / "context"),
        "--day-source-root",
        str(tmp_path / "day-source"),
        "--news-database",
        str(tmp_path / "news.sqlite3"),
        "--runtime-output-root",
        str(tmp_path / "reports"),
        "--config",
        str(config_path),
        "--plist",
        str(plist_path),
    )


def _cycle_args(tmp_path: Path) -> tuple[str, ...]:
    return (
        "--credentials-path",
        str(tmp_path / "alpaca.env"),
        "--live-session-root",
        str(tmp_path / "live"),
        "--market-context-root",
        str(tmp_path / "context"),
        "--day-source-root",
        str(tmp_path / "day-source"),
        "--news-database",
        str(tmp_path / "news.sqlite3"),
    )
