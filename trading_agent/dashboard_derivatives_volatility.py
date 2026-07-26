from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from trading_agent.alpaca_option_skew_models import AlpacaOptionSkew
from trading_agent.alpaca_option_surface import AlpacaOptionSurface
from trading_agent.alpaca_option_term_structure_models import AlpacaOptionTermStructure
from trading_agent.dashboard_derivatives_section import DerivativesSection
from trading_agent.dashboard_models_v2 import (
    SourceStateName,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)

_MAX_ARTIFACT_BYTES: Final = 16 * 1024 * 1024
_POINT_CAP: Final = 16


class InvalidDashboardVolatilityArtifactError(ValueError):
    pass


def read_volatility_section(outputs: Path, now: dt.datetime) -> DerivativesSection:
    root = outputs / "derivatives"
    paths = tuple(
        sorted(
            (
                *root.glob("option_surface_*.json"),
                *root.glob("option_skew_*.json"),
                *root.glob("option_term_structure_*.json"),
            )
        )
    )
    if not paths:
        return DerivativesSection("empty", None, None, (), (), ())
    try:
        surfaces = tuple(_surface(path) for path in paths if path.name.startswith("option_surface_"))
        skews = tuple(_skew(path) for path in paths if path.name.startswith("option_skew_"))
        terms = tuple(_term(path) for path in paths if path.name.startswith("option_term_structure_"))
        observed = (
            *(surface.surface_observed_at for surface in surfaces),
            *(skew.as_of for skew in skews),
            *(term.as_of for term in terms),
        )
        if not observed or max(observed) > now + dt.timedelta(minutes=5):
            return _invalid(now, "derivative_future_observation")
        surface_ids = {surface.surface_id for surface in surfaces}
        if any(
            skew.call_surface_id not in surface_ids or skew.put_surface_id not in surface_ids for skew in skews
        ) or any(slice_.surface_id not in surface_ids for term in terms for slice_ in term.slices):
            return _invalid(now, "derivative_epoch_mismatch")
    except (
        InvalidDashboardVolatilityArtifactError,
        InvalidPrivateQueryBytesError,
        ValidationError,
        ValueError,
    ):
        return _invalid(now, "options_receipt_invalid")
    latest = max(observed)
    stale = now - latest > dt.timedelta(minutes=20)
    state = "stale" if stale else "blocked"
    blocker = "derivative_surface_stale" if stale else "current_quote_not_licensed"
    source_id = "trace.derivatives.volatility"
    items = _items(surfaces, skews, terms, latest, state)
    terminal_id = f"{source_id}.blocker"
    nodes = (
        TraceNodeV2(
            node_id=source_id,
            kind="source_receipt",
            label="Bound option IV, skew and term artifacts",
            observed_at=latest,
            safe_ref=(
                terms[-1].term_structure_id if terms else skews[-1].skew_id if skews else surfaces[-1].surface_id
            ),
            state="accepted",
            source_namespace="derivatives.volatility",
        ),
        TraceNodeV2(
            node_id=terminal_id,
            kind="blocker_terminal",
            label="Current quote authority gate",
            observed_at=latest,
            safe_ref=(surfaces[-1].chain_run_sha256 if surfaces else "0" * 64),
            state="blocked",
            source_namespace="derivatives.volatility",
        ),
    )
    return DerivativesSection(
        state,
        blocker,
        latest,
        items,
        nodes,
        (TraceEdgeV2(from_node_id=source_id, to_node_id=terminal_id, kind="blocked_by"),),
    )


def _surface(path: Path) -> AlpacaOptionSurface:
    value = AlpacaOptionSurface.model_validate_json(read_private_bytes_query_only(path, max_bytes=_MAX_ARTIFACT_BYTES))
    if path.name != f"option_surface_{value.surface_id}.json":
        raise InvalidDashboardVolatilityArtifactError
    return value


def _skew(path: Path) -> AlpacaOptionSkew:
    value = AlpacaOptionSkew.model_validate_json(read_private_bytes_query_only(path, max_bytes=_MAX_ARTIFACT_BYTES))
    if path.name != f"option_skew_{value.skew_id}.json":
        raise InvalidDashboardVolatilityArtifactError
    return value


def _term(path: Path) -> AlpacaOptionTermStructure:
    value = AlpacaOptionTermStructure.model_validate_json(
        read_private_bytes_query_only(path, max_bytes=_MAX_ARTIFACT_BYTES)
    )
    if path.name != f"option_term_structure_{value.term_structure_id}.json":
        raise InvalidDashboardVolatilityArtifactError
    return value


def _items(
    surfaces: tuple[AlpacaOptionSurface, ...],
    skews: tuple[AlpacaOptionSkew, ...],
    terms: tuple[AlpacaOptionTermStructure, ...],
    observed_at: dt.datetime,
    state: SourceStateName,
) -> tuple[WorkspaceItemV2, ...]:
    values = [
        (
            f"derivative.iv.{surface.surface_id[:12]}.{index}",
            f"{contract.root_symbol} {contract.strike_price} IV",
            str(contract.implied_volatility),
        )
        for surface in surfaces
        for index, contract in enumerate(surface.contracts)
        if contract.implied_volatility is not None
    ]
    values.extend(
        (
            f"derivative.skew.{skew.skew_id[:12]}.{index}",
            f"{skew.underlying_symbol} skew {bucket.bucket_id}",
            str(bucket.median_put_minus_call_iv),
        )
        for skew in skews
        for index, bucket in enumerate(skew.strike_buckets)
    )
    values.extend(
        (
            f"derivative.term.{term.term_structure_id[:12]}.{index}",
            f"{term.underlying_symbol} term {slice_.expiration_date.isoformat()}",
            str(slice_.median_implied_volatility),
        )
        for term in terms
        for index, slice_ in enumerate(term.slices)
    )
    return tuple(
        WorkspaceItemV2(
            item_id=item_id,
            kind="derivative",
            label=label,
            state=state,
            value=value,
            observed_at=observed_at,
            trace_id="trace.derivatives.volatility",
        )
        for item_id, label, value in values[:_POINT_CAP]
    )


def _invalid(now: dt.datetime, blocker: str) -> DerivativesSection:
    source_id = "trace.derivatives.volatility"
    terminal_id = f"{source_id}.blocker"
    nodes = (
        TraceNodeV2(
            node_id=source_id,
            kind="source_receipt",
            label="Option volatility artifact authority",
            observed_at=now,
            safe_ref="0" * 64,
            state="failed",
            source_namespace="derivatives.volatility",
        ),
        TraceNodeV2(
            node_id=terminal_id,
            kind="blocker_terminal",
            label="Option volatility integrity blocker",
            observed_at=now,
            safe_ref="0" * 64,
            state="blocked",
            source_namespace="derivatives.volatility",
        ),
    )
    return DerivativesSection(
        "corrupt",
        blocker,
        now,
        (),
        nodes,
        (TraceEdgeV2(from_node_id=source_id, to_node_id=terminal_id, kind="blocked_by"),),
    )


__all__ = ("read_volatility_section",)
