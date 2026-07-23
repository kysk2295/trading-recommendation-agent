from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.alpaca_option_surface import (
    AlpacaOptionSurface,
    OptionSurfaceStatus,
)
from trading_agent.alpaca_option_term_structure_models import (
    AlpacaOptionTermStructure,
    AlpacaOptionTermStructureError,
    OptionTermSlice,
    OptionTermStructureStatus,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)

_MAX_SURFACE_BYTES = 32 * 1024 * 1024
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class _LoadedSurface:
    surface: AlpacaOptionSurface
    raw_sha256: str


def build_alpaca_option_term_structure(
    surface_paths: tuple[Path, ...],
    maximum_observation_skew_seconds: int,
) -> AlpacaOptionTermStructure:
    try:
        if not 2 <= len(surface_paths) <= 32:
            raise AlpacaOptionTermStructureError
        loaded = tuple(_load_surface(path) for path in surface_paths)
        first = loaded[0].surface
        if any(
            item.surface.status is not OptionSurfaceStatus.READY
            or item.surface.underlying_symbol != first.underlying_symbol
            or item.surface.feed is not first.feed
            for item in loaded
        ):
            raise AlpacaOptionTermStructureError
        as_of = max(item.surface.surface_observed_at for item in loaded)
        market_date = as_of.astimezone(_NEW_YORK).date()
        slices = tuple(
            sorted(
                (_term_slice(item, market_date) for item in loaded),
                key=lambda item: (
                    item.expiration_date,
                    item.contract_type.value,
                ),
            )
        )
        return AlpacaOptionTermStructure(
            status=OptionTermStructureStatus.READY,
            feed=first.feed,
            underlying_symbol=first.underlying_symbol,
            market_date=market_date,
            as_of=as_of,
            maximum_observation_skew_seconds=maximum_observation_skew_seconds,
            expiration_count=len({item.expiration_date for item in slices}),
            surface_count=len(slices),
            slices=slices,
        )
    except AlpacaOptionTermStructureError:
        raise
    except (
        IndexError,
        InvalidPrivateQueryBytesError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise AlpacaOptionTermStructureError from None


def publish_alpaca_option_term_structure(
    output_root: Path,
    structure: AlpacaOptionTermStructure,
) -> tuple[Path, bool]:
    try:
        checked = AlpacaOptionTermStructure.model_validate(structure.model_dump())
        path = output_root / (
            f"option_term_structure_{checked.term_structure_id}.json"
        )
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except AlpacaOptionTermStructureError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise AlpacaOptionTermStructureError from None


def _load_surface(path: Path) -> _LoadedSurface:
    payload = read_private_bytes_query_only(path, max_bytes=_MAX_SURFACE_BYTES)
    surface = AlpacaOptionSurface.model_validate_json(payload)
    if path.name != f"option_surface_{surface.surface_id}.json":
        raise AlpacaOptionTermStructureError
    return _LoadedSurface(
        surface=surface,
        raw_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _term_slice(
    loaded: _LoadedSurface,
    market_date,
) -> OptionTermSlice:
    surface = loaded.surface
    open_interests = tuple(
        item.open_interest
        for item in surface.contracts
        if item.open_interest is not None
    )
    open_interest_dates = {
        item.open_interest_date
        for item in surface.contracts
        if item.open_interest_date is not None
    }
    implied_volatilities = tuple(
        item.implied_volatility
        for item in surface.contracts
        if item.implied_volatility is not None
    )
    if (
        not open_interests
        or len(open_interest_dates) != 1
        or not implied_volatilities
    ):
        raise AlpacaOptionTermStructureError
    return OptionTermSlice(
        surface_id=surface.surface_id,
        surface_sha256=loaded.raw_sha256,
        expiration_date=surface.expiration_date,
        contract_type=surface.contract_type,
        days_to_expiry=(surface.expiration_date - market_date).days,
        surface_observed_at=surface.surface_observed_at,
        contract_count=surface.master_contract_count,
        open_interest_observation_count=len(open_interests),
        open_interest_date=open_interest_dates.pop(),
        total_open_interest=sum(open_interests),
        implied_volatility_observation_count=len(implied_volatilities),
        median_implied_volatility=_median(implied_volatilities),
    )


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = tuple(sorted(values))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


__all__ = (
    "build_alpaca_option_term_structure",
    "publish_alpaca_option_term_structure",
)
