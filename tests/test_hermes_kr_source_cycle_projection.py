from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from run_hermes_delivery import main
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore

CYCLE_ID = "kr-live-20260722-1340"
COLLECTION_DATE = dt.date(2026, 7, 22)
COMPLETED_AT = dt.datetime(2026, 7, 22, 13, 39, tzinfo=dt.timezone(dt.timedelta(hours=9)))
OBSERVED_AT = dt.datetime(2026, 7, 22, 4, 40, tzinfo=dt.UTC)


def test_cli_projects_current_incomplete_kr_cycle_as_incident(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    source_database = tmp_path / "kr-theme.sqlite3"
    delivery_database = tmp_path / "delivery.sqlite3"
    _seed_source_runs(source_database, include_dart=False)

    # When
    exit_code = main(
        _arguments(source_database, delivery_database),
        clock=lambda: OBSERVED_AT,
    )
    output = json.loads(capsys.readouterr().out)

    # Then
    assert exit_code == 0
    assert output == {
        "examined": 1,
        "inserted": 1,
        "replayed": 0,
        "result": "projected_kr_source_incident",
    }
    events = HermesDeliveryStore(delivery_database).events()
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.INCIDENT
    assert events[0].market_id == "kr_equities"
    assert events[0].agent_family == "opportunity_manager"
    assert events[0].status == "blocked_source_incomplete"
    assert events[0].instrument_id is None


def test_cli_replays_identical_kr_cycle_incident_without_duplicate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    source_database = tmp_path / "kr-theme.sqlite3"
    delivery_database = tmp_path / "delivery.sqlite3"
    _seed_source_runs(source_database, include_dart=False)
    assert main(
        _arguments(source_database, delivery_database),
        clock=lambda: OBSERVED_AT,
    ) == 0
    _ = capsys.readouterr()

    # When
    exit_code = main(
        _arguments(source_database, delivery_database),
        clock=lambda: OBSERVED_AT,
    )
    output = json.loads(capsys.readouterr().out)

    # Then
    assert exit_code == 0
    assert output["inserted"] == 0
    assert output["replayed"] == 1
    assert len(HermesDeliveryStore(delivery_database).events()) == 1


def test_cli_blocks_stale_kr_cycle_incident(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    stale_source = tmp_path / "stale.sqlite3"
    stale_delivery = tmp_path / "stale-delivery.sqlite3"
    _seed_source_runs(stale_source, include_dart=False, collection_date=dt.date(2026, 7, 21))

    # When
    stale_exit = main(
        _arguments(stale_source, stale_delivery),
        clock=lambda: OBSERVED_AT,
    )
    stale_output = json.loads(capsys.readouterr().out)

    # Then
    assert stale_exit == 2
    assert stale_output == {"reason": "invalid_projection_source", "result": "blocked"}
    assert HermesDeliveryStore(stale_delivery).events() == ()


def test_cli_blocks_complete_kr_cycle_incident(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    complete_source = tmp_path / "complete.sqlite3"
    complete_delivery = tmp_path / "complete-delivery.sqlite3"
    _seed_source_runs(complete_source, include_dart=True)

    # When
    complete_exit = main(
        _arguments(complete_source, complete_delivery),
        clock=lambda: OBSERVED_AT,
    )
    complete_output = json.loads(capsys.readouterr().out)

    # Then
    assert complete_exit == 2
    assert complete_output == {"reason": "invalid_projection_source", "result": "blocked"}
    assert HermesDeliveryStore(complete_delivery).events() == ()


def _arguments(source_database: Path, delivery_database: Path) -> tuple[str, ...]:
    return (
        "project-kr-cycle",
        "--database",
        str(delivery_database),
        "--source-database",
        str(source_database),
        "--collection-cycle-id",
        CYCLE_ID,
    )


def _seed_source_runs(
    database: Path,
    *,
    include_dart: bool,
    collection_date: dt.date = COLLECTION_DATE,
) -> None:
    with KrThemeStore(database).writer() as writer:
        for source in KrCatalystSource:
            if source is KrCatalystSource.DART and not include_dart:
                continue
            _ = writer.append_source_run(
                KrSourceCollectionRun(
                    source_run_id=f"{CYCLE_ID}:{source.value}",
                    collection_cycle_id=CYCLE_ID,
                    source=source,
                    adapter_version=f"{source.value}-fixture-v1",
                    started_at=COMPLETED_AT - dt.timedelta(minutes=1),
                    completed_at=COMPLETED_AT,
                    status=KrCoverageStatus.SUCCESS,
                    record_count=0,
                    failure_code=None,
                    receipt_ids=(),
                    collection_date=collection_date,
                )
            )
