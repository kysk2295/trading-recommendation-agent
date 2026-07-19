from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_kr_theme_day_trial import OPPORTUNITY_VERSION, _calendar_evidence, _register_authority
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_theme_day_shadow_entry import (
    project_kr_theme_day_shadow_entry,
)
from trading_agent.kr_theme_day_shadow_entry_models import InvalidKrThemeDayShadowEntryError
from trading_agent.kr_theme_day_shadow_entry_store import (
    InvalidKrThemeDayShadowEntryStoreError,
    KrThemeDayShadowEntryStore,
)
from trading_agent.kr_theme_day_trial import (
    KrThemeDayTrialRegistrationRequest,
    register_kr_theme_day_shadow_trial,
    start_kr_theme_day_shadow_trial,
)
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.kr_theme_research_registration import kr_theme_day_strategy_version
from trading_agent.signal_contract_models import (
    EvidenceRef,
    QuoteValidation,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)

KST = ZoneInfo("Asia/Seoul")
CODE = "kr-theme-day-fixture-code-v1"
VERSION = kr_theme_day_strategy_version(CODE)
SESSION = dt.date(2026, 7, 20)
REGISTERED = dt.datetime(2026, 7, 19, 8, 31, tzinfo=KST)
STARTED = dt.datetime(2026, 7, 20, 9, tzinfo=KST)
OBSERVED = dt.datetime(2026, 7, 20, 9, 5, tzinfo=KST)


def _ledger(path: Path, *, started: bool = True) -> ExperimentLedgerStore:
    ledger = ExperimentLedgerStore(path)
    _register_authority(ledger)
    result = register_kr_theme_day_shadow_trial(
        ledger,
        KrThemeDayTrialRegistrationRequest(
            strategy_version=VERSION,
            code_version=CODE,
            session_date=SESSION,
            registered_at=REGISTERED,
            calendar_snapshot=_calendar_evidence()[1],
            opportunity_strategy_version=OPPORTUNITY_VERSION,
        ),
        clock=lambda: REGISTERED,
    )
    if started:
        _ = start_kr_theme_day_shadow_trial(ledger, result.registration.trial_id, STARTED)
    return ledger


def _signal() -> TradeSignalEnvelope:
    return TradeSignalEnvelope(
        signal_id="kr-theme-shadow-entry-fixture",
        strategy_lane=KR_THEME_LEADER_VWAP_RECLAIM_LANE,
        producer_strategy_version=VERSION,
        symbol="005930",
        observed_at=OBSERVED,
        valid_until=OBSERVED + dt.timedelta(seconds=5),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.LIMIT,
        entry_price=Decimal("10000"),
        stop_price=Decimal("9800"),
        targets=(
            TradeTarget(label="1r", price=Decimal("10200")),
            TradeTarget(label="2r", price=Decimal("10400")),
        ),
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule="Invalidate below completed-bar VWAP support.",
        rationale="Fresh rank-one theme leader VWAP reclaim.",
        evidence_refs=(EvidenceRef(namespace="quote/kis-kr", record_id="quote-1", observed_at=OBSERVED),),
        quote_validation=QuoteValidation(
            bid=Decimal("9990"),
            ask=Decimal("10000"),
            observed_at=OBSERVED,
            valid_until=OBSERVED + dt.timedelta(seconds=5),
            spread_bps=Decimal("10.005"),
            max_slippage_bps=Decimal("20"),
        ),
        opportunity_id="KR-THEME-OPPORTUNITY-001",
    )


def test_current_signal_projects_trial_bound_conservative_entry_and_replays(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    filled_at = OBSERVED + dt.timedelta(seconds=1)

    first = project_kr_theme_day_shadow_entry(ledger, store, _signal(), filled_at=filled_at)
    second = project_kr_theme_day_shadow_entry(ledger, store, _signal(), filled_at=filled_at)

    assert first.created is True
    assert second.created is False
    assert first.entry.fill_price == Decimal("10020.000")
    assert first.entry.slippage_bps == Decimal("20")
    assert first.entry.filled_at == filled_at
    assert store.entries() == (first.entry,)
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_shadow_entry_requires_started_trial_and_fresh_signal(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "experiment.sqlite3", started=False)
    store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")

    with pytest.raises(InvalidKrThemeDayShadowEntryError):
        _ = project_kr_theme_day_shadow_entry(ledger, store, _signal(), filled_at=OBSERVED + dt.timedelta(seconds=1))
    with pytest.raises(InvalidKrThemeDayShadowEntryError):
        _ = project_kr_theme_day_shadow_entry(
            _ledger(tmp_path / "started.sqlite3"),
            store,
            _signal(),
            filled_at=OBSERVED + dt.timedelta(seconds=6),
        )
    assert not store.path.exists()


def test_shadow_entry_store_detects_payload_tamper(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    _ = project_kr_theme_day_shadow_entry(ledger, store, _signal(), filled_at=OBSERVED + dt.timedelta(seconds=1))
    with sqlite3.connect(store.path) as connection:
        _ = connection.execute("DROP TRIGGER kr_theme_day_shadow_entries_no_update")
        _ = connection.execute("UPDATE kr_theme_day_shadow_entries SET payload_json = '{}' ")
        connection.commit()

    with pytest.raises(InvalidKrThemeDayShadowEntryStoreError):
        _ = store.entries()


def test_shadow_entry_store_rejects_public_file(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    _ = project_kr_theme_day_shadow_entry(ledger, store, _signal(), filled_at=OBSERVED + dt.timedelta(seconds=1))
    store.path.chmod(0o644)

    with pytest.raises(InvalidKrThemeDayShadowEntryStoreError):
        _ = store.entries()
