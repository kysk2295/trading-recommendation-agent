#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

import datetime as dt
import os
import stat
from pathlib import Path
from typing import Final

import typer

from trading_agent.alpaca_news_capability_projection import (
    AlpacaNewsCapabilityProjection,
    AlpacaNewsCapabilityProjectionError,
    project_alpaca_news_capability,
)
from trading_agent.alpaca_news_models import AlpacaNewsRequest
from trading_agent.alpaca_news_store import AlpacaNewsStore, AlpacaNewsStoreError
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

REPORT_NAME: Final = "alpaca_news_capability_registry_ko.md"


def main(
    collection_id: str | None = None,
    symbols: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    database: str = "outputs/us_news/alpaca_news.sqlite3",
    registry: str = "outputs/data_capability/registry.sqlite3",
    output_dir: str = "outputs/data_capability/alpaca-news-latest",
    limit: int = 50,
    max_pages: int = 8,
) -> None:
    try:
        request = _request(collection_id, symbols, start_at, end_at, limit, max_pages)
        database_path, registry_path, report_path = _private_distinct_paths(
            Path(database),
            Path(registry),
            Path(output_dir) / REPORT_NAME,
        )
        _preflight_report(report_path)
        run = AlpacaNewsStore(database_path).run(request.request_id)
        if run is None:
            raise AlpacaNewsStoreError
        projection = project_alpaca_news_capability(run)
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
        AlpacaNewsCapabilityProjectionError,
        AlpacaNewsStoreError,
        DataCapabilityRegistryError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("Alpaca news capability projection state is invalid") from None
    if not projection.complete:
        raise typer.Exit(code=2)
    typer.echo("complete Alpaca news capability projection")


def _request(
    collection_id: str | None,
    symbols: str | None,
    start_at: str | None,
    end_at: str | None,
    limit: int,
    max_pages: int,
) -> AlpacaNewsRequest:
    values = tuple(item.strip().upper() for item in (symbols or "").split(",") if item.strip())
    try:
        return AlpacaNewsRequest(
            collection_id=collection_id or "",
            symbols=values,
            start_at=_time(start_at),
            end_at=_time(end_at),
            limit=limit,
            max_pages=max_pages,
        )
    except (TypeError, ValueError):
        raise typer.BadParameter("bounded Alpaca news request is invalid") from None


def _time(value: str | None) -> dt.datetime:
    if value is None:
        raise ValueError
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _private_distinct_paths(
    database: Path,
    registry: Path,
    report: Path,
) -> tuple[Path, Path, Path]:
    try:
        paths = tuple(absolute_private_path(item) for item in (database, registry, report))
        for index, left in enumerate(paths):
            for right in paths[index + 1 :]:
                if left == right or (
                    left.exists() and right.exists() and os.path.samestat(left.stat(), right.stat())
                ):
                    raise ValueError
        return paths[0], paths[1], paths[2]
    except (OSError, RuntimeError, ValueError):
        raise typer.BadParameter("database, registry, and report paths must be distinct") from None


def _preflight_report(path: Path) -> None:
    parent = open_private_parent(path.parent, create=True)
    try:
        require_private_directory(parent)
        require_open_directory_path(path.parent, parent)
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
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
        os.close(parent)


def _report(
    projection: AlpacaNewsCapabilityProjection,
    *,
    capability_appended: int,
    entitlement_appended: int,
) -> str:
    return "\n".join(
        (
            "# Alpaca News Capability Registry",
            "",
            "> Local ledger projection only; bounded symbols are not market-wide coverage.",
            "",
            f"- result: {'complete' if projection.complete else 'incomplete'}",
            f"- health: {projection.capability.health_state.value}",
            f"- raw response pages: {projection.page_count}",
            f"- article metadata observations: {projection.article_count}",
            f"- capability appended: {capability_appended}",
            f"- entitlement appended: {entitlement_appended}",
            "- capability resolved: 1/1",
            "- entitlement resolved: 1/1",
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
