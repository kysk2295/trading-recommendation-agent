#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_cftc_tff_positioning_context.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated, Final

import typer

from trading_agent.cftc_tff_artifact import (
    CftcTffArtifactError,
    publish_cftc_tff_context,
)
from trading_agent.cftc_tff_client import (
    CftcTffClient,
    create_cftc_tff_http_client,
)
from trading_agent.cftc_tff_collection import (
    CftcTffCollectionResult,
    CftcTffTransportError,
    collect_cftc_tff,
)
from trading_agent.cftc_tff_models import (
    CFTC_TFF_MAX_RAW_BYTES,
    CftcTffError,
    CftcTffRawResponse,
    CftcTffRequest,
    CftcTffStatus,
)
from trading_agent.cftc_tff_store import CftcTffStore, CftcTffStoreError
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)

REPORT_NAME: Final = "cftc_tff_positioning_context_ko.md"


class _FixtureFetcher:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(self, request: CftcTffRequest) -> CftcTffRawResponse:
        payload = self._path.read_bytes()
        if len(payload) > CFTC_TFF_MAX_RAW_BYTES:
            raise CftcTffTransportError
        return CftcTffRawResponse(
            request_id=request.request_id,
            received_at=dt.datetime.now(dt.UTC),
            status_code=200,
            content_type="application/json",
            raw_payload=payload,
        )


class _OfficialFetcher:
    __slots__ = ()

    def fetch(self, request: CftcTffRequest) -> CftcTffRawResponse:
        with create_cftc_tff_http_client() as client:
            return CftcTffClient(client).fetch(request)


def main(
    collection_id: Annotated[str, typer.Option()],
    contract_market_code: Annotated[str, typer.Option()],
    through_date: Annotated[str, typer.Option()],
    database: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    fixture_response: Annotated[
        Path | None,
        typer.Option(),
    ] = None,
) -> None:
    try:
        request = CftcTffRequest(
            collection_id=collection_id,
            contract_market_code=contract_market_code,
            through_date=dt.date.fromisoformat(through_date),
        )
        store = CftcTffStore(database)
        store.preflight_write()
        result = collect_cftc_tff(
            (_FixtureFetcher(fixture_response) if fixture_response is not None else _OfficialFetcher()),
            store,
            request,
        )
        created = _publish(result, output_dir)
        receipt_count, run_count = store.counts()
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(
                result,
                created,
                receipt_count,
                run_count,
                fixture_response is not None,
            ),
        )
    except (
        CftcTffArtifactError,
        CftcTffError,
        CftcTffStoreError,
        CftcTffTransportError,
        InvalidPrivateStableReportError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter("CFTC TFF positioning context is invalid") from None
    if result.run.status is CftcTffStatus.FAILED:
        raise typer.Exit(2)
    typer.echo(f"complete CFTC TFF positioning context artifact_created={'yes' if created else 'no'}")


def _publish(
    result: CftcTffCollectionResult,
    output_dir: Path,
) -> bool:
    context = result.run.context
    if context is None:
        return False
    _, created = publish_cftc_tff_context(output_dir, context)
    return created


def _report(
    result: CftcTffCollectionResult,
    created: bool,
    receipt_count: int,
    run_count: int,
    fixture: bool,
) -> str:
    context = result.run.context
    network_access = int(not fixture and not result.replayed)
    return "\n".join(
        (
            "# CFTC TFF Positioning Context",
            "",
            "> M6 official-source, market-level weekly shadow context; "
            "not a recommendation, order, or allocation instruction.",
            "",
            f"- result: {result.run.status.value}",
            f"- failure: {_failure(result)}",
            f"- replayed terminal: {str(result.replayed).lower()}",
            f"- report count: {2 if context is not None else 0}",
            f"- latest report date: {context.latest_report_date if context is not None else 'none'}",
            f"- category count: {len(context.categories) if context is not None else 0}",
            f"- raw receipt count: {receipt_count}",
            f"- terminal run count: {run_count}",
            f"- artifact created: {'yes' if created else 'no'}",
            f"- network access: {network_access}",
            f"- provider operation: {'fixture query-only' if fixture else 'official CFTC GET-only'}",
            "- credential use: none",
            "- broker, account, order, or allocation mutation: none",
            "",
        )
    )


def _failure(result: CftcTffCollectionResult) -> str:
    failure = result.run.failure
    return "none" if failure is None else failure.value


if __name__ == "__main__":
    typer.run(main)
