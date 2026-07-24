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

from trading_agent.arxiv_research_capability import (
    ArxivCapabilityError,
    project_arxiv_capability,
)
from trading_agent.arxiv_research_client import (
    ArxivResearchClient,
    ArxivTransportError,
    create_arxiv_http_client,
)
from trading_agent.arxiv_research_collection import (
    ArxivArtifactStore,
    ArxivCollectionResult,
    ArxivStoreError,
    collect_arxiv_research,
)
from trading_agent.arxiv_research_models import (
    ARXIV_MAX_RAW_BYTES,
    ArxivRawReceipt,
    ArxivResearchError,
    ArxivResearchRequest,
    ArxivRunStatus,
)
from trading_agent.data_capability_models import DataCapabilityContractError
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "arxiv_research_ko.md"


class _FixtureFetcher:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(self, request: ArxivResearchRequest) -> ArxivRawReceipt:
        payload = self._path.read_bytes()
        if len(payload) > ARXIV_MAX_RAW_BYTES:
            raise ArxivTransportError
        return ArxivRawReceipt.from_raw(
            request_id=request.request_id,
            received_at=dt.datetime.now(dt.UTC),
            status_code=200,
            content_type="application/atom+xml",
            raw_payload=payload,
        )


class _OfficialFetcher:
    __slots__ = ()

    def fetch(self, request: ArxivResearchRequest) -> ArxivRawReceipt:
        with create_arxiv_http_client() as client:
            return ArxivResearchClient(client).fetch(request)


def main(
    collection_id: Annotated[str, typer.Option()],
    category: Annotated[str, typer.Option()],
    term: Annotated[list[str], typer.Option()],
    max_results: Annotated[int, typer.Option()],
    state_dir: Annotated[Path, typer.Option()],
    capability_registry: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    fixture_response: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        request = ArxivResearchRequest(
            collection_id=collection_id,
            category=category,
            terms=tuple(sorted(term)),
            max_results=max_results,
        )
        result = collect_arxiv_research(
            (
                _FixtureFetcher(fixture_response)
                if fixture_response is not None
                else _OfficialFetcher()
            ),
            ArxivArtifactStore(state_dir),
            request,
        )
        created = _publish(result, output_dir)
        projection = project_arxiv_capability(result.terminal)
        DataCapabilityRegistryStore(capability_registry).append(
            (projection.capability,),
            (projection.entitlement,),
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(result, created, projection.capability.health_state.value, fixture_response is not None),
        )
    except (
        ArxivCapabilityError,
        ArxivResearchError,
        ArxivStoreError,
        ArxivTransportError,
        DataCapabilityContractError,
        DataCapabilityRegistryError,
        InvalidPrivateImmutableFileError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("arXiv research request is invalid") from None
    if result.terminal.status is ArxivRunStatus.FAILED:
        raise typer.Exit(2)
    typer.echo(
        "complete arXiv research "
        f"artifact_created={'yes' if created else 'no'}"
    )


def _publish(result: ArxivCollectionResult, output_dir: Path) -> bool:
    snapshot = result.terminal.snapshot
    if snapshot is None:
        return False
    return publish_private_immutable_text(
        output_dir / f"arxiv_research_snapshot_{snapshot.snapshot_id}.json",
        canonical_experiment_ledger_json(snapshot) + "\n",
    )


def _report(
    result: ArxivCollectionResult,
    created: bool,
    health: str,
    fixture: bool,
) -> str:
    terminal = result.terminal
    snapshot = terminal.snapshot
    operation = (
        "stored receipt query-only"
        if not result.fetched
        else ("fixture query-only" if fixture else "official arXiv GET-only")
    )
    return "\n".join(
        (
            "# arXiv Research Metadata",
            "",
            "> Academic metadata discovery only; every claim still requires explicit reviewed lineage.",
            "",
            f"- result: {terminal.status.value}",
            f"- failure: {'none' if terminal.failure is None else terminal.failure.value}",
            f"- replayed terminal: {str(result.replayed).lower()}",
            f"- paper count: {len(snapshot.papers) if snapshot is not None else 0}",
            f"- total result count: {snapshot.total_results if snapshot is not None else 0}",
            f"- artifact created: {'yes' if created else 'no'}",
            f"- capability health: {health}",
            f"- network access: {int(result.fetched and not fixture)}",
            f"- provider operation: {operation}",
            "- credential use: none",
            "- automatic paper claim or hypothesis inference: none",
            "- hypothesis, strategy, trial, recommendation, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
