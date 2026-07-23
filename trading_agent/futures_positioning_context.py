from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from pydantic import BaseModel, ValidationError

from trading_agent.cftc_tff_models import CftcTffPositioningContext
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.futures_positioning_context_models import (
    FuturesPositioningBinding,
    FuturesPositioningContext,
    FuturesPositioningContextError,
    FuturesPositioningJoinRequest,
    LoadedCftcTffContext,
    LoadedFuturesPositioningBinding,
    LoadedFuturesRollMaster,
)
from trading_agent.futures_roll_security_master import (
    FuturesRollSecurityMaster,
    resolve_active_futures_contract,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)

_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


def load_cftc_tff_context_artifact(path: Path) -> LoadedCftcTffContext:
    try:
        raw = read_private_bytes_query_only(path, max_bytes=_MAX_ARTIFACT_BYTES)
        value = CftcTffPositioningContext.model_validate_json(raw)
        _require_canonical_artifact(
            path,
            raw,
            value,
            prefix="cftc_tff_context",
            semantic_id=value.context_id,
        )
        return LoadedCftcTffContext(
            value=value,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except FuturesPositioningContextError:
        raise
    except (
        InvalidPrivateQueryBytesError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise FuturesPositioningContextError from None


def load_futures_roll_master_artifact(
    path: Path,
) -> LoadedFuturesRollMaster:
    try:
        raw = read_private_bytes_query_only(path, max_bytes=_MAX_ARTIFACT_BYTES)
        value = FuturesRollSecurityMaster.model_validate_json(raw)
        _require_canonical_artifact(
            path,
            raw,
            value,
            prefix="futures_roll_security_master",
            semantic_id=value.master_id,
        )
        return LoadedFuturesRollMaster(
            value=value,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except FuturesPositioningContextError:
        raise
    except (
        InvalidPrivateQueryBytesError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise FuturesPositioningContextError from None


def load_futures_positioning_binding(
    path: Path,
) -> LoadedFuturesPositioningBinding:
    try:
        raw = read_private_bytes_query_only(path, max_bytes=_MAX_ARTIFACT_BYTES)
        value = FuturesPositioningBinding.model_validate_json(raw)
        expected_raw = (canonical_experiment_ledger_json(value) + "\n").encode()
        if raw != expected_raw:
            raise FuturesPositioningContextError
        return LoadedFuturesPositioningBinding(
            value=value,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except FuturesPositioningContextError:
        raise
    except (
        InvalidPrivateQueryBytesError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise FuturesPositioningContextError from None


def build_futures_positioning_context(
    request: FuturesPositioningJoinRequest,
) -> FuturesPositioningContext:
    try:
        checked = FuturesPositioningJoinRequest.model_validate(
            request.model_dump(mode="python"),
        )
        cftc = checked.cftc.value
        master = checked.futures_master.value
        binding = checked.binding.value
        as_of_date = checked.as_of.astimezone(dt.UTC).date()
        report_age = as_of_date - cftc.latest_report_date
        if (
            binding.cftc_contract_market_code != cftc.contract_market_code
            or binding.root_symbol != master.root_symbol
            or binding.venue != master.contracts[0].instrument.venue
            or binding.observed_at > checked.as_of
            or binding.effective_from > checked.as_of
            or (binding.effective_to is not None and checked.as_of >= binding.effective_to)
            or cftc.observed_at > checked.as_of
            or report_age.days < 0
            or report_age.days > checked.maximum_report_age_days
        ):
            raise FuturesPositioningContextError
        active = resolve_active_futures_contract(master, checked.as_of)
        return FuturesPositioningContext(
            as_of=checked.as_of,
            maximum_report_age_days=checked.maximum_report_age_days,
            binding_artifact_sha256=checked.binding.artifact_sha256,
            cftc_context_id=cftc.context_id,
            cftc_artifact_sha256=checked.cftc.artifact_sha256,
            futures_master_id=master.master_id,
            futures_master_artifact_sha256=(checked.futures_master.artifact_sha256),
            cftc_contract_market_code=cftc.contract_market_code,
            root_symbol=master.root_symbol,
            active_instrument=active.instrument,
            active_provider_alias=active.provider_alias,
            active_from=active.active_from,
            roll_at=active.roll_at,
            latest_report_date=cftc.latest_report_date,
            previous_report_date=cftc.previous_report_date,
            cftc_observed_at=cftc.observed_at,
            categories=cftc.categories,
        )
    except FuturesPositioningContextError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise FuturesPositioningContextError from None


def publish_futures_positioning_context(
    output_root: Path,
    context: FuturesPositioningContext,
) -> tuple[Path, bool]:
    try:
        checked = FuturesPositioningContext.model_validate(
            context.model_dump(mode="python"),
        )
        path = output_root / (f"futures_positioning_context_{checked.context_id}.json")
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except FuturesPositioningContextError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise FuturesPositioningContextError from None


def _require_canonical_artifact(
    path: Path,
    raw: bytes,
    value: BaseModel,
    *,
    prefix: str,
    semantic_id: str,
) -> None:
    expected_name = f"{prefix}_{semantic_id}.json"
    expected_raw = (canonical_experiment_ledger_json(value) + "\n").encode()
    if path.name != expected_name or raw != expected_raw:
        raise FuturesPositioningContextError


__all__ = (
    "build_futures_positioning_context",
    "load_cftc_tff_context_artifact",
    "load_futures_positioning_binding",
    "load_futures_roll_master_artifact",
    "publish_futures_positioning_context",
)
