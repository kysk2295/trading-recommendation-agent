#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.4", "httpx2[http2,brotli,zstd]", "pyarrow==25.0.0", "pydantic>=2.11"]
# ///
# --- How to run ---
# uv run python run_us_news_catalyst_cohort_collect.py --help

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import (
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaCredentials,
    create_alpaca_news_http_client,
    load_alpaca_credentials,
)
from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_news_catalyst_cohort_collection import (
    UsNewsCatalystCohortCollectionPaths,
    UsNewsCatalystCohortCollector,
)
from trading_agent.us_news_catalyst_collection_artifact import (
    collection_plan_path,
    collection_receipt_path,
    load_us_news_catalyst_collection_plan,
)
from trading_agent.us_news_catalyst_trial_artifact import load_us_news_catalyst_cohort

REPORT_NAME = "us_news_catalyst_cohort_collection_ko.md"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystCollectionCliDependencies:
    clock: Callable[[], dt.datetime]
    client_factory: Callable[[], httpx2.Client]
    credentials_loader: Callable[[Path], AlpacaCredentials]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


DEFAULT_DEPENDENCIES = UsNewsCatalystCollectionCliDependencies(
    clock=_utc_now,
    client_factory=create_alpaca_news_http_client,
    credentials_loader=load_alpaca_credentials,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="동결된 US news-catalyst cohort 전 종목을 Alpaca SIP GET-only로 수집",
    )
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--security-master-store", type=Path, required=True)
    parser.add_argument("--plan-root", type=Path, required=True)
    parser.add_argument("--profile-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    dependencies: UsNewsCatalystCollectionCliDependencies = DEFAULT_DEPENDENCIES,
) -> int:
    args = parse_args(argv)
    credential_read = False
    try:
        if type(dependencies) is not UsNewsCatalystCollectionCliDependencies:
            raise ValueError
        cohort = load_us_news_catalyst_cohort(args.cohort)
        security_master = AlpacaSecurityMasterStore(
            args.security_master_store
        ).latest_snapshot()
        if security_master is None:
            raise ValueError
        plan_path = collection_plan_path(args.plan_root, cohort.artifact_id)
        receipt_path = collection_receipt_path(args.receipt_root, cohort.artifact_id)
        if receipt_path.exists() and not plan_path.exists():
            raise ValueError
        evaluated_at = dependencies.clock()
        evidence_at = evaluated_at
        if plan_path.exists():
            evidence_at = load_us_news_catalyst_collection_plan(
                plan_path
            ).content.evaluated_at
        replay = receipt_path.exists()
        credentials = AlpacaCredentials("local-replay", "local-replay")
        if not replay:
            credentials = dependencies.credentials_loader(args.secret_path)
            credential_read = True
        with dependencies.client_factory() as client:
            result = UsNewsCatalystCohortCollector(
                AlpacaSipMinutePageClient(
                    client,
                    credentials,
                    clock=lambda: evidence_at,
                ),
                UsNewsCatalystCohortCollectionPaths(
                    args.plan_root,
                    args.profile_root,
                    args.runtime_root,
                    args.canonical_root,
                    args.feature_root,
                    args.receipt_root,
                ),
            ).collect(cohort, security_master, evaluated_at=evaluated_at)
        _write_report(
            args.output_dir,
            ready=True,
            created=result.created,
            credential_read=credential_read,
        )
        return 0
    except (OSError, TypeError, ValueError, httpx2.HTTPError):
        _write_report(
            args.output_dir,
            ready=False,
            created=False,
            credential_read=credential_read,
        )
        return 1


def _write_report(
    output_dir: Path,
    *,
    ready: bool,
    created: bool,
    credential_read: bool,
) -> None:
    result = "ready" if ready else "blocked"
    receipt = ("new" if created else "replay") if ready else "not-published"
    lines = (
        "# US news-catalyst frozen cohort collection",
        "",
        "> Alpaca SIP market data GET-only research evidence; no account or order authority.",
        "",
        f"- result: {result}",
        f"- collection receipt: {receipt}",
        f"- credential read: {1 if credential_read else 0}",
        "- provider method: GET-only",
        "- account/order mutation: 0",
        "",
    )
    write_private_stable_report(output_dir / REPORT_NAME, "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
