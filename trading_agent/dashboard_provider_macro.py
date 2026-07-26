from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Final, Literal, assert_never

from trading_agent.cftc_tff_models import CftcTffStatus
from trading_agent.cftc_tff_store import CftcTffStore, CftcTffStoreError
from trading_agent.dashboard_provider_evidence import (
    ProviderEvidence,
    ProviderName,
    unavailable_provider,
)
from trading_agent.fred_alfred_collection import FredArtifactStore, FredStoreError
from trading_agent.fred_alfred_models import FredRunStatus
from trading_agent.treasury_yield_models import TreasuryYieldStatus
from trading_agent.treasury_yield_store import (
    TreasuryYieldStore,
    TreasuryYieldStoreError,
)

_REQUEST_ID_QUERIES: Final = {
    "treasury_yield_runs": (
        "SELECT request_id FROM treasury_yield_runs ORDER BY rowid DESC LIMIT 1"
    ),
    "cftc_tff_runs": "SELECT request_id FROM cftc_tff_runs ORDER BY rowid DESC LIMIT 1",
}


def read_fred_provider(
    outputs: Path,
    provider: ProviderName,
    now: dt.datetime,
) -> ProviderEvidence:
    root = outputs / "source_evidence" / "fred_alfred" / provider
    terminals = tuple(sorted(root.glob("*.terminal.json")))
    if not terminals:
        return unavailable_provider(provider, f"{provider}_receipt_missing")
    try:
        request_id = terminals[-1].name.removesuffix(".terminal.json")
        terminal = FredArtifactStore(root).terminal(request_id)
    except (FredStoreError, OSError):
        return _corrupt(provider, f"{provider}_receipt_invalid", now)
    if terminal is None or terminal.request.source_mode.value != provider:
        return _corrupt(provider, f"{provider}_receipt_invalid", now)
    if terminal.completed_at > now + dt.timedelta(minutes=5):
        return _corrupt(provider, f"{provider}_future_observation", now)
    match terminal.status:
        case FredRunStatus.SUCCESS:
            snapshot = terminal.snapshot
            if snapshot is None:
                return _corrupt(provider, f"{provider}_receipt_invalid", now)
            stale = now - terminal.completed_at > dt.timedelta(days=2)
            return ProviderEvidence(
                provider,
                "stale" if stale else "populated",
                "research_only",
                terminal.completed_at,
                f"{snapshot.series_id}:{snapshot.available_observation_count}",
                snapshot.snapshot_id,
                f"{provider}_receipt_stale" if stale else None,
            )
        case FredRunStatus.FAILED:
            return ProviderEvidence(
                provider,
                "error",
                "research_only",
                terminal.completed_at,
                terminal.failure.value if terminal.failure is not None else "failed",
                request_id,
                f"{provider}_collection_failed",
            )
        case unreachable:
            assert_never(unreachable)


def read_treasury_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    provider: ProviderName = "treasury"
    path = outputs / "source_evidence" / "treasury-yield.sqlite3"
    request_id = _latest_request_id(path, "treasury_yield_runs")
    if request_id is None:
        return unavailable_provider(provider, "treasury_receipt_missing")
    try:
        run = TreasuryYieldStore(path).run(request_id)
    except TreasuryYieldStoreError:
        return _corrupt(provider, "treasury_curve_invalid", now)
    if run is None or run.completed_at > now + dt.timedelta(minutes=5):
        return _corrupt(provider, "treasury_curve_invalid", now)
    match run.status:
        case TreasuryYieldStatus.SUCCESS:
            context = run.context
            if context is None:
                return _corrupt(provider, "treasury_curve_invalid", now)
            stale = now.date() - context.latest_date > dt.timedelta(days=4)
            return ProviderEvidence(
                provider,
                "stale" if stale else "populated",
                "research_only",
                context.observed_at,
                f"{context.latest_date.isoformat()}:{context.ten_year_minus_two_year_bps}",
                context.context_id,
                "treasury_curve_stale" if stale else None,
            )
        case TreasuryYieldStatus.FAILED:
            return _failed(provider, run.completed_at, request_id, "treasury_collection_failed")
        case unreachable:
            assert_never(unreachable)


def read_cftc_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    provider: ProviderName = "cftc"
    path = outputs / "source_evidence" / "cftc-tff.sqlite3"
    request_id = _latest_request_id(path, "cftc_tff_runs")
    if request_id is None:
        return unavailable_provider(provider, "cftc_receipt_missing")
    try:
        run = CftcTffStore(path).run(request_id)
    except CftcTffStoreError:
        return _corrupt(provider, "cftc_report_invalid", now)
    if run is None or run.completed_at > now + dt.timedelta(minutes=5):
        return _corrupt(provider, "cftc_report_invalid", now)
    match run.status:
        case CftcTffStatus.SUCCESS:
            context = run.context
            if context is None:
                return _corrupt(provider, "cftc_report_invalid", now)
            stale = now.date() - context.latest_report_date > dt.timedelta(days=10)
            return ProviderEvidence(
                provider,
                "stale" if stale else "populated",
                "research_only",
                context.observed_at,
                f"{context.contract_market_code}:{context.latest_open_interest}",
                context.context_id,
                "cftc_report_stale" if stale else None,
            )
        case CftcTffStatus.FAILED:
            return _failed(provider, run.completed_at, request_id, "cftc_collection_failed")
        case unreachable:
            assert_never(unreachable)


def _latest_request_id(
    path: Path,
    table: Literal["treasury_yield_runs", "cftc_tff_runs"],
) -> str | None:
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row: tuple[str] | None = connection.execute(_REQUEST_ID_QUERIES[table]).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else row[0]


def _corrupt(
    provider: ProviderName,
    blocker_code: str,
    now: dt.datetime,
) -> ProviderEvidence:
    return ProviderEvidence(provider, "corrupt", "unavailable", now, None, "0" * 64, blocker_code)


def _failed(
    provider: ProviderName,
    observed_at: dt.datetime,
    safe_ref: str,
    blocker_code: str,
) -> ProviderEvidence:
    return ProviderEvidence(
        provider,
        "error",
        "research_only",
        observed_at,
        "failed",
        safe_ref,
        blocker_code,
    )


__all__ = (
    "read_cftc_provider",
    "read_fred_provider",
    "read_treasury_provider",
)
