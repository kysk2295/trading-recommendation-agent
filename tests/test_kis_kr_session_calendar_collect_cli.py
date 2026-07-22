from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
from pathlib import Path

import httpx2
import pytest

import run_kis_kr_session_calendar_collect as collect_cli
from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_auth import KisMode
from trading_agent.kis_kr_session_calendar_client import KIS_KR_CALENDAR_BASE_URL
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kis_kr_session_calendar_collect.py"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 7, 23, 8, 57, tzinfo=KST)


def test_calendar_collect_cli_help_exposes_only_current_date_get_contract() -> None:
    # Given: the production calendar collector entry point.
    # When: an operator asks for its public CLI contract.
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: only private output paths are configurable, never time or mutation authority.
    assert completed.returncode == 0
    assert "calendar-store" in completed.stdout
    assert "output-dir" in completed.stdout
    assert "base-date" not in completed.stdout
    assert "fixture" not in completed.stdout
    assert "account" not in completed.stdout
    assert "order" not in completed.stdout


def test_calendar_collect_cli_fetches_current_date_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an official KIS read-only response and private output locations.
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=_calendar_payload(),
        )

    def client(_: KisMode) -> httpx2.Client:
        return httpx2.Client(
            base_url=KIS_KR_CALENDAR_BASE_URL,
            transport=httpx2.MockTransport(handler),
            follow_redirects=False,
        )

    monkeypatch.setattr(collect_cli, "load_kis_credentials", lambda _: _credentials())
    monkeypatch.setattr(collect_cli, "create_kis_client", client)
    monkeypatch.setattr(collect_cli, "get_access_token", lambda *_: "dummy-token")
    store_path = tmp_path / "calendar.sqlite3"
    output = tmp_path / "report"
    args = ("--calendar-store", str(store_path), "--output-dir", str(output))

    # When: the operator runs the same current-date collection twice.
    first = collect_cli.main(args, clock=lambda: NOW)
    second = collect_cli.main(args, clock=lambda: NOW)

    # Then: one immutable snapshot is reused and no mutation authority is exposed.
    snapshots = KisKrSessionCalendarStore(store_path).snapshots()
    report_path = output / "kis_kr_session_calendar_collection_ko.md"
    report = report_path.read_text(encoding="utf-8")
    printed = capsys.readouterr().out.splitlines()
    assert first == 0
    assert second == 0
    assert len(seen) == 2
    assert all(request.method == "GET" for request in seen)
    assert all(request.url.params["BASS_DT"] == "20260723" for request in seen)
    assert len(snapshots) == 1
    assert printed == [snapshots[0].snapshot_id, snapshots[0].snapshot_id]
    assert "snapshot 신규/재사용: 0/1" in report
    assert "external account/order mutation: 0" in report
    assert "dummy" not in report
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def _credentials() -> KisCredentials:
    return KisCredentials(app_key="dummy-app-key", app_secret="dummy-app-secret")


def _calendar_payload() -> bytes:
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "success",
            "ctx_area_fk": "",
            "ctx_area_nk": "",
            "output": [
                {
                    "bass_dt": "20260723",
                    "wday_dvsn_cd": "4",
                    "bzdy_yn": "Y",
                    "tr_day_yn": "Y",
                    "opnd_yn": "Y",
                    "sttl_day_yn": "Y",
                }
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
