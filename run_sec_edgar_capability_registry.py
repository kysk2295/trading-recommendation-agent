#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["ijson==3.5.0", "pydantic>=2.11", "typer>=0.15"]
# ///
# How to run:
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run --script run_sec_edgar_capability_registry.py --help
# 3. Or: chmod +x run_sec_edgar_capability_registry.py && ./run_sec_edgar_capability_registry.py --help

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Final

import typer

from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.sec_edgar_capability_projection import (
    SecCapabilityProjection,
    SecCapabilityProjectionError,
    project_sec_edgar_capability,
)
from trading_agent.sec_edgar_models import normalize_sec_cik
from trading_agent.sec_edgar_store import InvalidSecEdgarStoreError, SecEdgarStore

REPORT_NAME: Final = "sec_edgar_capability_registry_ko.md"
_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def main(
    parent_collection_id: str | None = None,
    cik: str | None = None,
    database: str = "outputs/us_regulatory/sec_edgar.sqlite3",
    registry: str = "outputs/data_capability/registry.sqlite3",
    output_dir: str = "outputs/data_capability/sec-edgar-latest",
) -> None:
    if parent_collection_id is None or _SAFE_ID.fullmatch(parent_collection_id) is None:
        raise typer.BadParameter("valid parent collection ID is required")
    try:
        normalized_cik = normalize_sec_cik(cik or "")
    except ValueError:
        raise typer.BadParameter("CIK must contain exactly 10 digits") from None
    try:
        database_path, registry_path, report_path = _private_distinct_paths(
            Path(database),
            Path(registry),
            Path(output_dir) / REPORT_NAME,
        )
        _preflight_report(report_path)
        evidence = SecEdgarStore(database_path).capability_evidence(
            parent_collection_id,
            normalized_cik,
        )
        if evidence is None:
            raise InvalidSecEdgarStoreError
        projection = project_sec_edgar_capability(evidence)
        store = DataCapabilityRegistryStore(registry_path)
        appended = store.append((projection.capability,), (projection.entitlement,))
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
                projection,
                capability_appended=appended.capability_assessments,
                entitlement_appended=appended.entitlements,
            ),
        )
    except (
        DataCapabilityRegistryError,
        InvalidPrivateStableReportError,
        InvalidSecEdgarStoreError,
        OSError,
        RuntimeError,
        SecCapabilityProjectionError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("SEC EDGAR capability projection state is invalid") from None
    if not projection.complete:
        raise typer.Exit(code=2)
    typer.echo("complete SEC EDGAR capability projection")


def _report(
    projection: SecCapabilityProjection,
    *,
    capability_appended: int,
    entitlement_appended: int,
) -> str:
    return "\n".join(
        (
            "# SEC EDGAR Capability Registry",
            "",
            "> Local ledger projection only; bounded issuer evidence is not market-wide coverage.",
            "",
            f"- result: {'complete' if projection.complete else 'incomplete'}",
            f"- health: {projection.capability.health_state.value}",
            f"- successful slices: {projection.successful_slice_count}/{projection.declared_slice_count}",
            f"- failed slices: {projection.failed_slice_count}",
            f"- missing slices: {projection.missing_slice_count}",
            f"- filing metadata observations: {projection.filing_count}",
            f"- capability appended: {capability_appended}",
            f"- entitlement appended: {entitlement_appended}",
            "- capability resolved: 1/1",
            "- entitlement resolved: 1/1",
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )


def _private_distinct_paths(
    database: Path,
    registry: Path,
    report: Path,
) -> tuple[Path, Path, Path]:
    database_path = absolute_private_path(database)
    registry_path = absolute_private_path(registry)
    report_path = absolute_private_path(report)
    paths = (database_path, registry_path, report_path)
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or (
                left.exists()
                and right.exists()
                and os.path.samestat(left.stat(), right.stat())
            ):
                raise ValueError
    return database_path, registry_path, report_path


def _preflight_report(path: Path) -> None:
    parent_descriptor = open_private_parent(path.parent, create=True)
    try:
        require_private_directory(parent_descriptor)
        require_open_directory_path(path.parent, parent_descriptor)
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            return
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise ValueError
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


if __name__ == "__main__":
    typer.run(main)
