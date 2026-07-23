#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_alpaca_option_chain_capability_registry.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.alpaca_option_chain_capability import (
    AlpacaOptionChainCapabilityError,
    project_alpaca_option_chain_capability,
)
from trading_agent.alpaca_option_chain_models import (
    AlpacaOptionChainError,
    OptionChainRequest,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import (
    AlpacaOptionChainStore,
    AlpacaOptionChainStoreError,
)
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "alpaca_option_chain_capability_registry_ko.md"


def main(
    collection_id: Annotated[str, typer.Option()],
    underlying_symbol: Annotated[str, typer.Option()],
    feed: Annotated[OptionFeed, typer.Option()],
    expiration_date: Annotated[str, typer.Option()],
    contract_type: Annotated[OptionContractType, typer.Option()],
    database: Annotated[Path, typer.Option()],
    registry: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    limit: Annotated[int, typer.Option()] = 1_000,
    max_pages: Annotated[int, typer.Option()] = 2,
) -> None:
    try:
        request = OptionChainRequest(
            collection_id=collection_id,
            underlying_symbol=underlying_symbol,
            feed=feed,
            expiration_date=dt.date.fromisoformat(expiration_date),
            contract_type=contract_type,
            limit=limit,
            max_pages=max_pages,
        )
        database_path, registry_path, report_path = _distinct_paths(
            database,
            registry,
            output_dir / REPORT_NAME,
        )
        run = AlpacaOptionChainStore(database_path).run(request.request_id)
        if run is None:
            raise AlpacaOptionChainStoreError
        projection = project_alpaca_option_chain_capability(run)
        store = DataCapabilityRegistryStore(registry_path)
        appended = store.append(
            (projection.capability,),
            (projection.entitlement,),
        )
        snapshot = store.snapshot(
            as_of=projection.capability.assessed_at,
            source_ids=(projection.capability.source_id,),
        )
        if (
            snapshot.capabilities != (projection.capability,)
            or snapshot.entitlements != (projection.entitlement,)
            or snapshot.missing_capability_source_ids
            or snapshot.missing_entitlement_source_ids
        ):
            raise DataCapabilityRegistryError
        write_private_stable_report(
            report_path,
            _report(
                projection.complete,
                projection.capability.health_state.value,
                len(run.snapshots),
                appended.capability_assessments,
                appended.entitlements,
            ),
        )
    except (
        AlpacaOptionChainCapabilityError,
        AlpacaOptionChainError,
        AlpacaOptionChainStoreError,
        DataCapabilityRegistryError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "Alpaca option chain capability state is invalid"
        ) from None
    if not projection.complete:
        raise typer.Exit(code=2)
    typer.echo("complete Alpaca option chain capability projection")


def _distinct_paths(
    database: Path,
    registry: Path,
    report: Path,
) -> tuple[Path, Path, Path]:
    paths = tuple(
        absolute_private_path(item) for item in (database, registry, report)
    )
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or (
                left.exists()
                and right.exists()
                and os.path.samestat(left.stat(), right.stat())
            ):
                raise AlpacaOptionChainStoreError
    return paths[0], paths[1], paths[2]


def _report(
    complete: bool,
    health: str,
    snapshots: int,
    capability_appended: int,
    entitlement_appended: int,
) -> str:
    return "\n".join(
        (
            "# Alpaca Option Chain Capability Registry",
            "",
            "> Exact bounded chain evidence; not market-wide OPRA coverage.",
            "",
            f"- result: {'complete' if complete else 'incomplete'}",
            f"- health: {health}",
            f"- option snapshots: {snapshots}",
            f"- capability appended: {capability_appended}",
            f"- entitlement appended: {entitlement_appended}",
            "- network access: 0",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
