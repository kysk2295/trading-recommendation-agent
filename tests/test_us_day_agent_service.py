from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_arm_request import HermesArmConsumeCommand
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.paper_mutation_arm import PaperMutationArm
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.us_day_agent_service import (
    LiveUsDayPaperSessionControl,
    UsDayAgentService,
    UsDayAgentServiceConfig,
    UsDayAgentServiceError,
    UsDayAgentTickRequest,
    UsDayAgentTickResult,
    UsDaySessionPhase,
)


class _UnusedArmConsumer:
    def consume(self, command: HermesArmConsumeCommand, expected_strategy_version: str) -> PaperMutationArm:
        raise AssertionError


class _FakeVertical:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def premarket(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        self.calls.append("premarket")
        return UsDayAgentTickResult.accepted(request, market_map_id="map-1")

    def recover(self, request: UsDayAgentTickRequest) -> None:
        self.calls.append("recover")

    def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        self.calls.append("regular")
        return UsDayAgentTickResult.accepted(
            request,
            recommendation_id="rec-1",
            paper_status="completed",
        )

    def cutoff(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        self.calls.append("cutoff")
        return UsDayAgentTickResult.accepted(request, paper_status="entries_blocked")

    def eod(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        self.calls.append("eod")
        return UsDayAgentTickResult.accepted(request, paper_status="flat")

    def post_close(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        self.calls.append("post_close")
        return UsDayAgentTickResult.accepted(
            request,
            market_close_report_id="report-1",
            challenger_version_id="challenger-1",
            paper_status="finalized",
        )


def _request(path: Path, phase: UsDaySessionPhase) -> UsDayAgentTickRequest:
    times = {
        UsDaySessionPhase.PREMARKET: dt.datetime(2026, 8, 21, 8, tzinfo=dt.UTC),
        UsDaySessionPhase.REGULAR: dt.datetime(2026, 8, 21, 15, tzinfo=dt.UTC),
        UsDaySessionPhase.ENTRY_CUTOFF: dt.datetime(2026, 8, 21, 19, 47, tzinfo=dt.UTC),
        UsDaySessionPhase.EOD: dt.datetime(2026, 8, 21, 19, 57, tzinfo=dt.UTC),
        UsDaySessionPhase.POST_CLOSE: dt.datetime(2026, 8, 21, 20, 30, tzinfo=dt.UTC),
        UsDaySessionPhase.CLOSED: dt.datetime(2026, 8, 22, 15, tzinfo=dt.UTC),
    }
    return UsDayAgentTickRequest(
        situation_path=path,
        evaluated_at=times[phase],
        source_sha256="a" * 64,
    )


def test_regular_tick_recovers_paper_before_new_entry_and_replays_after_restart(tmp_path: Path) -> None:
    # Given: a regular-session tick and a durable receipt root.
    fake = _FakeVertical()
    request = _request(tmp_path / "situation.json", UsDaySessionPhase.REGULAR)
    config = UsDayAgentServiceConfig(receipt_root=tmp_path / "receipts")

    # When: the exact tick is run twice across service instances.
    first = UsDayAgentService(config, fake, lambda: request.evaluated_at).tick(request)
    replay = UsDayAgentService(config, fake, lambda: request.evaluated_at).tick(request)

    # Then: recovery precedes one entry attempt and replay is exact.
    assert fake.calls == ["regular"]
    assert replay == first
    assert first.recommendation_id == "rec-1"


def test_session_phases_dispatch_cutoff_eod_and_post_close(tmp_path: Path) -> None:
    # Given: official-XNYS phase timestamps.
    fake = _FakeVertical()
    service = UsDayAgentService(
        UsDayAgentServiceConfig(receipt_root=tmp_path / "receipts"), fake, lambda: dt.datetime.now(dt.UTC)
    )

    # When: one unique tick is run in each non-regular phase.
    results = tuple(
        service.tick(_request(tmp_path / f"{phase.value}.json", phase))
        for phase in (
            UsDaySessionPhase.PREMARKET,
            UsDaySessionPhase.ENTRY_CUTOFF,
            UsDaySessionPhase.EOD,
            UsDaySessionPhase.POST_CLOSE,
        )
    )

    # Then: each phase has its bounded authority and post-close learns only after finalization.
    assert tuple(item.phase for item in results) == (
        UsDaySessionPhase.PREMARKET,
        UsDaySessionPhase.ENTRY_CUTOFF,
        UsDaySessionPhase.EOD,
        UsDaySessionPhase.POST_CLOSE,
    )
    assert fake.calls == ["premarket", "recover", "cutoff", "recover", "eod", "recover", "post_close"]
    assert results[-1].paper_status == "finalized"
    assert results[-1].challenger_version_id == "challenger-1"


def test_model_failure_is_blocked_without_changing_champion(tmp_path: Path) -> None:
    # Given: a vertical whose regular model boundary fails.
    class _FailedModel(_FakeVertical):
        def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
            self.calls.append("regular")
            return UsDayAgentTickResult.blocked(request, "day_agent_model_call_failed")

    fake = _FailedModel()
    request = _request(tmp_path / "situation.json", UsDaySessionPhase.REGULAR)

    # When: the service executes the regular tick.
    result = UsDayAgentService(
        UsDayAgentServiceConfig(receipt_root=tmp_path / "receipts"), fake, lambda: request.evaluated_at
    ).tick(request)

    # Then: it records a stable blocker and never invokes the post-close promotion boundary.
    assert result.status == "blocked"
    assert result.reason == "day_agent_model_call_failed"
    assert fake.calls == ["regular"]


def test_live_paper_control_blocks_missing_standard_credentials_before_transport(tmp_path: Path) -> None:
    # Given: concrete live Paper control whose standard credential loader fails.
    opened = False

    @contextmanager
    def opener(credentials: AlpacaPaperCredentials, store: ExecutionStore) -> Iterator[PaperOperatingSession]:
        nonlocal opened
        opened = True
        raise AssertionError
        yield

    def missing_credentials() -> AlpacaPaperCredentials:
        raise FileNotFoundError

    control = LiveUsDayPaperSessionControl(
        outputs=tmp_path,
        execution_store=ExecutionStore(tmp_path / "execution.sqlite3"),
        lane_registry=LaneRegistryStore(tmp_path / "lane.sqlite3"),
        session_root=tmp_path / "session",
        arm_consumer=_UnusedArmConsumer(),
        safety_arm_request_id="a" * 64,
        strategy_version="leader_breakout",
        session_id="XNYS-2026-08-21",
        credentials_loader=missing_credentials,
        session_opener=opener,
    )

    # When: recovery is required before admission.
    with pytest.raises(UsDayAgentServiceError) as blocked:
        control.recover_and_reconcile(dt.datetime(2026, 8, 21, 15, tzinfo=dt.UTC))

    # Then: the stable blocker is produced without opening broker transport.
    assert blocked.value.reason == "paper_credentials_or_recovery_invalid"
    assert not opened
