#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_bls_public_data_collect.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.bls_public_artifact import (
    BlsPublicArtifactError,
    publish_bls_macro_snapshot,
)
from trading_agent.bls_public_capability import (
    BlsPublicCapabilityError,
    project_bls_public_capability,
)
from trading_agent.bls_public_client import (
    BlsPublicClient,
    create_bls_public_http_client,
)
from trading_agent.bls_public_collection import (
    BlsPublicCollectionResult,
    BlsPublicTransportError,
    collect_bls_public_data,
)
from trading_agent.bls_public_models import (
    BLS_PUBLIC_MAX_RAW_BYTES,
    BlsPublicError,
    BlsPublicRawResponse,
    BlsPublicRequest,
    BlsPublicStatus,
)
from trading_agent.bls_public_store import (
    BlsPublicStore,
    BlsPublicStoreError,
)
from trading_agent.data_capability_models import DataCapabilityContractError
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "bls_public_data_ko.md"


class _FixtureFetcher:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(self, request: BlsPublicRequest) -> BlsPublicRawResponse:
        payload = self._path.read_bytes()
        if len(payload) > BLS_PUBLIC_MAX_RAW_BYTES:
            raise BlsPublicTransportError
        return BlsPublicRawResponse(
            request_id=request.request_id,
            received_at=dt.datetime.now(dt.UTC),
            status_code=200,
            content_type="application/json",
            raw_payload=payload,
        )


class _OfficialFetcher:
    __slots__ = ()

    def fetch(self, request: BlsPublicRequest) -> BlsPublicRawResponse:
        with create_bls_public_http_client() as client:
            return BlsPublicClient(client).fetch(request)


def main(
    collection_id: Annotated[str, typer.Option()],
    series_id: Annotated[list[str], typer.Option()],
    start_year: Annotated[int, typer.Option()],
    end_year: Annotated[int, typer.Option()],
    database: Annotated[Path, typer.Option()],
    capability_registry: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    fixture_response: Annotated[
        Path | None,
        typer.Option(),
    ] = None,
) -> None:
    try:
        request = BlsPublicRequest(
            collection_id=collection_id,
            series_ids=tuple(sorted(series_id)),
            start_year=start_year,
            end_year=end_year,
        )
        store = BlsPublicStore(database)
        store.preflight_write()
        result = collect_bls_public_data(
            (
                _FixtureFetcher(fixture_response)
                if fixture_response is not None
                else _OfficialFetcher()
            ),
            store,
            request,
        )
        created = _publish(result, output_dir)
        projection = project_bls_public_capability(request, result.run)
        DataCapabilityRegistryStore(capability_registry).append(
            (projection.capability,),
            (projection.entitlement,),
        )
        receipt_count, run_count = store.counts()
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(
                result,
                created,
                receipt_count,
                run_count,
                projection.capability.health_state.value,
                fixture_response is not None,
            ),
        )
    except (
        BlsPublicArtifactError,
        BlsPublicCapabilityError,
        BlsPublicError,
        BlsPublicStoreError,
        BlsPublicTransportError,
        DataCapabilityContractError,
        DataCapabilityRegistryError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("BLS public data request is invalid") from None
    if result.run.status is BlsPublicStatus.FAILED:
        raise typer.Exit(2)
    typer.echo(
        "complete BLS public data "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _publish(
    result: BlsPublicCollectionResult,
    output_dir: Path,
) -> bool:
    snapshot = result.run.snapshot
    if snapshot is None:
        return False
    _, created = publish_bls_macro_snapshot(output_dir, snapshot)
    return created


def _report(
    result: BlsPublicCollectionResult,
    created: bool,
    receipt_count: int,
    run_count: int,
    capability_health: str,
    fixture: bool,
) -> str:
    snapshot = result.run.snapshot
    failure = result.run.failure
    network_access = int(result.fetched and not fixture)
    return "\n".join(
        (
            "# BLS Public Data",
            "",
            "> Official public macro evidence; not a recommendation, order, "
            "or allocation instruction.",
            "",
            f"- result: {result.run.status.value}",
            f"- failure: {'none' if failure is None else failure.value}",
            f"- replayed terminal: {str(result.replayed).lower()}",
            f"- series count: {len(snapshot.series) if snapshot is not None else 0}",
            f"- observation count: {snapshot.observation_count if snapshot is not None else 0}",
            f"- available observation count: {snapshot.available_observation_count if snapshot is not None else 0}",
            f"- missing observation count: {snapshot.missing_observation_count if snapshot is not None else 0}",
            f"- observed completeness bps: {snapshot.observed_completeness_bps if snapshot is not None else 0}",
            f"- raw receipt count: {receipt_count}",
            f"- terminal run count: {run_count}",
            f"- artifact created: {'yes' if created else 'no'}",
            f"- capability health: {capability_health}",
            f"- network access: {network_access}",
            f"- provider operation: {_provider_operation(result, fixture)}",
            "- unregistered daily request limit: 25",
            "- credential use: none",
            "- broker, account, order, or allocation mutation: none",
            "",
        )
    )


def _provider_operation(
    result: BlsPublicCollectionResult,
    fixture: bool,
) -> str:
    if not result.fetched:
        return "stored receipt query-only"
    return "fixture query-only" if fixture else "official BLS POST-only"


if __name__ == "__main__":
    typer.run(main)
