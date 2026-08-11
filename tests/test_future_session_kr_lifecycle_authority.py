from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_future_session_plan_compiler import _kr_request
from trading_agent.future_session_kr_lifecycle_authority import (
    KrFutureSessionLifecycleRequest,
    bootstrap_kr_future_session_lifecycle,
)
from trading_agent.future_session_plan_compiler import compile_future_session_plan
from trading_agent.future_session_plan_models import ReadyToPrepareSessionPlan


def test_explicit_local_bootstrap_makes_new_day_version_plan_ready_once(
    tmp_path: Path,
) -> None:
    # Given
    request, ledger, day_version = _kr_request(tmp_path, lifecycle="absent")
    assert request.kr_calendar_store is not None
    assert request.kr_rollover_bundle is not None
    authority = KrFutureSessionLifecycleRequest(
        experiment_ledger=ledger,
        calendar_store=request.kr_calendar_store,
        rollover_bundle=request.kr_rollover_bundle,
        code_version=request.frozen_runtime.commit_sha,
        strategy_version=day_version.strategy_version,
        target_session=dt.date(2026, 7, 22),
        decided_at=dt.datetime(2026, 7, 20, 18, tzinfo=dt.timezone(dt.timedelta(hours=9))),
    )

    # When
    first = bootstrap_kr_future_session_lifecycle(authority)
    replay = bootstrap_kr_future_session_lifecycle(authority)
    decision = compile_future_session_plan(request)

    # Then
    assert first.created is True
    assert replay.created is False
    assert replay.event == first.event
    assert isinstance(decision, ReadyToPrepareSessionPlan)
    assert first.event.effective_session_date == decision.target_session
