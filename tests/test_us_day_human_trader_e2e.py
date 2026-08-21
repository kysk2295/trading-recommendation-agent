from __future__ import annotations

from pathlib import Path

from tests.test_us_day_agent_service import _FakeVertical, _request
from trading_agent.us_day_agent_service import UsDayAgentService, UsDayAgentServiceConfig, UsDaySessionPhase


def test_natural_session_vertical_recommends_executes_reviews_and_learns(tmp_path: Path) -> None:
    # Given: one local-fake XNYS session and durable application receipts.
    fake = _FakeVertical()
    service = UsDayAgentService(UsDayAgentServiceConfig(receipt_root=tmp_path / "receipts"), fake)

    # When: the scheduler advances through premarket, regular, EOD, and post-close.
    pre = service.tick(_request(tmp_path / "pre.json", UsDaySessionPhase.PREMARKET))
    live = service.tick(_request(tmp_path / "live.json", UsDaySessionPhase.REGULAR))
    flat = service.tick(_request(tmp_path / "eod.json", UsDaySessionPhase.EOD))
    close = service.tick(_request(tmp_path / "close.json", UsDaySessionPhase.POST_CLOSE))

    # Then: the observable lifecycle reaches review and research-only Challenger creation.
    assert pre.market_map_id == "map-1"
    assert live.recommendation_id == "rec-1"
    assert live.paper_status == "completed"
    assert flat.paper_status == "flat"
    assert close.market_close_report_id == "report-1"
    assert close.challenger_version_id == "challenger-1"
