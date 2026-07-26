from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tests.test_data_capability_registry import _capability, _entitlement
from trading_agent.dashboard_projection_sources import PROVIDER_SOURCES
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.dashboard_system_evidence import MILESTONE_FILE, MILESTONE_IDS
from trading_agent.data_capability_registry import DataCapabilityRegistryStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)
KST = dt.timezone(dt.timedelta(hours=9))


def test_provider_capabilities_diverge_from_authoritative_registry(tmp_path: Path) -> None:
    # Given only FRED has a persisted capability and entitlement
    outputs = tmp_path / "outputs"
    source = PROVIDER_SOURCES["fred"]
    capability = _capability(NOW).model_copy(update={"source_id": source})
    entitlement = _entitlement().model_copy(
        update={"source_id": source, "entitlement_id": "fred-dashboard-v1"}
    )
    DataCapabilityRegistryStore(
        outputs / "source_evidence" / "data_capability_registry.sqlite3"
    ).append((capability,), (entitlement,))

    # When the provider registry is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then FRED is truthful and unrelated providers remain unavailable
    capabilities = {
        capability.provider: capability
        for capability in snapshot.workspaces.data_sources.capabilities
    }
    assert capabilities["fred"].state == "populated"
    assert capabilities["fred"].entitlement == "realtime"
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


def test_system_projects_exactly_m0_through_m10_from_typed_evidence(
    tmp_path: Path,
) -> None:
    # Given complete allowlisted typed milestone evidence
    outputs = tmp_path / "outputs"
    root = outputs / "system"
    root.mkdir(parents=True)
    path = root / MILESTONE_FILE
    rows = [
        {
            "schema_version": 2,
            "evidence_type": "milestone",
            "epoch_id": "release-1",
            "milestone_id": milestone,
            "status": "passed",
            "observed_at": NOW.isoformat(),
            "code_sha256": f"{index + 1:064x}",
            "result_code": "stage_passed",
        }
        for index, milestone in enumerate(MILESTONE_IDS)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)

    # When system evidence is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then exactly the eleven allowlisted milestones are populated
    system = snapshot.workspaces.system
    assert system.state == "populated"
    assert tuple(item.label for item in system.items) == MILESTONE_IDS
    assert len(system.items) == 11


def test_system_rejects_secret_future_and_mixed_epoch_evidence(tmp_path: Path) -> None:
    # Given typed system evidence contains hostile fields or incompatible epochs
    outputs = tmp_path / "outputs"
    root = outputs / "system"
    root.mkdir(parents=True)
    path = root / MILESTONE_FILE
    rows = [
        {
            "schema_version": 2,
            "evidence_type": "milestone",
            "epoch_id": epoch,
            "milestone_id": milestone,
            "status": "passed",
            "observed_at": observed_at,
            "code_sha256": "a" * 64,
            "result_code": result,
        }
        for epoch, milestone, observed_at, result in (
            ("release-1", "M0", NOW.isoformat(), "stage_passed"),
            ("release-2", "M1", "2099-01-01T00:00:00Z", "api_key_leaked"),
        )
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)

    # When the strict system reader parses it
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then the section fails closed without leaking hostile content
    assert snapshot.workspaces.system.state == "corrupt"
    assert "api_key" not in snapshot.model_dump_json().lower()


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
