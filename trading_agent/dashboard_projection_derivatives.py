from __future__ import annotations

import datetime as dt
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection, blocked_projection
from trading_agent.futures_roll_security_master_models import (
    FuturesRollSecurityMaster,
    FuturesRollSecurityMasterError,
)

FUTURES_MASTER_FILE = "futures-roll-security-master.v1.json"


def project_derivatives(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    path = outputs / "derivatives" / FUTURES_MASTER_FILE
    if not path.exists():
        return blocked_projection(
            "derivatives",
            now=now,
            state="unavailable",
            blocker_code="futures_master_missing",
        )
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
        return blocked_projection(
            "derivatives",
            now=now,
            state="corrupt",
            blocker_code="futures_master_invalid",
        )
    if master.source_observed_at > now + dt.timedelta(minutes=5):
        return blocked_projection(
            "derivatives",
            now=now,
            state="corrupt",
            blocker_code="derivative_future_observation",
        )
    stale = now - master.source_observed_at > dt.timedelta(days=7)
    source_id = "trace.derivatives.futures_master"
    items = tuple(
        WorkspaceItemV2(
            item_id=f"derivative.future.{index}",
            kind="derivative",
            label=contract.root_symbol,
            state="stale" if stale else "populated",
            value=contract.expiration_date.isoformat(),
            observed_at=contract.observed_at,
            trace_id=source_id,
        )
        for index, contract in enumerate(master.contracts[:24])
    )
    total = len(master.contracts)
    return WorkspaceProjection(
        SourceStateV2(
            state="stale" if stale else "populated",
            observed_at=master.source_observed_at,
            freshness=FreshnessV2(
                policy_id="futures-security-master-v1",
                age_seconds=min(
                    max(0, int((now - master.source_observed_at).total_seconds())),
                    31_536_000,
                ),
                as_of=now,
            ),
            blocker_code=None,
            summary="Authoritative futures roll security master projected",
            total_count=total,
            projected_count=len(items),
            truncated=total > len(items),
            trace_id=source_id,
            items=items,
        ),
        (
            TraceNodeV2(
                node_id=source_id,
                kind="source_receipt",
                label="Futures roll security master",
                observed_at=master.source_observed_at,
                safe_ref=master.source_manifest_sha256,
                state="accepted",
                source_namespace="derivatives.futures_master",
            ),
        ),
        (),
    )
