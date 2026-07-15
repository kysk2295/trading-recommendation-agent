from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path

import pytest

from trading_agent.kr_source_collection_models import (
    KrSourceCollectionRun,
    KrSourceReceipt,
)
from trading_agent.kr_source_cycle import finalize_kr_source_cycle
from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystObservation,
    KrCatalystRecord,
    KrCatalystSource,
    KrCoverageStatus,
    KrSourceCoverage,
)
from trading_agent.kr_theme_schema import CREATE_KR_THEME_SCHEMA_V1
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeConflictError,
    KrThemeStore,
)

CYCLE_ID = "kr-source-cycle-20260715-001"
STARTED_AT = dt.datetime(
    2026,
    7,
    15,
    8,
    59,
    tzinfo=dt.timezone(dt.timedelta(hours=9)),
)


def test_finalizer_projects_exact_four_successful_runs_into_complete_cycle(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    runs = _seed_zero_record_runs(store)

    result = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)

    assert result.source_runs == tuple(
        sorted(runs, key=lambda item: item.source.value)
    )
    assert result.missing_sources == ()
    assert result.cycle is not None
    assert result.cycle.started_at == min(run.started_at for run in runs)
    assert result.cycle.completed_at == max(run.completed_at for run in runs)
    assert result.cycle.complete is True
    assert tuple(item.source for item in result.cycle.coverage) == tuple(
        sorted(KrCatalystSource, key=lambda item: item.value)
    )
    assert result.appended is True
    assert store.cycles() == (result.cycle,)


def test_finalizer_preserves_terminal_source_failure_in_incomplete_cycle(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    _ = _seed_zero_record_runs(
        store,
        failed_source=KrCatalystSource.NEWS,
        failure_code="http_503",
    )

    result = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)

    assert result.cycle is not None
    assert result.cycle.complete is False
    news = next(
        item
        for item in result.cycle.coverage
        if item.source is KrCatalystSource.NEWS
    )
    assert news.status is KrCoverageStatus.FAILED
    assert news.record_count == 0
    assert news.failure_code == "http_503"
    assert result.appended is True
    assert store.cycles() == (result.cycle,)


def test_finalizer_does_not_append_cycle_when_any_source_run_is_missing(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    _ = _seed_zero_record_runs(store, omitted_source=KrCatalystSource.VOLUME_SURGE)

    result = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)

    assert result.cycle is None
    assert result.appended is False
    assert result.missing_sources == (KrCatalystSource.VOLUME_SURGE,)
    assert store.cycles() == ()


def test_finalizer_exact_restart_is_noop(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    _ = _seed_zero_record_runs(store)

    first = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)
    second = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)

    assert first.cycle == second.cycle
    assert first.appended is True
    assert second.appended is False
    assert len(store.cycles()) == 1


def test_finalizer_rejects_conflicting_existing_cycle(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    conflicting = _cycle(
        started_at=STARTED_AT - dt.timedelta(minutes=5),
        completed_at=STARTED_AT + dt.timedelta(minutes=10),
    )
    with store.writer() as writer:
        _ = writer.append_cycle(conflicting)
    _ = _seed_zero_record_runs(store)

    with pytest.raises(KrThemeConflictError):
        _ = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)

    assert store.cycles() == (conflicting,)


def test_finalizer_uses_store_time_validation_and_fails_closed(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    payload = b'{"report_nm":"synthetic disclosure"}'
    receipt_at = STARTED_AT + dt.timedelta(seconds=1)
    receipt = KrSourceReceipt(
        source_run_id=f"{CYCLE_ID}:dart",
        source=KrCatalystSource.DART,
        request_key="opendart:list:20260715:page:1",
        received_at=receipt_at,
        http_status=200,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    observed_at = receipt_at + dt.timedelta(minutes=3)
    record = KrCatalystRecord(
        source=KrCatalystSource.DART,
        source_record_id="opendart://disclosure/20260715000001",
        publisher_id="00123456",
        published_at=None,
        first_observed_at=observed_at,
        content_type="application/json",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    observation = KrCatalystObservation(
        collection_cycle_id=CYCLE_ID,
        catalyst_id=record.catalyst_id,
        observed_at=observed_at,
    )
    dart_run = _run(
        KrCatalystSource.DART,
        completed_at=STARTED_AT + dt.timedelta(minutes=2),
        record_count=1,
        receipt_ids=(receipt.receipt_id,),
    )
    with store.writer() as writer:
        _ = writer.append_source_receipt(receipt, payload)
        _ = writer.append_catalyst_from_receipt(
            record,
            observation,
            payload,
            receipt_id=receipt.receipt_id,
            item_index=0,
        )
        _ = writer.append_source_run(dart_run)
        for source in KrCatalystSource:
            if source is not KrCatalystSource.DART:
                _ = writer.append_source_run(_run(source))

    with pytest.raises(InvalidKrThemeSourceError):
        _ = finalize_kr_source_cycle(store, collection_cycle_id=CYCLE_ID)

    assert store.cycles() == ()


def test_finalizer_rejects_invalid_cycle_id_before_creating_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kr-theme.sqlite3"

    with pytest.raises(ValueError, match="cycle ID"):
        _ = finalize_kr_source_cycle(
            KrThemeStore(database),
            collection_cycle_id="../escape",
        )

    assert database.exists() is False


def test_finalizer_migrates_v1_before_reading_source_runs(tmp_path: Path) -> None:
    database = tmp_path / "kr-theme.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_KR_THEME_SCHEMA_V1)
        _ = connection.execute("PRAGMA user_version = 1")

    result = finalize_kr_source_cycle(
        KrThemeStore(database),
        collection_cycle_id=CYCLE_ID,
    )

    assert result.cycle is None
    assert result.missing_sources == tuple(
        sorted(KrCatalystSource, key=lambda item: item.value)
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


def _seed_zero_record_runs(
    store: KrThemeStore,
    *,
    omitted_source: KrCatalystSource | None = None,
    failed_source: KrCatalystSource | None = None,
    failure_code: str | None = None,
) -> tuple[KrSourceCollectionRun, ...]:
    runs = tuple(
        _run(
            source,
            status=(
                KrCoverageStatus.FAILED
                if source is failed_source
                else KrCoverageStatus.SUCCESS
            ),
            failure_code=failure_code if source is failed_source else None,
        )
        for source in KrCatalystSource
        if source is not omitted_source
    )
    with store.writer() as writer:
        for run in runs:
            _ = writer.append_source_run(run)
    return runs


def _run(
    source: KrCatalystSource,
    *,
    completed_at: dt.datetime | None = None,
    status: KrCoverageStatus = KrCoverageStatus.SUCCESS,
    failure_code: str | None = None,
    record_count: int = 0,
    receipt_ids: tuple[str, ...] = (),
) -> KrSourceCollectionRun:
    offset = tuple(KrCatalystSource).index(source)
    started_at = STARTED_AT + dt.timedelta(seconds=offset)
    return KrSourceCollectionRun(
        source_run_id=f"{CYCLE_ID}:{source.value}",
        collection_cycle_id=CYCLE_ID,
        source=source,
        adapter_version=f"{source.value}-fixture-v1",
        started_at=started_at,
        completed_at=completed_at or started_at + dt.timedelta(minutes=1),
        status=status,
        record_count=record_count,
        failure_code=failure_code,
        receipt_ids=receipt_ids,
    )


def _cycle(
    *,
    started_at: dt.datetime,
    completed_at: dt.datetime,
) -> KrCatalystCollectionCycle:
    return KrCatalystCollectionCycle(
        collection_cycle_id=CYCLE_ID,
        started_at=started_at,
        completed_at=completed_at,
        coverage=tuple(
            KrSourceCoverage(
                source=source,
                status=KrCoverageStatus.SUCCESS,
                record_count=0,
                failure_code=None,
            )
            for source in sorted(KrCatalystSource, key=lambda item: item.value)
        ),
    )
