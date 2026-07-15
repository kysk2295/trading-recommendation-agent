from __future__ import annotations

import re
from dataclasses import dataclass

from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrCatalystSource,
    KrSourceCoverage,
)
from trading_agent.kr_theme_store import KrThemeStore

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EXPECTED_SOURCES = tuple(sorted(KrCatalystSource, key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class KrSourceCycleFinalization:
    source_runs: tuple[KrSourceCollectionRun, ...]
    missing_sources: tuple[KrCatalystSource, ...]
    cycle: KrCatalystCollectionCycle | None
    appended: bool


def finalize_kr_source_cycle(
    store: KrThemeStore,
    *,
    collection_cycle_id: str,
) -> KrSourceCycleFinalization:
    if _SAFE_ID.fullmatch(collection_cycle_id) is None:
        raise ValueError("유효하지 않은 KR source cycle ID입니다")

    with store.writer() as writer:
        source_runs = tuple(
            sorted(
                store.source_runs(collection_cycle_id),
                key=lambda item: item.source.value,
            )
        )
        present_sources = {run.source for run in source_runs}
        missing_sources = tuple(
            source for source in _EXPECTED_SOURCES if source not in present_sources
        )
        if missing_sources:
            return KrSourceCycleFinalization(
                source_runs=source_runs,
                missing_sources=missing_sources,
                cycle=None,
                appended=False,
            )

        cycle = KrCatalystCollectionCycle(
            collection_cycle_id=collection_cycle_id,
            started_at=min(run.started_at for run in source_runs),
            completed_at=max(run.completed_at for run in source_runs),
            coverage=tuple(
                KrSourceCoverage(
                    source=run.source,
                    status=run.status,
                    record_count=run.record_count,
                    failure_code=run.failure_code,
                )
                for run in source_runs
            ),
        )
        appended = writer.append_cycle(cycle)

    return KrSourceCycleFinalization(
        source_runs=source_runs,
        missing_sources=(),
        cycle=cycle,
        appended=appended,
    )
