from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import httpx2

from tests.alpaca_sip_runtime_fleet_fixtures import NOW, decision, opportunity
from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_sip_profile_materializer import AlpacaSipProfileMaterializer
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.us_runtime_fleet_cycle import bind_runtime_profiles
from trading_agent.us_runtime_policy_scope import PreparedRuntimePolicyScope


def test_desired_profiles_materialize_once_then_replay_without_historical_http(
    tmp_path: Path,
) -> None:
    calls: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return _historical_response(request)

    materializer = AlpacaSipProfileMaterializer(
        AlpacaSipMinutePageClient(
            httpx2.Client(
                base_url=ALPACA_DATA_URL,
                transport=httpx2.MockTransport(respond),
                follow_redirects=False,
            ),
            AlpacaCredentials("fixture-key", "fixture-secret"),
            clock=lambda: NOW,
        ),
        tmp_path / "profiles",
    )
    scope = PreparedRuntimePolicyScope(opportunity(), decision(), 35)

    first = materializer.materialize(scope)
    first_call_count = len(calls)
    second = materializer.materialize(scope)
    prepared = bind_runtime_profiles(scope, second)

    assert second == first
    assert first_call_count == 40
    assert len(calls) == first_call_count
    assert tuple(item.instrument_id for item in prepared.requests) == (
        "alpaca:asset-aaa",
        "alpaca:asset-bbb",
    )
    assert all(item.volume_profile.through_minute == 35 for item in prepared.requests)
    assert all(request.method == "GET" for request in calls)
    assert all(request.url.host == "data.alpaca.markets" for request in calls)
    owner_dirs = tuple(path for path in (tmp_path / "profiles").iterdir() if path.is_dir())
    assert len(owner_dirs) == 2
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in owner_dirs)


def _historical_response(request: httpx2.Request) -> httpx2.Response:
    opened = dt.datetime.fromisoformat(request.url.params["start"])
    closed = dt.datetime.fromisoformat(request.url.params["end"])
    symbol = request.url.params["symbols"]
    count = int((closed - opened) / dt.timedelta(minutes=1)) + 1
    bars = tuple(
        {
            "t": (opened + dt.timedelta(minutes=index)).isoformat(),
            "o": 100.0,
            "h": 101.0,
            "l": 99.0,
            "c": 100.0,
            "v": 1000,
            "n": 10,
            "vw": 100.0,
        }
        for index in range(count)
    )
    return httpx2.Response(200, json={"bars": {symbol: bars}, "next_page_token": None})
