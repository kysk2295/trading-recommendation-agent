from __future__ import annotations

import datetime as dt
import importlib
from pathlib import Path

import pytest

from tests.test_day_learning_report_models import NOW, SHA_A, _payload, _report
from trading_agent.day_learning_report_models import (
    ExecutionReportSection,
    InvalidDayLearningReportError,
    MarketCloseReportPayload,
)
from trading_agent.day_learning_report_store import (
    load_market_close_report,
    publish_market_close_report,
)
from trading_agent.day_learning_reports import build_daily_learning_report, seal_market_close_report
from trading_agent.research_identity_models import MarketId


def test_day_learning_report_store_api_is_explicit() -> None:
    # Given: the immutable market-close report store module.
    module = importlib.import_module("trading_agent.day_learning_report_store")

    # When: its supported content-addressed operations are inspected.
    names = {"load_market_close_report", "publish_market_close_report"}

    # Then: reports have one publication and verification boundary.
    assert names <= set(module.__all__)


def test_report_store_is_private_idempotent_and_requires_exact_revision_chain(
    tmp_path: Path,
) -> None:
    # Given: one initial market final and a revision linked to its exact content ID.
    first = _report(_payload())
    revision = _report(_payload(revision=2, previous_report_id=first.report_id))

    # When: the projector publishes the initial report, its replay, and the revision.
    first_path, first_created = publish_market_close_report(tmp_path, first)
    replay_path, replay_created = publish_market_close_report(tmp_path, first)
    revision_path, revision_created = publish_market_close_report(tmp_path, revision)

    # Then: publication is private, replay is a no-op, and both immutable revisions verify.
    assert (first_created, replay_created, revision_created) == (True, False, True)
    assert replay_path == first_path
    assert first_path.stat().st_mode & 0o777 == 0o600
    assert load_market_close_report(first_path) == first
    assert load_market_close_report(revision_path) == revision


def test_report_store_rejects_a_second_initial_final_for_the_same_watermark(
    tmp_path: Path,
) -> None:
    # Given: an initial report already published for one market/session/watermark.
    first = _report(_payload())
    _ = publish_market_close_report(tmp_path, first)
    raw = _payload().model_dump(mode="python")
    changed_execution = ExecutionReportSection.model_validate(raw["execution"] | {"unresolved_count": 2})
    conflicting = _report(MarketCloseReportPayload.model_validate(raw | {"execution": changed_execution}))

    # When / Then: the same finalization tuple cannot acquire another revision-one identity.
    with pytest.raises(InvalidDayLearningReportError, match="initial_final_conflict"):
        _ = publish_market_close_report(tmp_path, conflicting)


def test_report_store_rejects_a_fork_from_an_already_revised_parent(
    tmp_path: Path,
) -> None:
    # Given: a report chain that already contains revision two.
    first = _report(_payload())
    second_payload = _payload(revision=2, previous_report_id=first.report_id)
    second = _report(second_payload)
    _ = publish_market_close_report(tmp_path, first)
    _ = publish_market_close_report(tmp_path, second)
    raw = second_payload.model_dump(mode="python")
    changed_execution = ExecutionReportSection.model_validate(raw["execution"] | {"unresolved_count": 2})
    fork = _report(MarketCloseReportPayload.model_validate(raw | {"execution": changed_execution}))

    # When / Then: the same parent cannot acquire a second revision-two child.
    with pytest.raises(InvalidDayLearningReportError, match="revision_chain_invalid"):
        _ = publish_market_close_report(tmp_path, fork)


def test_report_store_requires_compounded_cross_session_cumulative_returns(
    tmp_path: Path,
) -> None:
    # Given: one US market close and two candidate reports for the next session.
    first = _report(_payload())
    valid_second = _report(
        _payload(
            session_date=dt.date(2026, 8, 21),
            watermark_id="c" * 64,
            cumulative_lineage_report_ids=(first.report_id,),
            cumulative_actual_return=0.008016,
            cumulative_modeled_return=0.012036,
        )
    )
    invalid_second = _report(
        _payload(
            session_date=dt.date(2026, 8, 21),
            watermark_id="c" * 64,
            cumulative_lineage_report_ids=(first.report_id,),
            cumulative_actual_return=0.008,
            cumulative_modeled_return=0.012,
        )
    )

    # When: each next-day report is published against the exact prior market report.
    _ = publish_market_close_report(tmp_path / "valid", first)
    _, valid_created = publish_market_close_report(tmp_path / "valid", valid_second)
    _ = publish_market_close_report(tmp_path / "invalid", first)

    # Then: compounded totals pass and additive-looking totals fail closed.
    assert valid_created is True
    with pytest.raises(InvalidDayLearningReportError, match="cumulative_return_invalid"):
        _ = publish_market_close_report(tmp_path / "invalid", invalid_second)


def test_query_facade_requires_verified_market_reports_and_has_no_return_projection() -> None:
    # Given: separately sealed US and KR reports.
    us_report = seal_market_close_report(_payload())
    kr_report = seal_market_close_report(_payload(MarketId.KR_EQUITIES))

    # When: a query-only dual-market facade is built.
    facade = build_daily_learning_report(
        us_report,
        kr_report,
        generated_at=NOW + dt.timedelta(minutes=1),
    )

    # Then: it only links the two content IDs and exposes no return field.
    assert (facade.us_report_id, facade.kr_report_id) == (
        us_report.report_id,
        kr_report.report_id,
    )
    assert all("return" not in field for field in type(facade).model_fields)
    assert SHA_A not in facade.model_dump_json()
