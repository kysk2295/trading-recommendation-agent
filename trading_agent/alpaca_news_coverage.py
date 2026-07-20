from __future__ import annotations

from typing import assert_never

from trading_agent.alpaca_news_coverage_models import (
    AlpacaNewsCoverageAssessment,
    AlpacaNewsCoverageContractError,
    AlpacaNewsCoverageManifest,
    AlpacaNewsCoverageSlice,
    AlpacaNewsCoverageSliceStatus,
)
from trading_agent.alpaca_news_models import (
    AlpacaNewsRequest,
    AlpacaNewsRun,
    AlpacaNewsRunStatus,
)
from trading_agent.alpaca_news_store import AlpacaNewsStore


def assess_alpaca_news_coverage(
    manifest: AlpacaNewsCoverageManifest,
    store: AlpacaNewsStore,
) -> AlpacaNewsCoverageAssessment:
    slices = tuple(
        _slice(request, store.run(request.request_id), manifest)
        for request in manifest.requests
    )
    request_by_id = {item.request_id: item for item in manifest.requests}
    successful_symbols = sum(
        len(request_by_id[item.request_id].symbols)
        for item in slices
        if item.status is AlpacaNewsCoverageSliceStatus.SUCCESS
    )
    accepted_articles = sum(
        item.article_count
        for item in slices
        if item.status is AlpacaNewsCoverageSliceStatus.SUCCESS
    )
    latest = max(
        (
            item.latest_event_at
            for item in slices
            if item.status is AlpacaNewsCoverageSliceStatus.SUCCESS
            and item.latest_event_at is not None
        ),
        default=None,
    )
    return AlpacaNewsCoverageAssessment(
        manifest_id=manifest.manifest_id,
        universe_id=manifest.universe_id,
        assessed_at=manifest.cutoff_at,
        slices=slices,
        declared_symbol_count=len(manifest.symbols),
        successful_symbol_count=successful_symbols,
        completeness_bps=successful_symbols * 10_000 // len(manifest.symbols),
        accepted_article_count=accepted_articles,
        latest_event_at=latest,
    )


def require_alpaca_news_coverage_assessment(
    manifest: AlpacaNewsCoverageManifest,
    assessment: AlpacaNewsCoverageAssessment,
    store: AlpacaNewsStore,
) -> None:
    expected = assess_alpaca_news_coverage(manifest, store)
    if assessment != expected:
        raise AlpacaNewsCoverageContractError


def _slice(
    request: AlpacaNewsRequest,
    run: AlpacaNewsRun | None,
    manifest: AlpacaNewsCoverageManifest,
) -> AlpacaNewsCoverageSlice:
    if run is None or run.completed_at > manifest.cutoff_at:
        return AlpacaNewsCoverageSlice(
            request_id=request.request_id,
            status=AlpacaNewsCoverageSliceStatus.MISSING,
            run_id=None,
            completed_at=None,
            page_count=0,
            article_count=0,
            latest_event_at=None,
            failure_code=None,
        )
    if run.request != request:
        raise AlpacaNewsCoverageContractError
    match run.status:
        case AlpacaNewsRunStatus.SUCCESS:
            status = AlpacaNewsCoverageSliceStatus.SUCCESS
        case AlpacaNewsRunStatus.FAILED:
            status = AlpacaNewsCoverageSliceStatus.FAILED
        case unreachable:
            assert_never(unreachable)
    return AlpacaNewsCoverageSlice(
        request_id=request.request_id,
        status=status,
        run_id=run.run_id,
        completed_at=run.completed_at,
        page_count=run.page_count,
        article_count=run.article_count,
        latest_event_at=run.latest_event_at,
        failure_code=run.failure_code,
    )


__all__ = (
    "assess_alpaca_news_coverage",
    "require_alpaca_news_coverage_assessment",
)
