from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from trading_agent.alpaca_option_chain_capability import (
    AlpacaOptionChainCapabilityError,
    project_alpaca_option_chain_capability,
)
from trading_agent.alpaca_option_chain_store import (
    AlpacaOptionChainStore,
    AlpacaOptionChainStoreError,
)
from trading_agent.dashboard_provider_evidence import (
    ProviderEvidence,
    unavailable_provider,
)
from trading_agent.data_capability_models import RedistributionPolicy


def read_alpaca_provider(outputs: Path, now: dt.datetime) -> ProviderEvidence:
    path = outputs / "derivatives" / "option-chain.sqlite3"
    request_id = _latest_request_id(path)
    if request_id is None:
        return unavailable_provider("alpaca", "alpaca_capability_missing")
    try:
        run = AlpacaOptionChainStore(path).run(request_id)
        if run is None:
            return unavailable_provider("alpaca", "alpaca_capability_missing")
        projection = project_alpaca_option_chain_capability(run)
    except (
        AlpacaOptionChainCapabilityError,
        AlpacaOptionChainStoreError,
        sqlite3.Error,
        ValueError,
    ):
        return ProviderEvidence(
            "alpaca",
            "corrupt",
            "unavailable",
            now,
            None,
            "0" * 64,
            "alpaca_receipt_invalid",
        )
    if run.completed_at > now + dt.timedelta(minutes=5):
        return ProviderEvidence(
            "alpaca",
            "corrupt",
            "unavailable",
            now,
            None,
            run.run_id,
            "alpaca_future_observation",
        )
    stale = now - run.completed_at > dt.timedelta(minutes=20)
    redistribution_blocked = (
        projection.entitlement.redistribution_policy is RedistributionPolicy.NONE
    )
    return ProviderEvidence(
        "alpaca",
        "blocked"
        if redistribution_blocked
        else "stale"
        if stale
        else "populated",
        "research_only",
        run.completed_at,
        f"{run.request.feed.value}:{len(run.snapshots)}",
        run.run_id,
        "redistribution_not_allowed"
        if redistribution_blocked
        else "alpaca_runtime_stale"
        if stale
        else None,
    )


def _latest_request_id(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row: tuple[str] | None = connection.execute(
                "SELECT request_id FROM alpaca_option_chain_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else row[0]


__all__ = ("read_alpaca_provider",)
