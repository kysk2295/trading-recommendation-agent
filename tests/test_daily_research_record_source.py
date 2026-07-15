from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from tests.daily_research_fixtures import write_complete_session
from trading_agent.daily_research_ledger import build_daily_record, write_daily_record
from trading_agent.daily_research_record_source import (
    InvalidDailyResearchRecordSourceError,
    load_daily_research_record_source,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import single_lane_experiment_scope
from trading_agent.lane_defaults import current_intraday_experiment_scope
from trading_agent.lane_policy_models import LaneId
from trading_agent.strategy_factory import StrategyMode

SESSION_DATE = dt.date(2026, 7, 14)
ORB_SCOPE_KEY = experiment_scope_key(current_intraday_experiment_scope("H-MOM-ORB-001"))


def test_source_selects_the_latest_exact_orb_record(tmp_path: Path) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)
    first = _write_record(session, "first", hour=21)
    latest = _write_record(session, "latest", hour=22)

    source = load_daily_research_record_source(
        session,
        SESSION_DATE,
        StrategyMode.ORB,
        ORB_SCOPE_KEY,
    )

    assert source.record.record_id == latest.record_id
    assert source.record.record_id != first.record_id
    assert source.record.strategy == "orb"
    assert source.raw_sha256 == hashlib.sha256(source.record_path.read_bytes()).hexdigest()


def test_source_projects_schema_v1_without_rewriting_raw_files(
    tmp_path: Path,
) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)
    record = _write_record(session, "legacy", hour=21)
    record_path = next((session / "daily_research_records").glob(f"*_{record.record_id[:12]}.json"))
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    del payload["experiment_scope"]
    del payload["experiment_scope_key"]
    raw = json.dumps(payload, sort_keys=True) + "\n"
    _ = record_path.write_text(raw, encoding="utf-8")
    ledger = session.parent / "daily_research_ledger.jsonl"
    _ = ledger.write_text(raw, encoding="utf-8")

    source = load_daily_research_record_source(
        session,
        SESSION_DATE,
        StrategyMode.ORB,
        ORB_SCOPE_KEY,
    )

    assert source.record.schema_version == 2
    assert source.record.experiment_scope_key == ORB_SCOPE_KEY
    assert record_path.read_text(encoding="utf-8") == raw
    assert ledger.read_text(encoding="utf-8") == raw


def test_source_requires_exact_parent_ledger_membership(tmp_path: Path) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)
    _ = _write_record(session, "orphan", hour=21)
    (session.parent / "daily_research_ledger.jsonl").unlink()

    with pytest.raises(InvalidDailyResearchRecordSourceError):
        _ = load_daily_research_record_source(
            session,
            SESSION_DATE,
            StrategyMode.ORB,
            ORB_SCOPE_KEY,
        )


@pytest.mark.parametrize(
    ("session_date", "strategy", "scope_key"),
    (
        (dt.date(2026, 7, 15), StrategyMode.ORB, ORB_SCOPE_KEY),
        (SESSION_DATE, StrategyMode.VWAP_RECLAIM, ORB_SCOPE_KEY),
        (
            SESSION_DATE,
            StrategyMode.ORB,
            experiment_scope_key(
                single_lane_experiment_scope(
                    LaneId.SWING_MOMENTUM,
                    "H-SWING-TEST-001",
                    dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
                )
            ),
        ),
    ),
)
def test_source_rejects_a_different_date_strategy_or_scope(
    tmp_path: Path,
    session_date: dt.date,
    strategy: StrategyMode,
    scope_key: str,
) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)
    _ = _write_record(session, "exact", hour=21)

    with pytest.raises(InvalidDailyResearchRecordSourceError):
        _ = load_daily_research_record_source(
            session,
            session_date,
            strategy,
            scope_key,
        )


def _write_record(session: Path, code_version: str, *, hour: int):
    record = build_daily_record(
        session,
        SESSION_DATE,
        StrategyMode.ORB,
        code_version,
        dt.datetime(2026, 7, 15, hour, tzinfo=dt.UTC),
    )
    assert write_daily_record(session, record) is True
    return record
