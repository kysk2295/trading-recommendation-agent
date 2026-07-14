from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials, KisMinuteBar
from trading_agent.kis_auth import KisMode, load_kis_credentials
from trading_agent.kis_daily import fetch_daily_context
from trading_agent.kis_provider import (
    fetch_updown_ranking,
    fetch_volume_ranking,
    ranking_to_bar_inputs,
)


def test_load_kis_credentials_selects_mode_without_exposing_secret(
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "kis.env"
    secret_file.write_text(
        "KIS_LIVE_APP_KEY=live-key\n"
        "KIS_LIVE_APP_SECRET=live-secret\n"
        "KIS_PAPER_APP_KEY=paper-key\n"
        "KIS_PAPER_APP_SECRET=paper-secret\n",
        encoding="utf-8",
    )
    secret_file.chmod(0o600)

    credentials = load_kis_credentials(KisMode.LIVE, secret_file)

    assert credentials == KisCredentials("live-key", "live-secret")
    assert "live-secret" not in repr(credentials)


def test_load_kis_credentials_rejects_world_readable_secret_file(
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "kis.env"
    secret_file.write_text("KIS_LIVE_APP_KEY=x\n", encoding="utf-8")
    secret_file.chmod(0o644)

    with pytest.raises(PermissionError, match="600"):
        _ = load_kis_credentials(KisMode.LIVE, secret_file)


def test_fetch_volume_ranking_parses_read_only_us_candidates() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리",
                "output2": [
                    {
                        "excd": "NAS",
                        "symb": "DEMO",
                        "name": "Demo",
                        "last": "10.50",
                        "rate": "5.00",
                        "pask": "10.51",
                        "pbid": "10.49",
                        "tvol": "500000",
                        "tamt": "5250000",
                        "a_tvol": "200000",
                        "rank": "1",
                    }
                ],
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        candidates = fetch_volume_ranking(
            client,
            KisCredentials("key", "secret"),
            "token",
            "NAS",
        )

    assert candidates[0].symbol == "DEMO"
    assert candidates[0].average_daily_volume == 200_000
    assert candidates[0].spread_bps == pytest.approx(19.047619)
    assert requests[0].url.path.endswith("/ranking/trade-vol")
    assert requests[0].headers["tr_id"] == "HHDFS76310010"


def test_fetch_updown_ranking_accepts_rows_without_average_volume() -> None:
    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리",
                "output2": [
                    {
                        "excd": "NYS",
                        "symb": "MOVE",
                        "name": "Mover",
                        "last": "20.00",
                        "rate": "10.00",
                        "pask": "20.02",
                        "pbid": "19.98",
                        "tvol": "100000",
                        "rank": "1",
                    }
                ],
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        candidates = fetch_updown_ranking(client, KisCredentials("key", "secret"), "token", "NYS")

    assert candidates[0].symbol == "MOVE"
    assert candidates[0].average_daily_volume == 100_000
    assert candidates[0].dollar_volume == 2_000_000.0


def test_ranking_to_bar_inputs_keeps_only_latest_regular_session() -> None:
    candidate_payload = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output2": [
            {
                "excd": "NAS",
                "symb": "DEMO",
                "name": "Demo",
                "last": "10.50",
                "rate": "5.00",
                "pask": "10.51",
                "pbid": "10.49",
                "tvol": "500000",
                "tamt": "5250000",
                "a_tvol": "200000",
                "rank": "1",
            }
        ],
    }

    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=json.dumps(candidate_payload))

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        candidate = fetch_volume_ranking(client, KisCredentials("key", "secret"), "token", "NAS")[0]
    new_york = ZoneInfo("America/New_York")
    bars = (
        KisMinuteBar(
            dt.datetime(2026, 7, 10, 9, 29, tzinfo=new_york),
            dt.datetime(2026, 7, 10, 22, 29, tzinfo=ZoneInfo("Asia/Seoul")),
            10.4,
            10.5,
            10.3,
            10.4,
            100,
            1040,
        ),
        KisMinuteBar(
            dt.datetime(2026, 7, 10, 9, 30, tzinfo=new_york),
            dt.datetime(2026, 7, 10, 22, 30, tzinfo=ZoneInfo("Asia/Seoul")),
            10.5,
            10.6,
            10.4,
            10.55,
            200,
            2110,
        ),
    )

    inputs = ranking_to_bar_inputs(candidate, bars)

    assert len(inputs) == 1
    assert inputs[0].timestamp.hour == 9
    assert inputs[0].timestamp.minute == 30
    assert inputs[0].prior_close == pytest.approx(10.0)


def test_fetch_daily_context_excludes_current_session_from_baseline() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리",
                "output2": [
                    {"xymd": "20260710", "clos": "10.50", "tvol": "500000"},
                    {"xymd": "20260709", "clos": "10.00", "tvol": "200000"},
                    {"xymd": "20260708", "clos": "9.80", "tvol": "100000"},
                ],
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        context = fetch_daily_context(
            client,
            KisCredentials("key", "secret"),
            "token",
            exchange="NAS",
            symbol="DEMO",
            session_date=dt.date(2026, 7, 10),
        )

    assert context.prior_close == 10.0
    assert context.average_daily_volume == 150_000
    assert requests[0].url.path.endswith("/quotations/dailyprice")
    assert requests[0].headers["tr_id"] == "HHDFS76240000"
