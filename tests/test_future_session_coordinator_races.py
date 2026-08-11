from __future__ import annotations

from pathlib import Path

import pytest

import trading_agent.future_session_coordinator as coordinator_module
from tests.test_future_session_us_materializer import _authority_files
from trading_agent.future_session_coordinator import coordinate_future_session
from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorRequest,
)
from trading_agent.future_session_us_activation_models import (
    FutureSessionActivationError,
)


def test_destination_claim_race_returns_typed_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: another process claims an activation destination after preparation inspection.
    _request, _plan, request_path, _plan_path = _authority_files(tmp_path)

    def raced_activation(*, manifest_path: Path, launch_agents_dir: Path, launchctl_runner):
        del manifest_path, launch_agents_dir, launchctl_runner
        raise FutureSessionActivationError("activation_already_claimed")

    monkeypatch.setattr(
        coordinator_module,
        "activate_us_future_session",
        raced_activation,
    )

    # When
    blocked = coordinate_future_session(
        FutureSessionCoordinatorRequest(
            request_path=request_path,
            plan_path=tmp_path / "coordinator-plan.json",
            launch_agents_dir=tmp_path / "Library" / "LaunchAgents",
        )
    )

    # Then
    assert blocked.result == "blocked"
    assert blocked.reason == "destination_claimed"
