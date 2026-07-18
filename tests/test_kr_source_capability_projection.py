from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.data_capability_models import (
    DataCorrectionPolicy,
    DataHealthState,
)
from trading_agent.kis_kr_ranking_collection import KIS_KR_RANKING_ADAPTER_VERSION
from trading_agent.kr_source_capability_projection import (
    KrSourceCapabilityProjectionError,
    project_kr_source_capabilities,
)
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_volume_surge import KR_VOLUME_SURGE_ADAPTER_VERSION
from trading_agent.ls_nws_collection import LS_NWS_ADAPTER_VERSION
from trading_agent.opendart_collection import OPENDART_ADAPTER_VERSION

UTC = dt.UTC
COLLECTION_DATE = dt.date(2026, 7, 17)
STARTED_AT = dt.datetime(2026, 7, 17, 0, 30, tzinfo=UTC)
COMPLETED_AT = STARTED_AT + dt.timedelta(seconds=1)
CYCLE_ID = "kr-health-cycle-001"
VERSIONS = {
    KrCatalystSource.DART: OPENDART_ADAPTER_VERSION,
    KrCatalystSource.NEWS: LS_NWS_ADAPTER_VERSION,
    KrCatalystSource.KIS_RANKING: KIS_KR_RANKING_ADAPTER_VERSION,
    KrCatalystSource.VOLUME_SURGE: KR_VOLUME_SURGE_ADAPTER_VERSION,
}
SUFFIXES = {
    KrCatalystSource.DART: "dart",
    KrCatalystSource.NEWS: "news",
    KrCatalystSource.KIS_RANKING: "kis_ranking",
    KrCatalystSource.VOLUME_SURGE: "volume_surge",
}
SOURCE_ORDER = (
    KrCatalystSource.DART,
    KrCatalystSource.NEWS,
    KrCatalystSource.KIS_RANKING,
    KrCatalystSource.VOLUME_SURGE,
)


def test_projects_complete_sparse_sources_with_heartbeat_not_fake_event() -> None:
    runs = _runs()

    projection = project_kr_source_capabilities(runs)

    assert projection.complete is True
    assert tuple(item.source_id.canonical_id for item in projection.capabilities) == (
        "kis/kr_ranking",
        "local/kr_volume_surge",
        "ls/nws",
        "opendart/list",
    )
    assert all(item.health_state is DataHealthState.COMPLETE for item in projection.capabilities)
    assert all(item.latest_event_received_at is None for item in projection.capabilities)
    assert all(item.latest_source_heartbeat_at == COMPLETED_AT for item in projection.capabilities)
    assert tuple(item.source_id for item in projection.entitlements) == tuple(
        item.source_id for item in projection.capabilities
    )
    retention_by_source = {
        item.source_id.canonical_id: item.retention.correction_policy for item in projection.capabilities
    }
    assert retention_by_source == {
        "kis/kr_ranking": DataCorrectionPolicy.APPEND_CORRECTION,
        "local/kr_volume_surge": DataCorrectionPolicy.APPEND_CORRECTION,
        "ls/nws": DataCorrectionPolicy.APPEND_TOMBSTONE,
        "opendart/list": DataCorrectionPolicy.APPEND_CORRECTION,
    }


def test_failed_terminal_run_is_preserved_as_failed_health() -> None:
    projection = project_kr_source_capabilities(_runs(failed_source=KrCatalystSource.NEWS))
    by_source = {item.source_id.canonical_id: item for item in projection.capabilities}

    assert projection.complete is False
    assert by_source["ls/nws"].health_state is DataHealthState.FAILED
    assert by_source["ls/nws"].observed_completeness_bps == 0
    assert by_source["ls/nws"].latest_event_received_at is None
    assert by_source["ls/nws"].latest_source_heartbeat_at == COMPLETED_AT


@pytest.mark.parametrize(
    "mutation",
    ("missing", "duplicate", "cycle", "date", "adapter", "source_run_id"),
)
def test_rejects_nonterminal_or_mixed_source_run_set(mutation: str) -> None:
    runs = list(_runs())
    if mutation == "missing":
        runs.pop()
    elif mutation == "duplicate":
        runs[-1] = runs[0]
    else:
        payload = runs[0].model_dump(mode="python")
        if mutation == "cycle":
            payload["collection_cycle_id"] = "other-cycle"
        elif mutation == "date":
            payload["collection_date"] = COLLECTION_DATE - dt.timedelta(days=1)
        elif mutation == "source_run_id":
            payload["source_run_id"] = f"{CYCLE_ID}:other"
        else:
            payload["adapter_version"] = "other-v1"
        runs[0] = KrSourceCollectionRun.model_validate(payload)

    with pytest.raises(KrSourceCapabilityProjectionError):
        project_kr_source_capabilities(tuple(runs))


def _runs(
    *,
    failed_source: KrCatalystSource | None = None,
) -> tuple[KrSourceCollectionRun, ...]:
    return tuple(_run(source, failed=source is failed_source) for source in SOURCE_ORDER)


def _run(source: KrCatalystSource, *, failed: bool) -> KrSourceCollectionRun:
    return KrSourceCollectionRun(
        source_run_id=f"{CYCLE_ID}:{SUFFIXES[source]}",
        collection_cycle_id=CYCLE_ID,
        source=source,
        adapter_version=VERSIONS[source],
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        status=KrCoverageStatus.FAILED if failed else KrCoverageStatus.SUCCESS,
        record_count=0,
        failure_code="fixture_failure" if failed else None,
        receipt_ids=(),
        collection_date=COLLECTION_DATE,
    )
