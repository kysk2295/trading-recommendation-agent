from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import typer

import run_kr_same_cycle_opportunity
from tests.test_kr_same_cycle_opportunity_cli import (
    CYCLE_ID,
    KST,
    _argv,
    _paths,
    _register,
    _write_policy,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore


def test_incomplete_source_cycle_delivers_incident_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: registered research whose live collection leaves one terminal source.
    paths = _paths(tmp_path)
    _register(paths["ledger"], tmp_path)
    policy = _write_policy(tmp_path)

    def collect_incomplete(**values: str | None) -> None:
        _seed_incomplete(Path(values["database"] or ""))
        raise typer.BadParameter("source incomplete")

    monkeypatch.setattr(run_kr_same_cycle_opportunity.run_kr_same_cycle_collect, "main", collect_incomplete)

    # When: the same blocked operating cycle is retried.
    first = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )
    second = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )

    # Then: the user-visible source incident is durable and deduplicated.
    events = HermesDeliveryStore(paths["delivery"]).events()
    assert first == 1
    assert second == 1
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.INCIDENT
    assert events[0].status == "blocked_source_incomplete"


def test_source_preflight_failure_delivers_incident_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _register(paths["ledger"], tmp_path)
    policy = _write_policy(tmp_path)

    def reject_before_store(**_values: str | None) -> None:
        raise typer.BadParameter("source preflight blocked")

    monkeypatch.setattr(run_kr_same_cycle_opportunity.run_kr_same_cycle_collect, "main", reject_before_store)

    first = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )
    second = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )

    events = HermesDeliveryStore(paths["delivery"]).events()
    assert first == 1
    assert second == 1
    assert not paths["database"].exists()
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.INCIDENT
    assert events[0].status == "blocked_source_preflight"


def test_unregistered_research_cannot_deliver_preexisting_source_incident(tmp_path: Path) -> None:
    # Given: an incomplete source ledger but no registered strategy authority.
    paths = _paths(tmp_path)
    _seed_incomplete(paths["database"])
    with ExperimentLedgerStore(paths["ledger"]).writer():
        pass
    policy = _write_policy(tmp_path)

    # When: the unauthorized operating cycle is requested.
    result = run_kr_same_cycle_opportunity.main(
        _argv(paths, policy),
        clock=lambda: dt.datetime(2026, 7, 16, 10, 2, 30, tzinfo=KST),
    )

    # Then: it remains blocked without creating a user-visible delivery.
    assert result == 1
    assert not paths["delivery"].exists()


def _seed_incomplete(database: Path) -> None:
    with KrThemeStore(database).writer() as writer:
        _ = writer.append_source_run(
            KrSourceCollectionRun(
                source_run_id=f"{CYCLE_ID}:news",
                collection_cycle_id=CYCLE_ID,
                source=KrCatalystSource.NEWS,
                adapter_version="news-fixture-v1",
                started_at=dt.datetime(2026, 7, 16, 10, 0, tzinfo=KST),
                completed_at=dt.datetime(2026, 7, 16, 10, 1, tzinfo=KST),
                status=KrCoverageStatus.SUCCESS,
                record_count=0,
                receipt_ids=(),
                collection_date=dt.date(2026, 7, 16),
            )
        )
