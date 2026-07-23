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

from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.treasury_yield_artifact import (
    publish_treasury_yield_context,
)
from trading_agent.treasury_yield_client import (
    TreasuryYieldClient,
    create_treasury_yield_http_client,
)
from trading_agent.treasury_yield_collection import (
    TreasuryYieldCollectionResult,
    TreasuryYieldTransportError,
    collect_treasury_yield,
)
from trading_agent.treasury_yield_models import (
    TREASURY_YIELD_MAX_RAW_BYTES,
    TreasuryYieldError,
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
    TreasuryYieldStatus,
)
from trading_agent.treasury_yield_store import (
    TreasuryYieldStore,
    TreasuryYieldStoreError,
)

REPORT_NAME: Final = "treasury_yield_curve_context_ko.md"


class _FixtureFetcher:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(
        self,
        request: TreasuryYieldRequest,
    ) -> TreasuryYieldRawResponse:
        payload = self._path.read_bytes()
        if len(payload) > TREASURY_YIELD_MAX_RAW_BYTES:
            raise TreasuryYieldTransportError
        return TreasuryYieldRawResponse(
            request_id=request.request_id,
            received_at=dt.datetime.now(dt.UTC),
            status_code=200,
            content_type="application/xml",
            raw_payload=payload,
        )


class _OfficialFetcher:
    __slots__ = ()

    def fetch(
        self,
        request: TreasuryYieldRequest,
    ) -> TreasuryYieldRawResponse:
        with create_treasury_yield_http_client() as client:
            return TreasuryYieldClient(client).fetch(request)


def main(
    collection_id: Annotated[str, typer.Option()],
    through_date: Annotated[str, typer.Option()],
    database: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    fixture_response: Annotated[
        Path | None,
        typer.Option(),
    ] = None,
) -> None:
    try:
        request = TreasuryYieldRequest(
            collection_id=collection_id,
            through_date=dt.date.fromisoformat(through_date),
        )
        store = TreasuryYieldStore(database)
        store.preflight_write()
        result = collect_treasury_yield(
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
        InvalidPrivateStableReportError,
        OSError,
        TreasuryYieldError,
        TreasuryYieldStoreError,
        TreasuryYieldTransportError,
        TypeError,
        ValueError,
    ):
        raise typer.BadParameter(
            "Treasury yield curve context is invalid",
        ) from None
    if result.run.status is TreasuryYieldStatus.FAILED:
        raise typer.Exit(2)
    typer.echo(
        f"complete Treasury yield curve context artifact_created={'yes' if created else 'no'}",
    )


def _publish(
    result: TreasuryYieldCollectionResult,
    output_dir: Path,
) -> bool:
    context = result.run.context
    if context is None:
        return False
    _, created = publish_treasury_yield_context(output_dir, context)
    return created


def _report(
    result: TreasuryYieldCollectionResult,
    created: bool,
    receipt_count: int,
    run_count: int,
    fixture: bool,
) -> str:
    context = result.run.context
    network_access = int(not fixture and not result.replayed)
    return "\n".join(
        (
            "# Treasury Yield Curve Context",
            "",
            "> M6 official daily macro context; not an intraday quote, "
            "recommendation, order, or allocation instruction.",
            "",
            f"- result: {result.run.status.value}",
            f"- failure: {_failure(result)}",
            f"- replayed terminal: {str(result.replayed).lower()}",
            f"- curve count: {2 if context is not None else 0}",
            f"- latest curve date: {context.latest_date if context is not None else 'none'}",
            f"- maturity count: {len(context.points) if context is not None else 0}",
            f"- raw receipt count: {receipt_count}",
            f"- terminal run count: {run_count}",
            f"- artifact created: {'yes' if created else 'no'}",
            f"- network access: {network_access}",
            f"- provider operation: {'fixture query-only' if fixture else 'official Treasury GET-only'}",
            "- credential use: none",
            "- broker, account, order, or allocation mutation: none",
            "",
        ),
    )


def _failure(result: TreasuryYieldCollectionResult) -> str:
    failure = result.run.failure
    return "none" if failure is None else failure.value


if __name__ == "__main__":
    typer.run(main)
