#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.data_capability_models import DataCapabilityContractError
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_alfred_client import FredTransportError
from trading_agent.fred_alfred_config import (
    DEFAULT_FRED_SECRET_PATH,
    FredCredentialFileError,
    InvalidFredCredentialsError,
    create_fred_http_client,
    load_fred_credentials,
)
from trading_agent.fred_alfred_models import (
    FRED_MAX_RAW_BYTES,
    FredRawReceipt,
    FredRunStatus,
)
from trading_agent.fred_vintage_dates_capability import (
    FredVintageDatesCapabilityError,
    project_fred_vintage_dates_capability,
)
from trading_agent.fred_vintage_dates_client import FredVintageDatesClient
from trading_agent.fred_vintage_dates_collection import (
    FredVintageDatesArtifactStore,
    FredVintageDatesCollectionResult,
    FredVintageDatesStoreError,
    collect_fred_vintage_dates,
)
from trading_agent.fred_vintage_dates_models import (
    FredVintageDatesError,
    FredVintageDatesRequest,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "fred_vintage_dates_ko.md"


class _FixtureFetcher:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(self, request: FredVintageDatesRequest) -> FredRawReceipt:
        payload = self._path.read_bytes()
        if len(payload) > FRED_MAX_RAW_BYTES:
            raise FredTransportError
        return FredRawReceipt.from_raw(
            request_id=request.request_id,
            received_at=dt.datetime.now(dt.UTC),
            status_code=200,
            content_type="application/json",
            raw_payload=payload,
        )


class _OfficialFetcher:
    __slots__ = ("_credential_file",)

    def __init__(self, credential_file: Path) -> None:
        self._credential_file = credential_file

    def fetch(self, request: FredVintageDatesRequest) -> FredRawReceipt:
        credentials = load_fred_credentials(self._credential_file)
        with create_fred_http_client() as client:
            return FredVintageDatesClient(client, credentials).fetch(request)


def main(
    collection_id: Annotated[str, typer.Option()],
    series_id: Annotated[str, typer.Option()],
    realtime_start: Annotated[str, typer.Option()],
    realtime_end: Annotated[str, typer.Option()],
    limit: Annotated[int, typer.Option()],
    state_dir: Annotated[Path, typer.Option()],
    capability_registry: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    credential_file: Annotated[
        Path,
        typer.Option(),
    ] = DEFAULT_FRED_SECRET_PATH,
    fixture_response: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        request = FredVintageDatesRequest(
            collection_id=collection_id,
            series_id=series_id,
            realtime_start=dt.date.fromisoformat(realtime_start),
            realtime_end=dt.date.fromisoformat(realtime_end),
            limit=limit,
        )
        result = collect_fred_vintage_dates(
            (
                _FixtureFetcher(fixture_response)
                if fixture_response is not None
                else _OfficialFetcher(credential_file)
            ),
            FredVintageDatesArtifactStore(state_dir),
            request,
        )
        created = _publish(result, output_dir)
        projection = project_fred_vintage_dates_capability(result.terminal)
        DataCapabilityRegistryStore(capability_registry).append(
            (projection.capability,),
            (projection.entitlement,),
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(result, created, fixture_response is not None),
        )
    except (
        DataCapabilityContractError,
        DataCapabilityRegistryError,
        FredCredentialFileError,
        FredTransportError,
        FredVintageDatesCapabilityError,
        FredVintageDatesError,
        FredVintageDatesStoreError,
        InvalidFredCredentialsError,
        InvalidPrivateImmutableFileError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("FRED vintage dates request is invalid") from None
    if result.terminal.status is not FredRunStatus.SUCCESS:
        raise typer.Exit(2)
    typer.echo(
        "complete FRED vintage dates "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _publish(
    result: FredVintageDatesCollectionResult,
    output_dir: Path,
) -> bool:
    snapshot = result.terminal.snapshot
    if snapshot is None:
        return False
    return publish_private_immutable_text(
        output_dir / f"fred_vintage_dates_snapshot_{snapshot.snapshot_id}.json",
        canonical_experiment_ledger_json(snapshot) + "\n",
    )


def _report(
    result: FredVintageDatesCollectionResult,
    created: bool,
    fixture: bool,
) -> str:
    terminal = result.terminal
    snapshot = terminal.snapshot
    return "\n".join(
        (
            "# FRED Series Vintage Dates",
            "",
            "> Official release-or-revision dates for one exact FRED series.",
            "",
            f"- result: {terminal.status.value}",
            f"- failure: {'none' if terminal.failure is None else terminal.failure.value}",
            f"- replayed terminal: {str(result.replayed).lower()}",
            f"- vintage date count: {len(snapshot.vintage_dates) if snapshot is not None else 0}",
            f"- artifact created: {'yes' if created else 'no'}",
            f"- network access: {int(result.fetched and not fixture)}",
            "- source meaning: release or revision changed series data",
            "- future no-data release dates included: no",
            "- credential persisted in evidence: no",
            "- broker, account, order, lifecycle, or allocation mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
