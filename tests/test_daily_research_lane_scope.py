from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.daily_research_fixtures import write_complete_session
from trading_agent import daily_research_ledger
from trading_agent.daily_research_contract import strategy_contract, strategy_version_identity
from trading_agent.daily_research_ledger import build_daily_record, read_daily_ledger, write_daily_record
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import (
    InvalidLaneContractError,
    single_lane_experiment_scope,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.strategy_factory import StrategyMode


def test_new_daily_record_contains_intraday_experiment_scope_v2(
    tmp_path: Path,
) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)

    record = _build(session, dt.date(2026, 7, 14))

    assert record.schema_version == 2
    assert record.experiment_scope.hypothesis_id == record.hypothesis_id
    assert record.experiment_scope.lanes == (LaneId.INTRADAY_MOMENTUM,)
    assert record.experiment_scope_key == experiment_scope_key(record.experiment_scope)


def test_prior_rows_from_a_different_scope_do_not_increase_forward_counts(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "live_sessions"
    first = sessions / "20260714"
    second = sessions / "20260715"
    write_complete_session(first, dt.date(2026, 7, 14))
    write_complete_session(second, dt.date(2026, 7, 15))
    prior = _build(first, dt.date(2026, 7, 14))
    other_scope = single_lane_experiment_scope(
        LaneId.SWING_MOMENTUM,
        "H-SWING-TEST-001",
        dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
    )
    payload = prior.model_dump(mode="json")
    payload.update(
        hypothesis_id=other_scope.hypothesis_id,
        experiment_scope=other_scope.model_dump(mode="json"),
        experiment_scope_key=experiment_scope_key(other_scope),
    )
    ledger = sessions / "daily_research_ledger.jsonl"
    _ = ledger.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    current = _build(second, dt.date(2026, 7, 15))

    assert current.promotion.cumulative_forward_days == 1
    assert current.promotion.cumulative_completed_trades == 1


def test_prior_rows_from_a_different_code_version_do_not_increase_forward_counts(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "live_sessions"
    first = sessions / "20260714"
    second = sessions / "20260715"
    write_complete_session(first, dt.date(2026, 7, 14))
    write_complete_session(second, dt.date(2026, 7, 15))
    prior = _build(first, dt.date(2026, 7, 14), code_version="first-code")
    assert write_daily_record(first, prior) is True

    current = _build(second, dt.date(2026, 7, 15), code_version="second-code")

    assert current.strategy_version == strategy_version_identity(StrategyMode.ORB, "second-code")
    assert current.strategy_version != prior.strategy_version
    assert current.promotion.cumulative_forward_days == 1
    assert current.promotion.cumulative_completed_trades == 1


def test_schema_v1_row_is_projected_without_rewriting_the_ledger(
    tmp_path: Path,
) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)
    current = _build(session, dt.date(2026, 7, 14))
    payload = current.model_dump(mode="json")
    payload["schema_version"] = 1
    del payload["experiment_scope"]
    del payload["experiment_scope_key"]
    ledger = session.parent / "daily_research_ledger.jsonl"
    original = json.dumps(payload, sort_keys=True) + "\n"
    _ = ledger.write_text(original, encoding="utf-8")

    projected = read_daily_ledger(ledger)

    assert len(projected) == 1
    assert projected[0].schema_version == 2
    assert projected[0].experiment_scope.lanes == (LaneId.INTRADAY_MOMENTUM,)
    assert ledger.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("invalid_row", ("[]", "{"))
def test_invalid_ledger_row_preserves_pydantic_validation_error(
    tmp_path: Path,
    invalid_row: str,
) -> None:
    ledger = tmp_path / "daily_research_ledger.jsonl"
    _ = ledger.write_text(invalid_row + "\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        _ = read_daily_ledger(ledger)


def test_daily_record_rejects_scope_registered_after_market_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session)
    original = strategy_contract(StrategyMode.ORB)
    late_scope = single_lane_experiment_scope(
        LaneId.INTRADAY_MOMENTUM,
        original.hypothesis_id,
        dt.datetime(2026, 7, 14, 14, tzinfo=dt.UTC),
    )
    late_contract = replace(original, experiment_scope=late_scope)
    monkeypatch.setattr(
        daily_research_ledger,
        "strategy_contract",
        lambda _: late_contract,
    )

    with pytest.raises(InvalidLaneContractError):
        _ = _build(session, dt.date(2026, 7, 14))

    assert not (session / "daily_research_records").exists()
    assert not (session / "daily_research_summary_ko.md").exists()


def _build(session: Path, session_date: dt.date, *, code_version: str = "test-code"):
    return build_daily_record(
        session,
        session_date,
        StrategyMode.ORB,
        code_version,
        dt.datetime(2026, 7, 15, 21, tzinfo=dt.UTC),
    )
