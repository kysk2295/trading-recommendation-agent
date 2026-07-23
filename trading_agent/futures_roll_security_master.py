from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.futures_roll_security_master_models import (
    FuturesRollContract,
    FuturesRollManifest,
    FuturesRollSecurityMaster,
    FuturesRollSecurityMasterError,
    FuturesSettlementType,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)

_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


def load_futures_roll_security_master(
    manifest_path: Path,
) -> FuturesRollSecurityMaster:
    try:
        raw = read_private_bytes_query_only(
            manifest_path,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
        manifest = FuturesRollManifest.model_validate_json(raw)
        return FuturesRollSecurityMaster(
            root_symbol=manifest.root_symbol,
            source_observed_at=manifest.source_observed_at,
            source_reference=manifest.source_reference,
            source_manifest_sha256=hashlib.sha256(raw).hexdigest(),
            contracts=manifest.contracts,
        )
    except FuturesRollSecurityMasterError:
        raise
    except (
        InvalidPrivateQueryBytesError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise FuturesRollSecurityMasterError from None


def resolve_active_futures_contract(
    master: FuturesRollSecurityMaster,
    as_of: dt.datetime,
) -> FuturesRollContract:
    try:
        checked = FuturesRollSecurityMaster.model_validate(master.model_dump(mode="python"))
        if not _aware(as_of) or as_of < checked.source_observed_at:
            raise FuturesRollSecurityMasterError
        matches = tuple(item for item in checked.contracts if item.active_from <= as_of < item.roll_at)
        if len(matches) != 1:
            raise FuturesRollSecurityMasterError
        return matches[0]
    except FuturesRollSecurityMasterError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise FuturesRollSecurityMasterError from None


def publish_futures_roll_security_master(
    output_root: Path,
    master: FuturesRollSecurityMaster,
) -> tuple[Path, bool]:
    try:
        checked = FuturesRollSecurityMaster.model_validate(master.model_dump(mode="python"))
        path = output_root / (f"futures_roll_security_master_{checked.master_id}.json")
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except FuturesRollSecurityMasterError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise FuturesRollSecurityMasterError from None


__all__ = (
    "FuturesRollContract",
    "FuturesRollManifest",
    "FuturesRollSecurityMaster",
    "FuturesRollSecurityMasterError",
    "FuturesSettlementType",
    "load_futures_roll_security_master",
    "publish_futures_roll_security_master",
    "resolve_active_futures_contract",
)
