#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import (
    ALPACA_DATA_URL,
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaCredentials,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    load_alpaca_credentials,
)
from trading_agent.alpaca_sip_historical_profile import (
    AlpacaSipHistoricalProfileCollector,
    AlpacaSipHistoricalProfileError,
)
from trading_agent.alpaca_sip_runtime_evidence import AlpacaSipRuntimeEvidenceProjector
from trading_agent.alpaca_sip_runtime_evidence_store import AlpacaSipRuntimeEvidenceStore
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.private_report import write_private_report
from trading_agent.us_intraday_volume_profile_artifact import (
    IntradayVolumeProfileArtifactError,
    IntradayVolumeProfileArtifactStore,
)

REPORT_NAME = "alpaca_sip_historical_profile_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alpaca SIP 과거 정규장 20일로 causal volume profile을 GET-only 생성")
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--target-session-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--through-minute", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    return parser.parse_args(argv)


def create_data_client() -> httpx2.Client:
    return httpx2.Client(
        base_url=ALPACA_DATA_URL,
        follow_redirects=False,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        artifacts = IntradayVolumeProfileArtifactStore(args.state_dir)
        credentials = load_alpaca_credentials(args.secret_path)
        evidence = AlpacaSipRuntimeEvidenceStore(args.state_dir / "evidence.sqlite3")
        before = evidence.page_count()
        with create_data_client() as client:
            profile = _collector(client, credentials, evidence, args.state_dir).collect(
                args.instrument_id,
                args.symbol,
                args.target_session_date,
                through_minute=args.through_minute,
            )
        artifact = artifacts.append(profile)
        new_pages = evidence.page_count() - before
    except (
        AlpacaSecretFileError,
        AlpacaSipHistoricalProfileError,
        IntradayVolumeProfileArtifactError,
        MissingAlpacaCredentialsError,
        OSError,
    ):
        _report(args.output_dir, ("result: blocked", "account/order mutation: 0"))
        return 1
    _report(
        args.output_dir,
        (
            "result: ready",
            f"target session: {profile.target_session_date.isoformat()}",
            f"through minute: {profile.through_minute}",
            f"source session: {len(profile.source_session_dates)}",
            f"new raw page: {new_pages}",
            f"artifact: {artifact.name}",
            "account/order mutation: 0",
        ),
    )
    return 0


def _collector(
    client: httpx2.Client,
    credentials: AlpacaCredentials,
    evidence: AlpacaSipRuntimeEvidenceStore,
    state_dir: Path,
) -> AlpacaSipHistoricalProfileCollector:
    page_client = AlpacaSipMinutePageClient(
        client,
        credentials,
        clock=lambda: dt.datetime.now(dt.UTC),
    )
    projector = AlpacaSipRuntimeEvidenceProjector(evidence, state_dir / "canonical")
    return AlpacaSipHistoricalProfileCollector(page_client, evidence, projector)


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Alpaca SIP historical volume profile",
            "",
            "> GET-only raw-first historical evidence 결과입니다.",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
