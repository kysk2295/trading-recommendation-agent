from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_kr_theme_day_shadow_entry import OBSERVED, _ledger, _signal
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.kr_theme_day_shadow_entry import project_kr_theme_day_shadow_entry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit import (
    InvalidKrThemeDayShadowExitError,
    project_kr_theme_day_shadow_exit,
)
from trading_agent.kr_theme_day_shadow_exit_models import KrThemeDayShadowExitReason
from trading_agent.kr_theme_day_shadow_exit_store import (
    InvalidKrThemeDayShadowExitStoreError,
    KrThemeDayShadowExitStore,
)
from trading_agent.signal_contract_models import EvidenceRef

KST = ZoneInfo("Asia/Seoul")
FIRST_BAR = dt.datetime(2026, 7, 20, 9, 6, tzinfo=KST)


def _stores(tmp_path: Path) -> tuple[KrThemeDayShadowEntryStore, KrThemeDayShadowExitStore, str]:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    entries = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    entry = project_kr_theme_day_shadow_entry(
        ledger,
        entries,
        _signal(),
        filled_at=OBSERVED + dt.timedelta(seconds=1),
    ).entry
    return entries, KrThemeDayShadowExitStore(tmp_path / "exits.sqlite3"), entry.entry_id


def _bar(
    start: dt.datetime,
    *,
    high: str = "10100",
    low: str = "9900",
    close: str = "10050",
) -> KrCompletedMinuteBar:
    observed = start + dt.timedelta(minutes=1, seconds=1)
    return KrCompletedMinuteBar(
        symbol="005930",
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        observed_at=observed,
        open=Decimal("10000"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=100,
        trading_value_krw=Decimal("1000000"),
        evidence_ref=EvidenceRef(
            namespace="kr/minute/bar",
            record_id=f"exit-{start:%H%M}",
            observed_at=observed,
        ),
    )


def test_same_bar_stop_and_target_uses_stop_first_and_replays(tmp_path: Path) -> None:
    entries, exits, entry_id = _stores(tmp_path)
    bar = _bar(FIRST_BAR, high="10300", low="9700", close="10000")

    first = project_kr_theme_day_shadow_exit(entries, exits, entry_id, (bar,), evaluated_at=bar.observed_at)
    replay = project_kr_theme_day_shadow_exit(entries, exits, entry_id, (bar,), evaluated_at=bar.observed_at)

    assert first is not None
    assert replay is not None
    assert first.created is True
    assert replay.created is False
    assert first.exit.reason is KrThemeDayShadowExitReason.STOPPED
    assert first.exit.trigger_price == Decimal("9800")
    assert first.exit.exit_price == Decimal("9780.400")
    assert first.exit.net_return == Decimal("9780.400") / Decimal("10020.000") - Decimal(1)
    assert first.exit.realized_r == (Decimal("9780.400") - Decimal("10020.000")) / Decimal("220.000")


def test_target_exit_uses_first_target_and_adverse_exit_slippage(tmp_path: Path) -> None:
    entries, exits, entry_id = _stores(tmp_path)
    bar = _bar(FIRST_BAR, high="10300", low="9900", close="10250")

    result = project_kr_theme_day_shadow_exit(entries, exits, entry_id, (bar,), evaluated_at=bar.observed_at)

    assert result is not None
    assert result.exit.reason is KrThemeDayShadowExitReason.TARGETED
    assert result.exit.trigger_price == Decimal("10200")
    assert result.exit.exit_price == Decimal("10179.600")


def test_incomplete_path_returns_none_without_creating_store(tmp_path: Path) -> None:
    entries, exits, entry_id = _stores(tmp_path)
    bar = _bar(FIRST_BAR)

    result = project_kr_theme_day_shadow_exit(entries, exits, entry_id, (bar,), evaluated_at=bar.observed_at)

    assert result is None
    assert not exits.path.exists()


def test_time_exit_requires_contiguous_path_through_1530(tmp_path: Path) -> None:
    entries, exits, entry_id = _stores(tmp_path)
    count = int((dt.datetime(2026, 7, 20, 15, 30, tzinfo=KST) - FIRST_BAR) / dt.timedelta(minutes=1))
    bars = tuple(_bar(FIRST_BAR + dt.timedelta(minutes=index)) for index in range(count))

    result = project_kr_theme_day_shadow_exit(entries, exits, entry_id, bars, evaluated_at=bars[-1].observed_at)

    assert result is not None
    assert result.exit.reason is KrThemeDayShadowExitReason.TIME_EXIT
    assert result.exit.trigger_price == Decimal("10050")
    assert result.exit.exit_price == Decimal("10029.900")
    assert len(result.exit.bar_payload_sha256s) == count


def test_mixed_entry_bar_or_sequence_gap_is_rejected(tmp_path: Path) -> None:
    entries, exits, entry_id = _stores(tmp_path)
    mixed = _bar(FIRST_BAR - dt.timedelta(minutes=1))
    gap = (_bar(FIRST_BAR), _bar(FIRST_BAR + dt.timedelta(minutes=2)))

    with pytest.raises(InvalidKrThemeDayShadowExitError):
        _ = project_kr_theme_day_shadow_exit(entries, exits, entry_id, (mixed,), evaluated_at=mixed.observed_at)
    with pytest.raises(InvalidKrThemeDayShadowExitError):
        _ = project_kr_theme_day_shadow_exit(entries, exits, entry_id, gap, evaluated_at=gap[-1].observed_at)
    assert not exits.path.exists()


def test_exit_store_detects_tamper_and_is_private(tmp_path: Path) -> None:
    entries, exits, entry_id = _stores(tmp_path)
    bar = _bar(FIRST_BAR, high="10300")
    result = project_kr_theme_day_shadow_exit(entries, exits, entry_id, (bar,), evaluated_at=bar.observed_at)
    assert result is not None
    assert stat.S_IMODE(exits.path.stat().st_mode) == 0o600
    with sqlite3.connect(exits.path) as connection:
        _ = connection.execute("DROP TRIGGER kr_theme_day_shadow_exits_no_update")
        _ = connection.execute("UPDATE kr_theme_day_shadow_exits SET payload_json = '{}' ")
        connection.commit()

    with pytest.raises(InvalidKrThemeDayShadowExitStoreError):
        _ = exits.exits()
