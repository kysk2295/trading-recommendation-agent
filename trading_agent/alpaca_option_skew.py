from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.alpaca_option_skew_buckets import (
    build_delta_skew_buckets,
    build_strike_skew_buckets,
)
from trading_agent.alpaca_option_skew_models import (
    AlpacaOptionSkew,
    AlpacaOptionSkewError,
    OptionSkewStatus,
)
from trading_agent.alpaca_option_skew_spot import (
    select_source_backed_spot,
)
from trading_agent.alpaca_option_surface import (
    AlpacaOptionSurface,
    OptionSurfaceStatus,
)
from trading_agent.canonical_dataset_event_reader import (
    replay_canonical_dataset_events,
)
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplayError
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeError,
)
from trading_agent.us_market_data_runtime_receipt_query import (
    read_market_data_runtime_receipts,
)

_MAX_SURFACE_BYTES = 32 * 1024 * 1024
_SOURCE_ID = "alpaca.sip.us_equities"


@dataclass(frozen=True, slots=True)
class _LoadedSurface:
    surface: AlpacaOptionSurface
    raw_sha256: str


def build_alpaca_option_skew(
    call_surface_path: Path,
    put_surface_path: Path,
    spot_runtime_store_path: Path,
    spot_dataset_path: Path,
    maximum_observation_skew_seconds: int,
) -> AlpacaOptionSkew:
    try:
        _require_private_runtime_store(spot_runtime_store_path)
        call = _load_surface(call_surface_path)
        put = _load_surface(put_surface_path)
        _validate_surfaces(call.surface, put.surface)
        underlying_instrument_id = _underlying_instrument_id(
            call.surface,
            put.surface,
        )
        replay, events = replay_canonical_dataset_events(spot_dataset_path)
        spot = select_source_backed_spot(
            read_market_data_runtime_receipts(
                spot_runtime_store_path,
                _SOURCE_ID,
                underlying_instrument_id,
            ),
            events,
            call.surface,
            put.surface,
            underlying_instrument_id,
        )
        observed = (
            call.surface.surface_observed_at,
            put.surface.surface_observed_at,
            spot.receipt.completed_bar.end_at,
        )
        return AlpacaOptionSkew(
            status=OptionSkewStatus.READY,
            feed=call.surface.feed,
            underlying_symbol=call.surface.underlying_symbol,
            underlying_instrument_id=underlying_instrument_id,
            expiration_date=call.surface.expiration_date,
            call_surface_id=call.surface.surface_id,
            call_surface_sha256=call.raw_sha256,
            call_surface_observed_at=call.surface.surface_observed_at,
            put_surface_id=put.surface.surface_id,
            put_surface_sha256=put.raw_sha256,
            put_surface_observed_at=put.surface.surface_observed_at,
            spot_dataset_id=replay.dataset_id,
            spot_dataset_parquet_sha256=replay.parquet_sha256,
            spot_dataset_event_content_sha256=(replay.canonical_event_content_sha256),
            spot_event_id=spot.event.event_id,
            spot_event_content_hash=spot.event.content_hash,
            spot_raw_receipt_ref=spot.event.raw_receipt_ref,
            spot_runtime_receipt_id=spot.receipt.receipt_id,
            spot_runtime_payload_sha256=spot.receipt.payload_sha256,
            spot_bar_started_at=spot.receipt.completed_bar.start_at,
            spot_bar_completed_at=spot.receipt.completed_bar.end_at,
            spot_price=spot.receipt.completed_bar.close,
            as_of=max(observed),
            maximum_observation_skew_seconds=(maximum_observation_skew_seconds),
            observation_skew_seconds=Decimal(str((max(observed) - min(observed)).total_seconds())),
            strike_buckets=build_strike_skew_buckets(
                call.surface,
                put.surface,
                spot.receipt.completed_bar.close,
            ),
            delta_buckets=build_delta_skew_buckets(
                call.surface,
                put.surface,
            ),
        )
    except AlpacaOptionSkewError:
        raise
    except (
        CanonicalDatasetReplayError,
        InvalidPrivateDirectoryIdentityError,
        InvalidPrivateQueryBytesError,
        MarketDataRuntimeError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise AlpacaOptionSkewError from None


def publish_alpaca_option_skew(
    output_root: Path,
    skew: AlpacaOptionSkew,
) -> tuple[Path, bool]:
    try:
        checked = AlpacaOptionSkew.model_validate(skew.model_dump())
        path = output_root / f"option_skew_{checked.skew_id}.json"
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except AlpacaOptionSkewError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise AlpacaOptionSkewError from None


def _load_surface(path: Path) -> _LoadedSurface:
    payload = read_private_bytes_query_only(
        path,
        max_bytes=_MAX_SURFACE_BYTES,
    )
    surface = AlpacaOptionSurface.model_validate_json(payload)
    if path.name != f"option_surface_{surface.surface_id}.json":
        raise AlpacaOptionSkewError
    return _LoadedSurface(surface, hashlib.sha256(payload).hexdigest())


def _require_private_runtime_store(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaOptionSkewError
    parent = open_private_parent(path.parent, create=False)
    try:
        require_private_directory_query_only(parent)
    finally:
        os.close(parent)


def _validate_surfaces(
    call: AlpacaOptionSurface,
    put: AlpacaOptionSurface,
) -> None:
    if (
        call.status is not OptionSurfaceStatus.READY
        or put.status is not OptionSurfaceStatus.READY
        or call.contract_type is not OptionContractType.CALL
        or put.contract_type is not OptionContractType.PUT
        or call.underlying_symbol != put.underlying_symbol
        or call.feed is not put.feed
        or call.expiration_date != put.expiration_date
        or call.surface_id == put.surface_id
    ):
        raise AlpacaOptionSkewError


def _underlying_instrument_id(
    call: AlpacaOptionSurface,
    put: AlpacaOptionSurface,
) -> str:
    identifiers = {contract.underlying_instrument_id for surface in (call, put) for contract in surface.contracts}
    if len(identifiers) != 1:
        raise AlpacaOptionSkewError
    return identifiers.pop()


__all__ = (
    "build_alpaca_option_skew",
    "publish_alpaca_option_skew",
)
