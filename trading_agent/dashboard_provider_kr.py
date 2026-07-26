from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import assert_never

from trading_agent.dashboard_provider_evidence import (
    ProviderEvidence,
    ProviderName,
    unavailable_provider,
)
from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
)
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeReader,
    UnsupportedKrThemeSchemaError,
)


def read_opendart_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    return _read_kr_source(outputs, now, "opendart", KrCatalystSource.DART)


def read_kis_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    return _read_kr_source(outputs, now, "kis", KrCatalystSource.KIS_RANKING)


def read_ls_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    return _read_kr_source(outputs, now, "ls", KrCatalystSource.NEWS)


def _read_kr_source(
    outputs: Path,
    now: dt.datetime,
    provider: ProviderName,
    source: KrCatalystSource,
) -> ProviderEvidence:
    reader = KrThemeReader(outputs / "live_sessions" / "kr_theme.sqlite3")
    if not reader.is_initialized():
        return unavailable_provider(provider, f"{provider}_run_missing")
    try:
        runs = tuple(run for run in reader.source_runs() if run.source is source)
    except (InvalidKrThemeSourceError, UnsupportedKrThemeSchemaError):
        return _corrupt(provider, now)
    if not runs:
        return unavailable_provider(provider, f"{provider}_run_missing")
    run = max(runs, key=lambda item: item.completed_at)
    if run.completed_at > now + dt.timedelta(minutes=5):
        return _corrupt(provider, now)
    safe_ref = hashlib.sha256("".join(run.receipt_ids).encode()).hexdigest()
    stale = now - run.completed_at > dt.timedelta(days=2)
    match run.status:
        case KrCoverageStatus.SUCCESS:
            return ProviderEvidence(
                provider,
                "stale" if stale else "empty" if run.record_count == 0 else "populated",
                "research_only",
                run.completed_at,
                str(run.record_count),
                safe_ref,
                f"{provider}_receipt_stale" if stale else None,
            )
        case KrCoverageStatus.FAILED:
            return ProviderEvidence(
                provider,
                "error",
                "research_only",
                run.completed_at,
                run.failure_code,
                safe_ref,
                f"{provider}_collection_failed",
            )
        case unreachable:
            assert_never(unreachable)


def _corrupt(provider: ProviderName, now: dt.datetime) -> ProviderEvidence:
    return ProviderEvidence(
        provider,
        "corrupt",
        "unavailable",
        now,
        None,
        "0" * 64,
        f"{provider}_receipt_invalid",
    )


__all__ = (
    "read_kis_provider",
    "read_ls_provider",
    "read_opendart_provider",
)
