from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.us_day_agent_service import (
    UsDayAgentService,
    UsDayAgentServiceConfig,
    UsDayAgentTickRequest,
    UsDayAgentTickResult,
    UsDaySessionPhase,
)


class _FakeVertical:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def premarket(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        self.calls.append("premarket")
        return UsDayAgentTickResult.accepted(request, market_map_id="map-1")

    def recover_paper(self, request: UsDayAgentTickRequest) -> None:
        self.calls.append("recover")

    def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
        self.calls.append("regular")
        return UsDayAgentTickResult.accepted(
            request,
            recommendation_id="rec-1",
            paper_eligible=True,
        )

    def publish_regular(self, request: UsDayAgentTickRequest, result: UsDayAgentTickResult) -> None:
        self.calls.append("publish")

    def execute_paper(self, request: UsDayAgentTickRequest, result: UsDayAgentTickResult) -> str:
        self.calls.append("paper")
        return "completed"

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
    first = UsDayAgentService(config, fake).tick(request)
    replay = UsDayAgentService(config, fake).tick(request)

    # Then: recovery precedes one entry attempt and replay is exact.
    assert fake.calls == ["recover", "regular", "publish", "paper"]
    assert replay == first
    assert first.recommendation_id == "rec-1"


def test_session_phases_dispatch_cutoff_eod_and_post_close(tmp_path: Path) -> None:
    # Given: official-XNYS phase timestamps.
    fake = _FakeVertical()
    service = UsDayAgentService(UsDayAgentServiceConfig(receipt_root=tmp_path / "receipts"), fake)

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
    result = UsDayAgentService(UsDayAgentServiceConfig(receipt_root=tmp_path / "receipts"), fake).tick(request)

    # Then: it records a stable blocker and never invokes the post-close promotion boundary.
    assert result.status == "blocked"
    assert result.reason == "day_agent_model_call_failed"
    assert fake.calls == ["recover", "regular"]


def test_ineligible_regular_result_publishes_without_paper_mutation(tmp_path: Path) -> None:
    # Given: an accepted NO_TRADE research result without Champion Paper eligibility.
    class _NoTrade(_FakeVertical):
        def regular(self, request: UsDayAgentTickRequest) -> UsDayAgentTickResult:
            self.calls.append("regular")
            return UsDayAgentTickResult.accepted(request)

    fake = _NoTrade()
    request = _request(tmp_path / "situation.json", UsDaySessionPhase.REGULAR)

    # When: the service publishes the regular-session decision.
    result = UsDayAgentService(UsDayAgentServiceConfig(receipt_root=tmp_path / "receipts"), fake).tick(request)

    # Then: no Paper execution method is reached.
    assert result.status == "accepted"
    assert fake.calls == ["recover", "regular", "publish"]
