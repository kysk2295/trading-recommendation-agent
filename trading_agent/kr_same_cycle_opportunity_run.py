from __future__ import annotations

import datetime as dt

from pydantic import ValidationError

from trading_agent.kr_same_cycle_opportunity_bundle import (
    RUN_MANIFEST_NAME,
    load_opportunity_bundle,
    opportunity_bundle_path,
    write_opportunity_bundle,
)
from trading_agent.kr_same_cycle_opportunity_models import (
    SAFE_KR_OPPORTUNITY_ID,
    InvalidKrSameCycleOpportunityRunError,
    KrSameCycleOpportunityPolicy,
    KrSameCycleOpportunityPreparation,
    PreparedKrSameCycleOpportunityRun,
    load_kr_same_cycle_opportunity_policy,
)
from trading_agent.kr_source_cycle_orchestrator import (
    has_terminal_kr_source_runs,
)
from trading_agent.kr_theme_models import (
    KrCatalystCollectionCycle,
    KrSourceCoverage,
)
from trading_agent.kr_theme_store import KrThemeStore


def prepare_kr_same_cycle_opportunity_run(
    store: KrThemeStore,
    request: KrSameCycleOpportunityPreparation,
    policy: KrSameCycleOpportunityPolicy,
) -> PreparedKrSameCycleOpportunityRun:
    try:
        checked = KrSameCycleOpportunityPolicy.model_validate(policy.model_dump(mode="python"))
        cycle = _exact_cycle(store, request)
        bundle = opportunity_bundle_path(request)
        manifest_path = bundle / RUN_MANIFEST_NAME
        if manifest_path.exists() or manifest_path.is_symlink():
            return load_opportunity_bundle(bundle, request, checked, replayed=True)
        _require_fresh_first_projection(cycle, request, checked)
        write_opportunity_bundle(bundle, request, checked)
        return load_opportunity_bundle(bundle, request, checked, replayed=False)
    except InvalidKrSameCycleOpportunityRunError:
        raise
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrSameCycleOpportunityRunError from None


def _exact_cycle(
    store: KrThemeStore,
    request: KrSameCycleOpportunityPreparation,
) -> KrCatalystCollectionCycle:
    if (
        SAFE_KR_OPPORTUNITY_ID.fullmatch(request.collection_cycle_id) is None
        or isinstance(request.collection_date, dt.datetime)
        or not isinstance(request.collection_date, dt.date)
        or not _aware(request.prepared_at)
        or not request.run_root.is_absolute()
        or not has_terminal_kr_source_runs(
            store,
            collection_cycle_id=request.collection_cycle_id,
            collection_date=request.collection_date,
        )
    ):
        raise InvalidKrSameCycleOpportunityRunError
    runs = store.source_runs(request.collection_cycle_id)
    cycles = tuple(item for item in store.cycles() if item.collection_cycle_id == request.collection_cycle_id)
    if len(runs) != 4 or len(cycles) != 1:
        raise InvalidKrSameCycleOpportunityRunError
    expected = KrCatalystCollectionCycle(
        collection_cycle_id=request.collection_cycle_id,
        started_at=min(item.started_at for item in runs),
        completed_at=max(item.completed_at for item in runs),
        coverage=tuple(
            KrSourceCoverage(
                source=item.source,
                status=item.status,
                record_count=item.record_count,
                failure_code=item.failure_code,
            )
            for item in sorted(runs, key=lambda value: value.source.value)
        ),
    )
    if not expected.complete or cycles[0] != expected:
        raise InvalidKrSameCycleOpportunityRunError
    return expected


def _require_fresh_first_projection(
    cycle: KrCatalystCollectionCycle,
    request: KrSameCycleOpportunityPreparation,
    policy: KrSameCycleOpportunityPolicy,
) -> None:
    delay = request.prepared_at - cycle.completed_at
    if delay < dt.timedelta(0) or delay > dt.timedelta(seconds=policy.maximum_cycle_age_seconds):
        raise InvalidKrSameCycleOpportunityRunError


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidKrSameCycleOpportunityRunError",
    "KrSameCycleOpportunityPolicy",
    "KrSameCycleOpportunityPreparation",
    "PreparedKrSameCycleOpportunityRun",
    "load_kr_same_cycle_opportunity_policy",
    "prepare_kr_same_cycle_opportunity_run",
)
