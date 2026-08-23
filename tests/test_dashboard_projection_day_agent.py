from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_day_learning_report_models import NOW, _payload, _report
from trading_agent.dashboard_projection_day_agent import project_day_agent_facade
from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_learning_report_store import publish_market_close_report
from trading_agent.research_identity_models import MarketId


def test_projects_independent_us_paper_us_shadow_and_kr_read_only_lanes(tmp_path: Path) -> None:
    # Given: immutable close evidence for each market.
    outputs = tmp_path / "outputs"
    us_report = _report(_payload())
    kr_report = _report(_payload(MarketId.KR_EQUITIES))
    _publish(outputs / "us_day" / "close_reports", us_report)
    _publish(outputs / "kr_day" / "close_reports", kr_report)

    # When: the query-only dashboard facade reads the two isolated roots.
    projection = project_day_agent_facade(outputs, now=NOW + dt.timedelta(minutes=1))

    # Then: each lane is independently named, with no combined performance projection or writer authority.
    labels = {item.label for item in (*projection.markets, *projection.research)}
    assert "US · Alpaca Paper" in labels
    assert "US · Shadow" in labels
    assert "KR · Shadow · provider read-only" in labels
    rendered = " ".join((item.value or "") for item in (*projection.markets, *projection.research))
    assert "active" in rendered and "queued" in rendered and "suspended" in rendered
    assert "combined" not in rendered.lower()
    assert "confidence" not in rendered.lower()
    assert all("return" not in item.item_id for item in (*projection.markets, *projection.research))
    assert all("order" not in item.item_id for item in (*projection.markets, *projection.research))
    assert projection.daily_learning_report is not None
    assert all("return" not in field for field in type(projection.daily_learning_report).model_fields)


def test_corrupt_kr_evidence_does_not_hide_valid_us_lanes(tmp_path: Path) -> None:
    # Given: a verified US report and a corrupt KR source file.
    outputs = tmp_path / "outputs"
    _publish(outputs / "us_day" / "close_reports", _report(_payload()))
    corrupt = outputs / "kr_day" / "close_reports" / "market_close_report_invalid.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{", encoding="utf-8")
    corrupt.chmod(0o600)

    # When: the facade projects both independently.
    projection = project_day_agent_facade(outputs, now=NOW + dt.timedelta(minutes=1))

    # Then: US remains available while KR alone fails closed.
    us = tuple(item for item in projection.markets if item.item_id.startswith("day_agent.us"))
    kr = tuple(item for item in projection.markets if item.item_id.startswith("day_agent.kr"))
    assert us and all(item.state in {"populated", "empty"} for item in us)
    assert kr and all(item.state == "corrupt" for item in kr)
    assert kr[0].value == "KR evidence invalid"


def _publish(root: Path, report: MarketCloseReport) -> None:
    _, created = publish_market_close_report(root, report)
    assert created
