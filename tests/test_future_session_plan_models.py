from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.future_session_plan_models import (
    DeferredTrialRegistrationState,
    FrozenRuntimeAuthority,
    FutureSessionArtifactLayout,
    FutureSessionMarket,
    FutureSessionPlanRequest,
)


def test_request_rejects_partial_kr_authority(tmp_path: Path) -> None:
    # Given
    payload = _request_payload(tmp_path)
    payload["market"] = "kr"
    payload["kr_calendar_store"] = str((tmp_path / "calendar.sqlite3").absolute())

    # When / Then
    with pytest.raises(ValidationError):
        FutureSessionPlanRequest.model_validate(payload)


def test_authority_and_layout_require_absolute_paths(tmp_path: Path) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        FrozenRuntimeAuthority(directory=Path("runtime"), commit_sha="a" * 40)
    with pytest.raises(ValueError):
        FutureSessionArtifactLayout.from_root(Path("artifacts"))


def test_trial_state_is_deferred_until_preopen() -> None:
    # Given / When / Then
    assert (
        DeferredTrialRegistrationState.DEFERRED_UNTIL_PREOPEN.value
        == "deferred_until_preopen"
    )


def _request_payload(tmp_path: Path) -> dict[str, str]:
    root = tmp_path.absolute()
    return {
        "market": FutureSessionMarket.US.value,
        "after_date": dt.date(2026, 7, 2).isoformat(),
        "compiled_at": "2026-07-02T20:00:00+00:00",
        "scheduler_main_sha": "b" * 40,
        "frozen_runtime": {
            "directory": str(root / "runtime"),
            "commit_sha": "a" * 40,
        },
        "artifact_root": str(root / "artifacts"),
        "experiment_ledger": str(root / "experiment.sqlite3"),
        "lane_registry": str(root / "lane.sqlite3"),
        "execution_database": str(root / "execution.sqlite3"),
    }
