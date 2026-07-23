#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "typer>=0.15"]
# ///
#
# ─── How to run ───
# uv run run_alpaca_sip_spot_capture.py --help
# ──────────────────

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Annotated, Final

import httpx2
import typer
from pydantic import ValidationError

from trading_agent.alpaca_http import (
    ALPACA_DATA_URL,
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    load_alpaca_credentials,
)
from trading_agent.alpaca_models import BARS_ADAPTER
from trading_agent.alpaca_sip_runtime_evidence import (
    AlpacaSipRuntimeEvidenceProjector,
)
from trading_agent.alpaca_sip_runtime_evidence_store import (
    AlpacaSipRuntimeEvidenceStore,
)
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipHttpStatusError,
    AlpacaSipMinutePage,
    AlpacaSipMinutePageRequest,
    AlpacaSipRawPage,
    AlpacaSipRuntimeError,
)
from trading_agent.alpaca_sip_spot_capture import (
    AlpacaSipSpotCaptureError,
    AlpacaSipSpotCaptureResult,
    materialize_alpaca_sip_spot_capture,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_market_data_runtime_store import MarketDataRuntimeStore

REPORT_NAME: Final = "alpaca_sip_spot_capture_ko.md"


def main(
    instrument_id: Annotated[str, typer.Option()],
    symbol: Annotated[str, typer.Option()],
    as_of: Annotated[str, typer.Option("--as-of")],
    state_dir: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    fixture_page: Annotated[Path | None, typer.Option()] = None,
    credentials_path: Annotated[
        Path,
        typer.Option(),
    ] = DEFAULT_ALPACA_SECRET_PATH,
) -> None:
    try:
        observed_at = dt.datetime.fromisoformat(as_of)
        request = _request(symbol, observed_at)
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        evidence = AlpacaSipRuntimeEvidenceStore(state_dir / "evidence.sqlite3")
        page_set = evidence.load_page_set(request)
        if page_set is not None:
            network_access = "0"
        elif fixture_page is not None:
            page_set = _fixture_page(request, fixture_page, observed_at)
            network_access = "0"
        else:
            _require_current_live_as_of(observed_at)
            credentials = load_alpaca_credentials(credentials_path)
            with httpx2.Client(
                base_url=ALPACA_DATA_URL,
                follow_redirects=False,
                timeout=httpx2.Timeout(
                    connect=5.0,
                    read=30.0,
                    write=10.0,
                    pool=10.0,
                ),
            ) as client:
                page_set = AlpacaSipMinutePageClient(
                    client,
                    credentials,
                    clock=lambda: dt.datetime.now(dt.UTC),
                ).fetch_page(request)
            network_access = "GET-only"
        result = materialize_alpaca_sip_spot_capture(
            page_set,
            instrument_id,
            AlpacaSipRuntimeEvidenceProjector(
                evidence,
                state_dir / "canonical",
            ),
            MarketDataRuntimeStore(state_dir / "runtime.sqlite3"),
        )
        write_private_stable_report(
            output_dir / REPORT_NAME,
            _report(result, network_access),
        )
    except AlpacaSipHttpStatusError as error:
        try:
            write_private_stable_report(
                output_dir / REPORT_NAME,
                _blocked_report(error.status_code),
            )
        except (InvalidPrivateStableReportError, OSError, TypeError, ValueError):
            raise typer.BadParameter("bounded Alpaca SIP spot capture is invalid") from None
        raise typer.Exit(code=2) from None
    except (
        AlpacaSecretFileError,
        AlpacaSipRuntimeError,
        AlpacaSipSpotCaptureError,
        InvalidPrivateStableReportError,
        MissingAlpacaCredentialsError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise typer.BadParameter("bounded Alpaca SIP spot capture is invalid") from None
    typer.echo(f"complete bounded Alpaca SIP spot capture new_receipts={result.inserted_receipt_count}")


def _request(
    symbol: str,
    observed_at: dt.datetime,
) -> AlpacaSipMinutePageRequest:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise AlpacaSipSpotCaptureError
    local = observed_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(local.date())
    completed_boundary = local.replace(second=0, microsecond=0)
    if bounds is None or not bounds[0] < observed_at < bounds[1] or not bounds[0] < completed_boundary <= bounds[1]:
        raise AlpacaSipSpotCaptureError
    return AlpacaSipMinutePageRequest(
        local.date(),
        symbol,
        bounds[0],
        completed_boundary - dt.timedelta(microseconds=1),
    )


def _fixture_page(
    request: AlpacaSipMinutePageRequest,
    path: Path,
    observed_at: dt.datetime,
) -> AlpacaSipMinutePage:
    payload_bytes = path.read_bytes()
    payload = BARS_ADAPTER.validate_json(payload_bytes)
    if payload.next_page_token is not None:
        raise AlpacaSipSpotCaptureError
    return AlpacaSipMinutePage(
        request,
        (
            AlpacaSipRawPage(
                0,
                None,
                observed_at,
                payload_bytes,
                payload,
            ),
        ),
    )


def _require_current_live_as_of(observed_at: dt.datetime) -> None:
    now = dt.datetime.now(dt.UTC)
    age = now - observed_at.astimezone(dt.UTC)
    if not dt.timedelta(0) <= age <= dt.timedelta(minutes=2):
        raise AlpacaSipSpotCaptureError


def _report(
    result: AlpacaSipSpotCaptureResult,
    network_access: str,
) -> str:
    return "\n".join(
        (
            "# Alpaca SIP Spot Capture",
            "",
            "> M6 GET-only completed-minute spot evidence; not a recommendation or order.",
            "",
            "- result: ready",
            f"- dataset id: {result.identity.dataset_id}",
            f"- latest completed bar: {result.latest_completed_at.isoformat()}",
            f"- runtime receipts: {result.receipt_count}",
            f"- new runtime receipts: {result.inserted_receipt_count}",
            f"- network access: {network_access}",
            "- provider operation: GET-only or exact local replay",
            "- broker, account, or order mutation: none",
            "",
        )
    )


def _blocked_report(status_code: int) -> str:
    reason = {
        401: "sip_authentication_rejected",
        403: "sip_access_forbidden",
        429: "provider_rate_limited",
    }.get(status_code, "provider_unavailable" if status_code >= 500 else "provider_http_rejected")
    return "\n".join(
        (
            "# Alpaca SIP Spot Capture",
            "",
            "> M6 GET-only source admission evidence; not a recommendation or order.",
            "",
            "- result: blocked_source",
            f"- reason: {reason}",
            f"- provider status: {status_code}",
            "- network access: GET-only",
            "- broker, account, or order mutation: none",
            "",
        )
    )


if __name__ == "__main__":
    typer.run(main)
