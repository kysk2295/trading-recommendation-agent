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
from trading_agent.fred_alfred_capability import (
    FredCapabilityError,
    project_fred_capability,
)
from trading_agent.fred_alfred_client import (
    FredAlfredClient,
    FredTransportError,
)
from trading_agent.fred_alfred_collection import (
    FredArtifactStore,
    FredCollectionResult,
    FredStoreError,
    collect_fred_alfred,
)
from trading_agent.fred_alfred_config import (
    DEFAULT_FRED_SECRET_PATH,
    FredCredentialFileError,
    InvalidFredCredentialsError,
    create_fred_http_client,
    load_fred_credentials,
)
from trading_agent.fred_alfred_models import (
    FRED_MAX_RAW_BYTES,
    FredAlfredError,
    FredAlfredRequest,
    FredRawReceipt,
    FredRunStatus,
    FredSourceMode,
)
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "fred_alfred_ko.md"


class _FixtureFetcher:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(self, request: FredAlfredRequest) -> FredRawReceipt:
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

    def fetch(self, request: FredAlfredRequest) -> FredRawReceipt:
        credentials = load_fred_credentials(self._credential_file)
        with create_fred_http_client() as client:
            return FredAlfredClient(client, credentials).fetch(request)


def main(
    mode: Annotated[FredSourceMode, typer.Option()],
    collection_id: Annotated[str, typer.Option()],
    series_id: Annotated[str, typer.Option()],
    observation_start: Annotated[str, typer.Option()],
    observation_end: Annotated[str, typer.Option()],
    limit: Annotated[int, typer.Option()],
    state_dir: Annotated[Path, typer.Option()],
    capability_registry: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    vintage_date: Annotated[str | None, typer.Option()] = None,
    credential_file: Annotated[
        Path,
        typer.Option(),
    ] = DEFAULT_FRED_SECRET_PATH,
    fixture_response: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        request = FredAlfredRequest(
            collection_id=collection_id,
            source_mode=mode,
            series_id=series_id,
            observation_start=dt.date.fromisoformat(observation_start),
            observation_end=dt.date.fromisoformat(observation_end),
            vintage_date=(
                None
                if vintage_date is None
                else dt.date.fromisoformat(vintage_date)
            ),
            limit=limit,
        )
        result = collect_fred_alfred(
            (
                _FixtureFetcher(fixture_response)
                if fixture_response is not None
                else _OfficialFetcher(credential_file)
            ),
            FredArtifactStore(state_dir),
            request,
        )
        created = _publish(result, output_dir)
        projection = project_fred_capability(result.terminal)
        DataCapabilityRegistryStore(capability_registry).append(
            (projection.capability,),
            (projection.entitlement,),
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(
                result,
                created,
                projection.capability.health_state.value,
                fixture_response is not None,
            ),
        )
    except (
        DataCapabilityContractError,
        DataCapabilityRegistryError,
        FredAlfredError,
        FredCapabilityError,
        FredCredentialFileError,
        FredStoreError,
        FredTransportError,
        InvalidFredCredentialsError,
        InvalidPrivateImmutableFileError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("FRED/ALFRED request is invalid") from None
    if result.terminal.status is FredRunStatus.FAILED:
        raise typer.Exit(2)
    typer.echo(
        "complete FRED/ALFRED "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _publish(result: FredCollectionResult, output_dir: Path) -> bool:
    snapshot = result.terminal.snapshot
    if snapshot is None:
        return False
    return publish_private_immutable_text(
        output_dir / f"fred_alfred_snapshot_{snapshot.snapshot_id}.json",
        canonical_experiment_ledger_json(snapshot) + "\n",
    )


def _report(
    result: FredCollectionResult,
    created: bool,
    health: str,
    fixture: bool,
) -> str:
    terminal = result.terminal
    snapshot = terminal.snapshot
    missing = (
        snapshot.observation_count - snapshot.available_observation_count
        if snapshot is not None
        else 0
    )
    return "\n".join(
        (
            "# FRED / ALFRED Macro Data",
            "",
            "> Official macro evidence only; ALFRED runs bind an explicit vintage date.",
            "",
            f"- result: {terminal.status.value}",
            f"- failure: {'none' if terminal.failure is None else terminal.failure.value}",
            f"- source mode: {terminal.request.source_mode.value}",
            f"- replayed terminal: {str(result.replayed).lower()}",
            f"- vintage bound: {str(terminal.request.vintage_date is not None).lower()}",
            f"- observation count: {snapshot.observation_count if snapshot is not None else 0}",
            f"- missing observation count: {missing}",
            f"- observed completeness bps: {snapshot.observed_completeness_bps if snapshot is not None else 0}",
            f"- artifact created: {'yes' if created else 'no'}",
            f"- capability health: {health}",
            f"- network access: {int(result.fetched and not fixture)}",
            f"- provider operation: {_provider_operation(result, fixture)}",
            "- credential persisted in evidence: no",
            "- hypothesis, strategy, trial, recommendation, or order mutation: none",
            "- broker, account, order, or allocation mutation: none",
            "",
        )
    )


def _provider_operation(
    result: FredCollectionResult,
    fixture: bool,
) -> str:
    if not result.fetched:
        return "stored receipt query-only"
    return "fixture query-only" if fixture else "official FRED GET-only"


if __name__ == "__main__":
    typer.run(main)
