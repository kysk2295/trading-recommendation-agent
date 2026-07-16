from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pytest

from trading_agent.kis_kr_ranking_collection import (
    KIS_KR_RANKING_ADAPTER_VERSION,
)
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_source_cycle_orchestrator import (
    KrSourceCycleOrchestrationError,
    orchestrate_kr_source_cycle,
)
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.kr_volume_surge import KR_VOLUME_SURGE_ADAPTER_VERSION
from trading_agent.ls_nws_collection import LS_NWS_ADAPTER_VERSION
from trading_agent.opendart_collection import OPENDART_ADAPTER_VERSION

CYCLE_ID = "kr-orchestrator-20260716-001"
COLLECTION_DATE = dt.date(2026, 7, 16)
STARTED_AT = dt.datetime(2026, 7, 16, tzinfo=dt.UTC)
ORDER = (
    KrCatalystSource.DART,
    KrCatalystSource.NEWS,
    KrCatalystSource.KIS_RANKING,
    KrCatalystSource.VOLUME_SURGE,
)


def test_orchestrator_runs_every_source_in_order_then_appends_complete_cycle(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    calls: list[KrCatalystSource] = []

    result = orchestrate_kr_source_cycle(
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        stage_runners={
            source: _append_terminal_runner(
                store,
                source=source,
                calls=calls,
            )
            for source in ORDER
        },
    )

    assert calls == list(ORDER)
    assert tuple(item.source for item in result.stages) == ORDER
    assert all(not item.replayed for item in result.stages)
    assert result.cycle is not None
    assert result.cycle.complete is True
    assert result.appended is True
    assert store.cycles() == (result.cycle,)


def test_terminal_source_failure_continues_and_appends_incomplete_cycle(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    calls: list[KrCatalystSource] = []

    result = orchestrate_kr_source_cycle(
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        stage_runners={
            source: _append_terminal_runner(
                store,
                source=source,
                calls=calls,
                status=(
                    KrCoverageStatus.FAILED
                    if source is KrCatalystSource.DART
                    else KrCoverageStatus.SUCCESS
                ),
            )
            for source in ORDER
        },
    )

    assert calls == list(ORDER)
    assert result.cycle is not None
    assert result.cycle.complete is False
    assert result.stages[0].status is KrCoverageStatus.FAILED
    assert result.stages[0].failure_code == "transport_error"


def test_stage_that_does_not_leave_terminal_run_aborts_before_next_source(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    calls: list[KrCatalystSource] = []

    def incomplete_stage() -> None:
        calls.append(KrCatalystSource.NEWS)

    runners = {
        source: _append_terminal_runner(store, source=source, calls=calls)
        for source in ORDER
    }
    runners[KrCatalystSource.NEWS] = incomplete_stage

    with pytest.raises(KrSourceCycleOrchestrationError):
        _ = orchestrate_kr_source_cycle(
            store,
            collection_cycle_id=CYCLE_ID,
            collection_date=COLLECTION_DATE,
            stage_runners=runners,
        )

    assert calls == [KrCatalystSource.DART, KrCatalystSource.NEWS]
    assert store.cycles() == ()


def test_all_terminal_historical_replay_does_not_call_any_stage(
    tmp_path: Path,
) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    calls: list[KrCatalystSource] = []
    first = orchestrate_kr_source_cycle(
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        stage_runners={
            source: _append_terminal_runner(
                store,
                source=source,
                calls=calls,
            )
            for source in ORDER
        },
    )
    calls.clear()

    def reject_stage(source: KrCatalystSource) -> Callable[[], None]:
        def reject() -> None:
            raise AssertionError(f"terminal replay called {source.value}")

        return reject

    second = orchestrate_kr_source_cycle(
        store,
        collection_cycle_id=CYCLE_ID,
        collection_date=COLLECTION_DATE,
        stage_runners={source: reject_stage(source) for source in ORDER},
    )

    assert calls == []
    assert second.cycle == first.cycle
    assert second.appended is False
    assert all(item.replayed for item in second.stages)


def test_conflicting_terminal_run_fails_before_stage_invocation(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "kr-theme.sqlite3")
    with store.writer() as writer:
        _ = writer.append_source_run(
            _terminal_run(
                KrCatalystSource.DART,
                collection_date=COLLECTION_DATE - dt.timedelta(days=1),
            )
        )

    with pytest.raises(KrSourceCycleOrchestrationError):
        _ = orchestrate_kr_source_cycle(
            store,
            collection_cycle_id=CYCLE_ID,
            collection_date=COLLECTION_DATE,
            stage_runners={
                source: _reject_runner(source)
                for source in ORDER
            },
        )


def _append_terminal_runner(
    store: KrThemeStore,
    *,
    source: KrCatalystSource,
    calls: list[KrCatalystSource],
    status: KrCoverageStatus = KrCoverageStatus.SUCCESS,
) -> Callable[[], None]:
    def run() -> None:
        calls.append(source)
        with store.writer() as writer:
            _ = writer.append_source_run(_terminal_run(source, status=status))

    return run


def _reject_runner(source: KrCatalystSource) -> Callable[[], None]:
    def reject() -> None:
        raise AssertionError(f"conflict called {source.value}")

    return reject


def _terminal_run(
    source: KrCatalystSource,
    *,
    collection_date: dt.date = COLLECTION_DATE,
    status: KrCoverageStatus = KrCoverageStatus.SUCCESS,
) -> KrSourceCollectionRun:
    return KrSourceCollectionRun(
        source_run_id=f"{CYCLE_ID}:{source.value}",
        collection_cycle_id=CYCLE_ID,
        source=source,
        adapter_version=_adapter_version(source),
        started_at=STARTED_AT,
        completed_at=STARTED_AT,
        status=status,
        record_count=0,
        failure_code="transport_error" if status is KrCoverageStatus.FAILED else None,
        receipt_ids=(),
        collection_date=collection_date,
    )


def _adapter_version(source: KrCatalystSource) -> str:
    return {
        KrCatalystSource.DART: OPENDART_ADAPTER_VERSION,
        KrCatalystSource.NEWS: LS_NWS_ADAPTER_VERSION,
        KrCatalystSource.KIS_RANKING: KIS_KR_RANKING_ADAPTER_VERSION,
        KrCatalystSource.VOLUME_SURGE: KR_VOLUME_SURGE_ADAPTER_VERSION,
    }[source]
