from __future__ import annotations

import datetime as dt
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from trading_agent.dashboard_derivatives_section import DerivativesSection
from trading_agent.dashboard_models_v2 import (
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_provider_macro import read_cftc_provider
from trading_agent.futures_roll_security_master_models import (
    FuturesRollSecurityMaster,
    FuturesRollSecurityMasterError,
)

FUTURES_MASTER_FILE = "futures-roll-security-master.v1.json"


def read_futures_section(outputs: Path, now: dt.datetime) -> DerivativesSection:
    path = outputs / "derivatives" / FUTURES_MASTER_FILE
    if not path.exists():
        return _missing(now, "futures_master_missing")
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise FuturesRollSecurityMasterError
        master = FuturesRollSecurityMaster.model_validate_json(path.read_bytes())
    except (FuturesRollSecurityMasterError, OSError, ValidationError, ValueError):
        return _missing(now, "futures_master_invalid", corrupt=True)
    if master.source_observed_at > now + dt.timedelta(minutes=5):
        return _missing(now, "derivative_future_observation", corrupt=True)
    cftc = read_cftc_provider(outputs, now)
    source_id = "trace.derivatives.futures_master"
    items = tuple(
        WorkspaceItemV2(
            item_id=f"derivative.future.{index}",
            kind="derivative",
            label=contract.root_symbol,
            state="populated",
            value=f"{contract.expiration_date.isoformat()}:{contract.roll_at.isoformat()}",
            observed_at=contract.observed_at,
            trace_id=source_id,
        )
        for index, contract in enumerate(master.contracts[:24])
    )
    nodes = [
        TraceNodeV2(
            node_id=source_id,
            kind="source_receipt",
            label="Futures roll security master",
            observed_at=master.source_observed_at,
            safe_ref=master.source_manifest_sha256,
            state="accepted",
            source_namespace="derivatives.futures_master",
        )
    ]
    edges: list[TraceEdgeV2] = []
    blocker = cftc.blocker_code
    if cftc.observed_at is not None and cftc.value is not None:
        items += (
            WorkspaceItemV2(
                item_id="derivative.cftc.positioning",
                kind="derivative",
                label="CFTC positioning",
                state=cftc.state,
                value=cftc.value,
                observed_at=cftc.observed_at,
                trace_id="trace.derivatives.cftc",
            ),
        )
        nodes.append(
            TraceNodeV2(
                node_id="trace.derivatives.cftc",
                kind="source_receipt",
                label="Typed CFTC positioning authority",
                observed_at=cftc.observed_at,
                safe_ref=cftc.safe_ref,
                state="accepted",
                source_namespace="derivatives.cftc",
            )
        )
    if blocker is not None:
        nodes.append(
            TraceNodeV2(
                node_id=f"{source_id}.blocker",
                kind="blocker_terminal",
                label="Futures positioning blocker",
                observed_at=cftc.observed_at or now,
                safe_ref=cftc.safe_ref,
                state="blocked",
                source_namespace="derivatives.futures_master",
            )
        )
        edges.append(
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=f"{source_id}.blocker",
                kind="blocked_by",
            )
        )
    stale = now - master.source_observed_at > dt.timedelta(days=7)
    return DerivativesSection(
        "blocked" if blocker is not None else "stale" if stale else "populated",
        blocker or ("futures_master_stale" if stale else None),
        max(master.source_observed_at, cftc.observed_at or master.source_observed_at),
        items,
        tuple(nodes),
        tuple(edges),
    )


def _missing(
    now: dt.datetime,
    blocker: str,
    *,
    corrupt: bool = False,
) -> DerivativesSection:
    source_id = "trace.derivatives.futures_master"
    safe_ref = "0" * 64
    nodes = (
        TraceNodeV2(
            node_id=source_id,
            kind="source_receipt",
            label="Futures roll authority",
            observed_at=now,
            safe_ref=safe_ref,
            state="unavailable",
            source_namespace="derivatives.futures_master",
        ),
        TraceNodeV2(
            node_id=f"{source_id}.blocker",
            kind="blocker_terminal",
            label="Futures authority blocker",
            observed_at=now,
            safe_ref=safe_ref,
            state="blocked",
            source_namespace="derivatives.futures_master",
        ),
    )
    return DerivativesSection(
        "corrupt" if corrupt else "unavailable",
        blocker,
        now if corrupt else None,
        (),
        nodes,
        (TraceEdgeV2(from_node_id=source_id, to_node_id=f"{source_id}.blocker", kind="blocked_by"),),
    )


__all__ = ("read_futures_section",)
