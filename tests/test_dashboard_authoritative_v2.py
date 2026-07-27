from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.fred_alfred_collection import (
    FredArtifactStore,
    collect_fred_alfred,
)
from trading_agent.fred_alfred_models import (
    FredAlfredRequest,
    FredRawReceipt,
    FredSourceMode,
)
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)
KST = dt.timezone(dt.timedelta(hours=9))


def test_provider_capabilities_diverge_from_authoritative_registry(tmp_path: Path) -> None:
    # Given only FRED has a provider-native typed terminal
    outputs = tmp_path / "outputs"
    request = FredAlfredRequest(
        collection_id="dashboard-fred",
        source_mode=FredSourceMode.FRED,
        series_id="CPIAUCSL",
        observation_start=dt.date(2024, 1, 1),
        observation_end=dt.date(2024, 3, 1),
        limit=10,
    )
    raw = (Path(__file__).parent / "fixtures/fred_alfred/fred_cpi_three.json").read_bytes()
    store = FredArtifactStore(outputs / "source_evidence" / "fred_alfred" / "fred")
    _ = collect_fred_alfred(
        _FredFetcher(request.request_id, raw),
        store,
        request,
        _clock=lambda: NOW,
    )

    # When the provider registry is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then FRED projects its real series/count and every other provider stays local
    capabilities = {
        capability.provider: capability
        for capability in snapshot.workspaces.data_sources.capabilities
    }
    assert capabilities["fred"].state == "populated"
    assert capabilities["fred"].entitlement == "research_only"
    fred_item = next(
        item for item in snapshot.workspaces.data_sources.items if item.item_id == "source.fred"
    )
    assert fred_item.value == "CPIAUCSL:3"
    assert capabilities["alfred"].state == "unavailable"
    assert capabilities["alpaca"].state == "unavailable"
    assert capabilities["kis"].state == "unavailable"


def test_calendar_spoof_cannot_claim_open_when_authority_is_missing(tmp_path: Path) -> None:
    # Given an arbitrary generic receipt claims an open market
    outputs = tmp_path / "outputs"
    root = outputs / "live_sessions"
    root.mkdir(parents=True)
    path = root / "dashboard-receipts.v2.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "snapshot_epoch": "spoof",
                "workspace": "overview",
                "item_id": "market.kr.session",
                "kind": "metric",
                "label": "KR session",
                "value": "open",
                "observed_at": NOW.isoformat(),
                "safe_ref": "a" * 64,
                "terminal_kind": "source_receipt",
                "state": "populated",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    # When authoritative calendar stores are absent
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then neither overview nor markets can report open
    assert snapshot.workspaces.overview.state == "blocked"
    assert snapshot.workspaces.markets.state == "blocked"
    assert all(item.value != "open" for item in snapshot.workspaces.overview.items)


def test_kr_holiday_projects_closed_from_kis_calendar(tmp_path: Path) -> None:
    # Given a KIS calendar explicitly marks the current KR date closed
    outputs = tmp_path / "outputs"
    rows = (
        _calendar_row("20260726", "N", "N", "N"),
        _calendar_row("20260727", "Y", "Y", "Y"),
    )
    receipt = KisKrSessionCalendarReceipt(
        base_date=dt.date(2026, 7, 26),
        received_at=dt.datetime(2026, 7, 26, 11, 55, tzinfo=KST),
        status_code=200,
        content_type="application/json",
        raw_payload=_calendar_payload(rows),
    )
    calendar = project_kis_kr_session_calendar(receipt)
    KisKrSessionCalendarStore(
        outputs / "live_sessions" / "kis_kr_session_calendar.sqlite3"
    ).append(receipt, calendar)

    # When the snapshot is projected during the holiday
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then KR is closed from authority and never inferred open from wall time
    kr = next(item for item in snapshot.workspaces.overview.items if item.item_id == "market.kr.session")
    assert kr.value == "closed"
    assert kr.state == "populated"


def test_kr_open_day_does_not_infer_intraday_open_state(tmp_path: Path) -> None:
    # Given KIS confirms only that the current date is an open trading day
    outputs = tmp_path / "outputs"
    rows = (_calendar_row("20260726", "Y", "Y", "Y"),)
    receipt = KisKrSessionCalendarReceipt(
        base_date=dt.date(2026, 7, 26),
        received_at=dt.datetime(2026, 7, 26, 11, 55, tzinfo=KST),
        status_code=200,
        content_type="application/json",
        raw_payload=_calendar_payload(rows),
    )
    calendar = project_kis_kr_session_calendar(receipt)
    KisKrSessionCalendarStore(
        outputs / "live_sessions" / "kis_kr_session_calendar.sqlite3"
    ).append(receipt, calendar)

    # When the calendar is projected without a typed intraday session receipt
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then it reports the schedule but never fabricates an open market
    kr = next(item for item in snapshot.workspaces.markets.items if item.item_id == "market.kr.session")
    assert kr.value == "scheduled"
    assert kr.value != "open"


def test_kr_calendar_projects_from_active_m3_realtime_output(tmp_path: Path) -> None:
    # Given the regular-session producer writes its authority beneath kr_theme/m3_live
    outputs = tmp_path / "outputs"
    rows = (_calendar_row("20260727", "Y", "Y", "Y"),)
    receipt = KisKrSessionCalendarReceipt(
        base_date=dt.date(2026, 7, 27),
        received_at=dt.datetime(2026, 7, 27, 8, 55, tzinfo=KST),
        status_code=200,
        content_type="application/json",
        raw_payload=_calendar_payload(rows),
    )
    calendar = project_kis_kr_session_calendar(receipt)
    KisKrSessionCalendarStore(
        outputs
        / "kr_theme"
        / "m3_live"
        / "2026-07-27-watch-test"
        / "calendar.sqlite3"
    ).append(receipt, calendar)

    # When the Dashboard projects the same regular-session date
    snapshot = collect_dashboard_snapshot_v2(
        outputs,
        now=dt.datetime(2026, 7, 27, 5, 20, tzinfo=dt.UTC),
    )

    # Then the producer authority is visible without copying it to a fake path
    kr = next(
        item
        for item in snapshot.workspaces.markets.items
        if item.item_id == "market.kr.session"
    )
    assert kr.value == "scheduled"
    assert kr.state == "populated"


def _calendar_payload(rows: tuple[dict[str, str], ...]) -> bytes:
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "success",
            "ctx_area_fk": "",
            "ctx_area_nk": "",
            "output": rows,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _calendar_row(
    date: str,
    business: str,
    trading: str,
    open_day: str,
) -> dict[str, str]:
    return {
        "bass_dt": date,
        "wday_dvsn_cd": "1",
        "bzdy_yn": business,
        "tr_day_yn": trading,
        "opnd_yn": open_day,
        "sttl_day_yn": business,
    }


class _FredFetcher:
    def __init__(self, request_id: str, raw: bytes) -> None:
        self._request_id = request_id
        self._raw = raw

    def fetch(self, request: FredAlfredRequest) -> FredRawReceipt:
        assert request.request_id == self._request_id
        return FredRawReceipt.from_raw(
            request_id=request.request_id,
            received_at=NOW - dt.timedelta(minutes=1),
            status_code=200,
            content_type="application/json",
            raw_payload=self._raw,
        )
