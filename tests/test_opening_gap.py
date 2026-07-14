from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from scr_backtest.kis_intraday import KisCredentials, KisSession
from trading_agent.kis_provider import KisRankedStock
from trading_agent.market_risk import (
    HaltSnapshot,
    MarketRiskConfig,
    MarketRiskGate,
    MarketRiskScreen,
)
from trading_agent.opening_gap import (
    OpeningGapCapture,
    OpeningGapCycleStatus,
    OpeningGapRuntime,
    capture_opening_gaps,
)
from trading_agent.opening_gap_checkpoint import repair_transient_cycle_rows


def test_capture_collects_all_risk_eligible_candidates_and_persists_gaps(
    tmp_path: Path,
) -> None:
    observed_at = dt.datetime(
        2026,
        7,
        10,
        10,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    selected = _stock("UP", 10.0, 0.10)
    beyond_limit = _stock("DOWN", 20.0, 0.08)
    rejected = _stock("WIDE", 5.0, 0.20, bid=4.0, ask=6.0)
    screen = MarketRiskGate(
        HaltSnapshot(observed_at, frozenset()),
        MarketRiskConfig(),
    ).screen(((selected, beyond_limit, rejected),), limit=1)
    requests: list[httpx2.Request] = []
    waits: list[float] = []

    def handle_request(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        symbol = request.url.params["SYMB"]
        session_open = "11.0" if symbol == "UP" else "18.0"
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리",
                "output": {
                    "base": "10.0" if symbol == "UP" else "20.0",
                    "open": session_open,
                    "last": session_open,
                    "tvol": "100000",
                    "pvol": "200000",
                },
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle_request),
    ) as client:
        result = capture_opening_gaps(
            client,
            OpeningGapCapture(
                tmp_path,
                KisSession(KisCredentials("key", "secret"), "token"),
                observed_at,
                screen,
            ),
            OpeningGapRuntime(lambda: observed_at + dt.timedelta(seconds=1), waits.append),
        )

    assert result.status is OpeningGapCycleStatus.COLLECTED
    assert tuple(request.url.params["SYMB"] for request in requests) == (
        "UP",
        "DOWN",
    )
    assert waits == [0.08]
    with (tmp_path / "kis_opening_gap_snapshots.csv").open(
        encoding="utf-8",
        newline="",
    ) as csv_handle:
        rows = tuple(csv.DictReader(csv_handle))
    assert tuple(row["symbol"] for row in rows) == ("UP", "DOWN")
    assert tuple(float(row["opening_gap_pct"]) for row in rows) == (0.1, -0.1)
    assert tuple(row["status"] for row in rows) == ("ok", "ok")


def test_capture_reuses_success_after_one_inline_server_retry(
    tmp_path: Path,
) -> None:
    observed_at = dt.datetime(
        2026,
        7,
        10,
        10,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    up = _stock("UP", 10.0, 0.10)
    retry = _stock("RETRY", 12.0, 0.09)
    new = _stock("NEW", 8.0, 0.08)
    request_symbols: list[str] = []
    retry_attempts = 0

    def handle_request(request: httpx2.Request) -> httpx2.Response:
        nonlocal retry_attempts
        symbol = request.url.params["SYMB"]
        request_symbols.append(symbol)
        if symbol == "RETRY":
            retry_attempts += 1
            if retry_attempts == 1:
                return httpx2.Response(500, text="temporary failure")
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리",
                "output": {
                    "base": "10.0",
                    "open": "11.0",
                    "last": "11.0",
                    "tvol": "100000",
                    "pvol": "200000",
                },
            },
        )

    first_screen = MarketRiskGate(
        HaltSnapshot(observed_at, frozenset()),
        MarketRiskConfig(),
    ).screen(((up, retry),), limit=10)
    second_at = observed_at.astimezone(ZoneInfo("Asia/Seoul")) + dt.timedelta(hours=2)
    second_screen = MarketRiskGate(
        HaltSnapshot(second_at, frozenset()),
        MarketRiskConfig(),
    ).screen(((up, retry, new),), limit=10)
    runtime = OpeningGapRuntime(lambda: second_at, lambda _: None)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle_request),
    ) as client:
        first = capture_opening_gaps(
            client,
            OpeningGapCapture(
                tmp_path,
                KisSession(KisCredentials("key", "secret"), "token"),
                observed_at,
                first_screen,
            ),
            runtime,
        )
        second = capture_opening_gaps(
            client,
            OpeningGapCapture(
                tmp_path,
                KisSession(KisCredentials("key", "secret"), "token"),
                second_at,
                second_screen,
            ),
            runtime,
        )

    assert request_symbols == ["UP", "RETRY", "RETRY", "NEW"]
    assert first.reused_success_count == 0
    assert first.attempted_count == 2
    assert second.eligible_count == 3
    assert second.reused_success_count == 2
    assert second.attempted_count == 1
    assert second.new_success_count == 1
    assert second.success_count == 3
    assert second.failure_count == 0
    with (tmp_path / "kis_opening_gap_snapshots.csv").open(
        encoding="utf-8",
        newline="",
    ) as csv_handle:
        snapshots = tuple(csv.DictReader(csv_handle))
    assert tuple((row["symbol"], row["status"]) for row in snapshots) == (
        ("UP", "ok"),
        ("RETRY", "ok"),
        ("NEW", "ok"),
    )
    with (tmp_path / "kis_opening_gap_cycles.csv").open(
        encoding="utf-8",
        newline="",
    ) as csv_handle:
        cycle_reader = csv.DictReader(csv_handle)
        cycles = tuple(cycle_reader)
    assert cycle_reader.fieldnames == [
        "ranking_observed_at",
        "status",
        "eligible_count",
        "success_count",
        "failure_count",
    ]
    assert tuple(row["success_count"] for row in cycles) == ("2", "3")
    with (tmp_path / "kis_opening_gap_reuse_cycles.csv").open(
        encoding="utf-8",
        newline="",
    ) as csv_handle:
        reuse_cycles = tuple(csv.DictReader(csv_handle))
    assert tuple(row["reused_success_count"] for row in reuse_cycles) == (
        "0",
        "2",
    )
    assert tuple(row["attempted_count"] for row in reuse_cycles) == ("2", "1")
    assert tuple(row["new_success_count"] for row in reuse_cycles) == ("2", "1")


def test_capture_records_market_closed_without_requesting_stale_open(
    tmp_path: Path,
) -> None:
    observed_at = dt.datetime(
        2026,
        7,
        12,
        10,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    screen = _screen(observed_at, _stock("CLOSED", 10.0, 0.1))

    def reject_request(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(request.url)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(reject_request),
    ) as client:
        result = capture_opening_gaps(
            client,
            OpeningGapCapture(
                tmp_path,
                KisSession(KisCredentials("key", "secret"), "token"),
                observed_at,
                screen,
            ),
        )

    assert result.status is OpeningGapCycleStatus.MARKET_CLOSED
    assert not (tmp_path / "kis_opening_gap_snapshots.csv").exists()
    with (tmp_path / "kis_opening_gap_cycles.csv").open(
        encoding="utf-8",
        newline="",
    ) as csv_handle:
        rows = tuple(csv.DictReader(csv_handle))
    assert rows[0]["status"] == "market_closed"
    assert rows[0]["eligible_count"] == "1"
    assert rows[0]["success_count"] == "0"


def test_repair_transient_cycle_rows_preserves_legacy_schema_and_is_idempotent(
    tmp_path: Path,
) -> None:
    cycle_path = tmp_path / "kis_opening_gap_cycles.csv"
    with cycle_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "ranking_observed_at",
                "status",
                "eligible_count",
                "success_count",
                "failure_count",
            )
        )
        writer.writerow(("2026-07-13T22:39:20+09:00", "collected", 64, 61, 3))
        writer.writerow(("2026-07-13T22:40:31+09:00", "collected", 73, 63, 10, 8, 2))
        writer.writerow(("2026-07-13T22:41:37+09:00", "collected", 78, 70, 8, 8, 0))
        writer.writerow(("2026-07-13T22:42:41+09:00", "collected", 71, 71, 0))
    reuse_path = tmp_path / "kis_opening_gap_reuse_cycles.csv"
    with reuse_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "ranking_observed_at",
                "status",
                "eligible_count",
                "reused_success_count",
                "attempted_count",
                "new_success_count",
                "failure_count",
            )
        )
        writer.writerow(("2026-07-13T22:42:41+09:00", "collected", 71, 70, 1, 1, 0))

    assert repair_transient_cycle_rows(tmp_path) == 2
    assert repair_transient_cycle_rows(tmp_path) == 0

    with cycle_path.open(encoding="utf-8", newline="") as handle:
        cycle_reader = csv.DictReader(handle)
        cycle_rows = tuple(cycle_reader)
    assert cycle_reader.fieldnames == [
        "ranking_observed_at",
        "status",
        "eligible_count",
        "success_count",
        "failure_count",
    ]
    assert tuple(row["success_count"] for row in cycle_rows) == (
        "61",
        "71",
        "78",
        "71",
    )
    with reuse_path.open(encoding="utf-8", newline="") as handle:
        reuse_rows = tuple(csv.DictReader(handle))
    assert tuple(row["ranking_observed_at"] for row in reuse_rows) == (
        "2026-07-13T22:40:31+09:00",
        "2026-07-13T22:41:37+09:00",
        "2026-07-13T22:42:41+09:00",
    )
    assert tuple(row["reused_success_count"] for row in reuse_rows) == (
        "63",
        "70",
        "70",
    )


def _screen(
    observed_at: dt.datetime,
    stock: KisRankedStock,
) -> MarketRiskScreen:
    return MarketRiskGate(
        HaltSnapshot(observed_at, frozenset()),
        MarketRiskConfig(),
    ).screen(((stock,),), limit=10)


def _stock(
    symbol: str,
    price: float,
    change_pct: float,
    *,
    bid: float | None = None,
    ask: float | None = None,
) -> KisRankedStock:
    return KisRankedStock(
        "NAS",
        symbol,
        symbol,
        price,
        change_pct,
        price - 0.01 if bid is None else bid,
        price + 0.01 if ask is None else ask,
        100_000,
        price * 100_000,
        200_000,
        1,
    )
