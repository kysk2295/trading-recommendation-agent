#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Final

import typer
from pydantic import ValidationError

from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.issuer_announcement_capability import (
    IssuerAnnouncementCapabilityError,
    project_issuer_announcement_capability,
)
from trading_agent.issuer_announcement_client import (
    IssuerAnnouncementTransportError,
    fetch_issuer_announcement_feed,
)
from trading_agent.issuer_announcement_collection import (
    IssuerAnnouncementArtifactStore,
    IssuerAnnouncementStoreError,
    collect_issuer_announcements,
)
from trading_agent.issuer_announcement_models import (
    ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES,
    IssuerAnnouncementContractError,
    IssuerAnnouncementFeedFormat,
    IssuerAnnouncementOnboarding,
    IssuerAnnouncementRawReceipt,
    IssuerAnnouncementRequest,
    IssuerAnnouncementRunStatus,
)
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory,
)
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "issuer_announcement_collection_ko.md"


def main(
    collection_id: str | None = None,
    requested_at: str | None = None,
    onboarding: str | None = None,
    store_dir: str = "outputs/us_issuer_announcements/store",
    registry: str = "outputs/data_capability/registry.sqlite3",
    output_dir: str = "outputs/us_issuer_announcements/latest",
    fixture_response: str | None = None,
) -> None:
    try:
        request = _request(collection_id, requested_at, onboarding)
        store_path, registry_path, report_path = _paths(
            Path(store_dir),
            Path(registry),
            Path(output_dir) / REPORT_NAME,
        )
        _private_parent(registry_path.parent)
        if fixture_response is None:
            fetcher = fetch_issuer_announcement_feed
            network_access = "GET-only"
        else:
            fixture_path = Path(fixture_response)

            def fetcher(
                onboarding: IssuerAnnouncementOnboarding,
                request_id: str,
            ) -> IssuerAnnouncementRawReceipt:
                payload = read_private_bytes_query_only(
                    fixture_path,
                    max_bytes=ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES,
                )
                return IssuerAnnouncementRawReceipt.from_raw(
                    request_id=request_id,
                    received_at=request.requested_at + dt.timedelta(seconds=1),
                    status_code=200,
                    content_type=_fixture_content_type(onboarding.feed_format),
                    raw_payload=payload,
                )

            network_access = "0"
        result = collect_issuer_announcements(
            fetcher,
            IssuerAnnouncementArtifactStore(store_path),
            request,
        )
        projection = project_issuer_announcement_capability(
            request,
            result.terminal,
        )
        capability_store = DataCapabilityRegistryStore(registry_path)
        appended = capability_store.append(
            (projection.capability,),
            (projection.entitlement,),
        )
        snapshot = capability_store.snapshot(
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
                status=result.terminal.status.value,
                failure=(
                    result.terminal.failure_code.value
                    if result.terminal.failure_code is not None
                    else "none"
                ),
                replayed=result.replayed,
                raw_receipts=1 if result.terminal.receipt_id is not None else 0,
                raw_bytes=_raw_bytes(store_path, request),
                announcement_count=result.terminal.announcement_count,
                capability_appended=appended.capability_assessments,
                entitlement_appended=appended.entitlements,
                network_access=("0" if result.replayed else network_access),
            ),
        )
    except (
        DataCapabilityRegistryError,
        InvalidPrivateQueryBytesError,
        InvalidPrivateQueryFileError,
        InvalidPrivateStableReportError,
        IssuerAnnouncementCapabilityError,
        IssuerAnnouncementContractError,
        IssuerAnnouncementStoreError,
        IssuerAnnouncementTransportError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise typer.BadParameter(
            "issuer announcement collection state is invalid"
        ) from None
    if result.terminal.status is not IssuerAnnouncementRunStatus.SUCCESS:
        raise typer.Exit(code=2)
    typer.echo("complete issuer announcement collection")


def _request(
    collection_id: str | None,
    requested_at: str | None,
    onboarding: str | None,
) -> IssuerAnnouncementRequest:
    if onboarding is None:
        raise typer.BadParameter("issuer announcement onboarding is required")
    try:
        source = IssuerAnnouncementOnboarding.model_validate_json(
            read_private_text_query_only(Path(onboarding))
        )
        return IssuerAnnouncementRequest(
            collection_id=collection_id or "",
            onboarding=source,
            requested_at=_time(requested_at),
        )
    except (
        InvalidPrivateQueryFileError,
        IssuerAnnouncementContractError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise typer.BadParameter(
            "issuer announcement onboarding is invalid"
        ) from None


def _time(value: str | None) -> dt.datetime:
    if value is None:
        raise ValueError
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _paths(store: Path, registry: Path, report: Path) -> tuple[Path, Path, Path]:
    try:
        paths = tuple(absolute_private_path(item) for item in (store, registry, report))
        if (
            paths[0] == paths[1]
            or paths[0] == paths[2]
            or paths[1] == paths[2]
            or paths[0] in paths[1].parents
            or paths[0] in paths[2].parents
        ):
            raise ValueError
        return paths[0], paths[1], paths[2]
    except (OSError, RuntimeError, TypeError, ValueError):
        raise typer.BadParameter(
            "store, registry, and report paths must be distinct"
        ) from None


def _private_parent(path: Path) -> None:
    descriptor = open_private_parent(path, create=True)
    try:
        require_private_directory(descriptor)
    finally:
        os.close(descriptor)


def _fixture_content_type(feed_format: IssuerAnnouncementFeedFormat) -> str:
    return (
        "application/rss+xml"
        if feed_format is IssuerAnnouncementFeedFormat.RSS2
        else "application/atom+xml"
    )


def _raw_bytes(store_path: Path, request: IssuerAnnouncementRequest) -> int:
    receipt = IssuerAnnouncementArtifactStore(store_path).receipt(request.request_id)
    return len(receipt.raw_payload) if receipt is not None else 0


def _report(
    *,
    status: str,
    failure: str,
    replayed: bool,
    raw_receipts: int,
    raw_bytes: int,
    announcement_count: int,
    capability_appended: int,
    entitlement_appended: int,
    network_access: str,
) -> str:
    return "\n".join(
        (
            "# Issuer-direct Announcement Collection",
            "",
            "> Onboarded issuer metadata only; not a recommendation or market-wide coverage.",
            "",
            f"- result: {status}",
            f"- failure: {failure}",
            f"- replayed: {'yes' if replayed else 'no'}",
            f"- raw receipts: {raw_receipts}",
            f"- raw response bytes: {raw_bytes}",
            f"- announcement metadata: {announcement_count}",
            f"- capability appended: {capability_appended}",
            f"- entitlement appended: {entitlement_appended}",
            f"- network access: {network_access}",
            "- provider operation: GET-only",
            "- Paper recommendation authority: false",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
