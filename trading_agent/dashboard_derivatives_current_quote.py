from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.alpaca_option_chain_models import OptionChainStatus, OptionFeed
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore, AlpacaOptionChainStoreError
from trading_agent.alpaca_option_contract_models import OptionCatalogStatus
from trading_agent.alpaca_option_contract_store import (
    AlpacaOptionContractStore,
    AlpacaOptionContractStoreError,
)
from trading_agent.dashboard_derivatives_section import DerivativesSection
from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)

_MAX_AUTHORITY_BYTES: Final = 32_768
_REQUEST_ID_QUERIES: Final = {
    "chain": "SELECT request_id FROM alpaca_option_chain_runs ORDER BY rowid DESC LIMIT 1",
    "catalog": "SELECT request_id FROM alpaca_option_contract_runs ORDER BY rowid DESC LIMIT 1",
}


class CurrentOptionQuoteAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    chain_run_id: str
    catalog_run_id: str
    entitlement: Literal["active_realtime", "expired"]
    redistribution: Literal["allowed", "research_only"]
    capability_health: Literal["healthy", "degraded", "failed"]
    capability_observed_at: dt.datetime
    capability_ttl_seconds: int = Field(ge=1, le=1_200)
    quote_observed_at: dt.datetime
    quote_ttl_seconds: int = Field(ge=1, le=120)
    safe_ref: str

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if (
            any(
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
                for value in (self.chain_run_id, self.catalog_run_id, self.safe_ref)
            )
            or not _aware(self.capability_observed_at)
            or not _aware(self.quote_observed_at)
            or self.quote_observed_at > self.capability_observed_at
        ):
            raise InvalidCurrentOptionQuoteAuthorityError
        return self

    @property
    def authority_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class InvalidCurrentOptionQuoteAuthorityError(ValueError):
    pass


def read_current_option_quotes(outputs: Path, now: dt.datetime) -> DerivativesSection:
    root = outputs / "derivatives"
    paths = tuple(sorted(root.glob("option_current_authority_*.json")))
    if not paths:
        return DerivativesSection("empty", None, None, (), (), ())
    try:
        authorities = tuple(_authority(path) for path in paths)
        authority = max(authorities, key=lambda item: item.capability_observed_at)
        chain_id = _latest_id(root / "option-chain.sqlite3", "chain")
        catalog_id = _latest_id(root / "option-contracts.sqlite3", "catalog")
        if chain_id is None or catalog_id is None:
            raise InvalidCurrentOptionQuoteAuthorityError
        chain = AlpacaOptionChainStore(root / "option-chain.sqlite3").run(chain_id)
        catalog = AlpacaOptionContractStore(root / "option-contracts.sqlite3").run(catalog_id)
        if chain is None or catalog is None:
            raise InvalidCurrentOptionQuoteAuthorityError
        if (
            chain.run_id != authority.chain_run_id
            or catalog.run_id != authority.catalog_run_id
            or chain.request.feed is not OptionFeed.OPRA
            or chain.status is not OptionChainStatus.SUCCESS
            or catalog.status is not OptionCatalogStatus.SUCCESS
            or authority.capability_observed_at > now + dt.timedelta(minutes=5)
            or authority.quote_observed_at > now + dt.timedelta(minutes=5)
        ):
            raise InvalidCurrentOptionQuoteAuthorityError
    except (
        AlpacaOptionChainStoreError,
        AlpacaOptionContractStoreError,
        InvalidCurrentOptionQuoteAuthorityError,
        InvalidPrivateQueryBytesError,
        ValidationError,
        sqlite3.Error,
        ValueError,
    ):
        return _corrupt(now)
    current = (
        authority.entitlement == "active_realtime"
        and authority.redistribution == "allowed"
        and authority.capability_health == "healthy"
        and now - authority.capability_observed_at <= dt.timedelta(seconds=authority.capability_ttl_seconds)
        and now - authority.quote_observed_at <= dt.timedelta(seconds=authority.quote_ttl_seconds)
    )
    if not current:
        source = _node(authority, "blocked")
        terminal = _blocker(
            node_id=f"{source.node_id}.blocker",
            label="Current quote authority is not licensed and fresh",
            observed_at=authority.quote_observed_at,
            safe_ref=authority.safe_ref,
        )
        return DerivativesSection(
            "blocked",
            "current_quote_not_licensed",
            authority.quote_observed_at,
            (),
            (source, terminal),
            (TraceEdgeV2(from_node_id=source.node_id, to_node_id=terminal.node_id, kind="blocked_by"),),
        )
    source_id = "trace.derivatives.options.current"
    gate_values = (
        "entitlement:active_realtime",
        "redistribution:allowed",
        "capability:healthy_current",
        "quote:fresh",
    )
    items = tuple(
        WorkspaceItemV2(
            item_id=f"derivative.quote.authority.{index}",
            kind="derivative",
            label="Current quote authority",
            state="populated",
            value=value,
            observed_at=authority.quote_observed_at,
            trace_id=source_id,
        )
        for index, value in enumerate(gate_values)
    ) + tuple(
        WorkspaceItemV2(
            item_id=f"derivative.quote.{index}",
            kind="derivative",
            label=snapshot.symbol,
            state="populated",
            value=f"{snapshot.latest_quote.bid_price} / {snapshot.latest_quote.ask_price}",
            observed_at=snapshot.latest_quote.timestamp,
            trace_id=source_id,
        )
        for index, snapshot in enumerate(chain.snapshots[:8])
        if snapshot.latest_quote is not None
    )
    return DerivativesSection(
        "populated" if len(items) > len(gate_values) else "empty",
        None,
        authority.quote_observed_at,
        items,
        (_node(authority, "accepted"),),
        (),
    )


def _authority(path: Path) -> CurrentOptionQuoteAuthority:
    value = CurrentOptionQuoteAuthority.model_validate_json(
        read_private_bytes_query_only(path, max_bytes=_MAX_AUTHORITY_BYTES)
    )
    if (
        path.name != f"option_current_authority_{value.authority_id}.json"
        or read_private_bytes_query_only(path, max_bytes=_MAX_AUTHORITY_BYTES)
        != (canonical_experiment_ledger_json(value) + "\n").encode()
    ):
        raise InvalidCurrentOptionQuoteAuthorityError
    return value


def _latest_id(path: Path, source: Literal["chain", "catalog"]) -> str | None:
    if not path.is_file():
        return None
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        _ = connection.execute("PRAGMA query_only = ON")
        row: tuple[str] | None = connection.execute(_REQUEST_ID_QUERIES[source]).fetchone()
    return None if row is None else row[0]


def _node(authority: CurrentOptionQuoteAuthority, state: Literal["accepted", "blocked"]) -> TraceNodeV2:
    return TraceNodeV2(
        node_id="trace.derivatives.options.current",
        kind="source_receipt",
        label="Current OPRA quote authority",
        observed_at=authority.quote_observed_at,
        safe_ref=authority.safe_ref,
        state=state,
        source_namespace="derivatives.options.current",
    )


def _corrupt(now: dt.datetime) -> DerivativesSection:
    source = TraceNodeV2(
        node_id="trace.derivatives.options.current",
        kind="source_receipt",
        label="Current OPRA quote authority",
        observed_at=now,
        safe_ref="0" * 64,
        state="failed",
        source_namespace="derivatives.options.current",
    )
    terminal = _blocker(
        node_id=f"{source.node_id}.blocker",
        label="Current quote authority invalid",
        observed_at=now,
        safe_ref="0" * 64,
    )
    return DerivativesSection(
        "corrupt",
        "options_current_authority_invalid",
        now,
        (),
        (source, terminal),
        (TraceEdgeV2(from_node_id=source.node_id, to_node_id=terminal.node_id, kind="blocked_by"),),
    )


def _blocker(*, node_id: str, label: str, observed_at: dt.datetime, safe_ref: str) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind="blocker_terminal",
        label=label,
        observed_at=observed_at,
        safe_ref=safe_ref,
        state="blocked",
        source_namespace="derivatives.options.current",
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = ("CurrentOptionQuoteAuthority", "read_current_option_quotes")
