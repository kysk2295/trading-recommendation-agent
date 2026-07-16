from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, override

from trading_agent.kis_kr_ranking_collection import (
    KIS_KR_RANKING_ADAPTER_VERSION,
)
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_source_cycle import finalize_kr_source_cycle
from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.kr_volume_surge import KR_VOLUME_SURGE_ADAPTER_VERSION
from trading_agent.ls_nws_collection import LS_NWS_ADAPTER_VERSION
from trading_agent.opendart_collection import OPENDART_ADAPTER_VERSION

_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SOURCE_ORDER: Final = (
    KrCatalystSource.DART,
    KrCatalystSource.NEWS,
    KrCatalystSource.KIS_RANKING,
    KrCatalystSource.VOLUME_SURGE,
)
_ADAPTER_VERSIONS: Final = {
    KrCatalystSource.DART: OPENDART_ADAPTER_VERSION,
    KrCatalystSource.NEWS: LS_NWS_ADAPTER_VERSION,
    KrCatalystSource.KIS_RANKING: KIS_KR_RANKING_ADAPTER_VERSION,
    KrCatalystSource.VOLUME_SURGE: KR_VOLUME_SURGE_ADAPTER_VERSION,
}
_SOURCE_RUN_SUFFIXES: Final = {
    KrCatalystSource.DART: "dart",
    KrCatalystSource.NEWS: "news",
    KrCatalystSource.KIS_RANKING: "kis_ranking",
    KrCatalystSource.VOLUME_SURGE: "volume_surge",
}

type KrSourceStageRunner = Callable[[], None]


class KrSourceCycleOrchestrationError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR source cycle orchestration을 안전하게 완료할 수 없습니다"


@dataclass(frozen=True, slots=True)
class KrSourceCycleStage:
    source: KrCatalystSource
    status: KrCoverageStatus
    record_count: int
    failure_code: str | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class KrSourceCycleOrchestration:
    stages: tuple[KrSourceCycleStage, ...]
    cycle: KrCatalystCollectionCycle
    appended: bool


def orchestrate_kr_source_cycle(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
    stage_runners: Mapping[KrCatalystSource, KrSourceStageRunner],
) -> KrSourceCycleOrchestration:
    _validate_request(collection_cycle_id, collection_date, stage_runners)
    stages: list[KrSourceCycleStage] = []
    for source in _SOURCE_ORDER:
        run = _exact_terminal_run(
            store,
            source=source,
            collection_cycle_id=collection_cycle_id,
            collection_date=collection_date,
        )
        replayed = run is not None
        if run is None:
            try:
                stage_runners[source]()
            except Exception:
                raise KrSourceCycleOrchestrationError from None
            run = _exact_terminal_run(
                store,
                source=source,
                collection_cycle_id=collection_cycle_id,
                collection_date=collection_date,
            )
            if run is None:
                raise KrSourceCycleOrchestrationError
        stages.append(
            KrSourceCycleStage(
                source=source,
                status=run.status,
                record_count=run.record_count,
                failure_code=run.failure_code,
                replayed=replayed,
            )
        )

    finalization = finalize_kr_source_cycle(
        store,
        collection_cycle_id=collection_cycle_id,
    )
    if finalization.cycle is None or finalization.missing_sources:
        raise KrSourceCycleOrchestrationError
    return KrSourceCycleOrchestration(
        stages=tuple(stages),
        cycle=finalization.cycle,
        appended=finalization.appended,
    )


def has_terminal_kr_source_runs(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
    collection_date: dt.date,
) -> bool:
    _validate_identity(collection_cycle_id, collection_date)
    return all(
        _exact_terminal_run(
            store,
            source=source,
            collection_cycle_id=collection_cycle_id,
            collection_date=collection_date,
        )
        is not None
        for source in _SOURCE_ORDER
    )


def _validate_request(
    collection_cycle_id: str,
    collection_date: dt.date,
    stage_runners: Mapping[KrCatalystSource, KrSourceStageRunner],
) -> None:
    _validate_identity(collection_cycle_id, collection_date)
    if set(stage_runners) != set(_SOURCE_ORDER):
        raise KrSourceCycleOrchestrationError


def _validate_identity(
    collection_cycle_id: str,
    collection_date: dt.date,
) -> None:
    if (
        _SAFE_ID.fullmatch(collection_cycle_id) is None
        or isinstance(collection_date, dt.datetime)
        or not isinstance(collection_date, dt.date)
    ):
        raise KrSourceCycleOrchestrationError


def _exact_terminal_run(
    store: KrThemeStore,
    *,
    source: KrCatalystSource,
    collection_cycle_id: str,
    collection_date: dt.date,
) -> KrSourceCollectionRun | None:
    runs = tuple(
        item
        for item in store.source_runs(collection_cycle_id)
        if item.source is source
    )
    if not runs:
        return None
    if len(runs) != 1:
        raise KrSourceCycleOrchestrationError
    run = runs[0]
    if (
        run.source_run_id
        != f"{collection_cycle_id}:{_SOURCE_RUN_SUFFIXES[source]}"
        or run.adapter_version != _ADAPTER_VERSIONS[source]
        or run.collection_date != collection_date
    ):
        raise KrSourceCycleOrchestrationError
    return run
