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
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import (
    InvalidKisKrMarketReceiptStoreError,
    KisKrMarketReceiptStore,
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
    return _read_kr_source(
        outputs / "live_sessions" / "opendart" / "kr_theme.sqlite3",
        now,
        "opendart",
        KrCatalystSource.DART,
    )


def read_kis_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    provider: ProviderName = "kis"
    path = outputs / "live_sessions" / "kis" / "market_receipts.sqlite3"
    try:
        receipts = KisKrMarketReceiptStore(path).receipts()
    except InvalidKisKrMarketReceiptStoreError:
        return _corrupt(provider, now)
    if not receipts:
        return unavailable_provider(provider, "kis_receipt_missing")
    latest = max(receipts, key=lambda item: item.received_at)
    selected = tuple(
        item
        for item in receipts
        if item.symbol == latest.symbol
        and latest.received_at - item.received_at <= dt.timedelta(minutes=5)
    )
    kinds = {item.kind for item in selected}
    safe_ref = hashlib.sha256(
        "".join(sorted(item.payload_sha256 for item in selected)).encode()
    ).hexdigest()
    if any(item.received_at > now + dt.timedelta(minutes=5) for item in selected):
        return _corrupt(provider, now)
    if any(item.status_code != 200 for item in selected):
        return ProviderEvidence(
            provider,
            "error",
            "research_only",
            latest.received_at,
            f"{latest.symbol}:{len(selected)}",
            safe_ref,
            "kis_collection_failed",
        )
    if kinds != set(KisKrMarketReceiptKind):
        return ProviderEvidence(
            provider,
            "blocked",
            "research_only",
            latest.received_at,
            f"{latest.symbol}:{len(selected)}",
            safe_ref,
            "kis_coverage_incomplete",
        )
    stale = now - latest.received_at > dt.timedelta(minutes=20)
    return ProviderEvidence(
        provider,
        "stale" if stale else "populated",
        "research_only",
        latest.received_at,
        f"{latest.symbol}:{len(selected)}",
        safe_ref,
        "kis_receipt_stale" if stale else None,
    )


def read_ls_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    return _read_kr_source(
        outputs / "live_sessions" / "ls" / "kr_theme.sqlite3",
        now,
        "ls",
        KrCatalystSource.NEWS,
    )


def _read_kr_source(
    path: Path,
    now: dt.datetime,
    provider: ProviderName,
    source: KrCatalystSource,
) -> ProviderEvidence:
    reader = KrThemeReader(path)
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
    snapshot = reader.source_receipt_projection_snapshot(
        collection_cycle_id=run.collection_cycle_id,
        source=source,
    )
    if snapshot is None or snapshot.run != run:
        return _corrupt(provider, now)
    receipt_ids = tuple(item.receipt.receipt_id for item in snapshot.receipts)
    if receipt_ids != run.receipt_ids:
        return _corrupt(provider, now)
    safe_ref = hashlib.sha256("".join(receipt_ids).encode()).hexdigest()
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
