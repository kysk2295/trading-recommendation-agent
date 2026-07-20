#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsOpportunityEvidenceError,
)
from trading_agent.alpaca_news_opportunity_evidence_artifact import (
    load_alpaca_news_opportunity_evidence,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
    write_private_stable_report,
)
from trading_agent.us_news_catalyst_opportunity import (
    OPPORTUNITY_VALIDITY,
    UsNewsCatalystProjectionError,
    UsNewsCatalystProjectionStatus,
    project_registered_us_news_catalyst_opportunity,
)
from trading_agent.us_news_catalyst_opportunity_artifact import (
    publish_us_news_catalyst_opportunity_projection,
)
from trading_agent.us_news_catalyst_research_registration import (
    InvalidUsNewsCatalystResearchRegistrationError,
    UsNewsCatalystProjectionAuthorityRequest,
    load_us_news_catalyst_research_manifest,
)

REPORT_NAME = "us_news_catalyst_opportunity_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="사전등록된 US news-catalyst baseline Opportunity을 발행"
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--registration-manifest", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    try:
        evidence = load_alpaca_news_opportunity_evidence(args.evidence)
        manifest = load_us_news_catalyst_research_manifest(args.registration_manifest)
        now = clock()
        observed_at = evidence.assessment.assessed_at
        if (
            not _aware(now)
            or now < observed_at
            or now >= observed_at + OPPORTUNITY_VALIDITY
        ):
            raise UsNewsCatalystProjectionError
        request = UsNewsCatalystProjectionAuthorityRequest(
            strategy_version=manifest.strategy_version,
            code_version=manifest.code_version,
            projected_at=observed_at,
        )
        projection = project_registered_us_news_catalyst_opportunity(
            evidence,
            ExperimentLedgerReader(args.experiment_ledger),
            request,
        )
        _, created = publish_us_news_catalyst_opportunity_projection(
            args.output_dir,
            projection,
        )
        _write_report(
            args.output_dir,
            result=projection.status.value,
            eligible_symbol_count=projection.eligible_symbol_count,
            created=created,
        )
        return 0 if projection.status is UsNewsCatalystProjectionStatus.RANKED else 2
    except (
        AlpacaNewsOpportunityEvidenceError,
        InvalidExperimentLedgerSourceError,
        InvalidPrivateStableReportError,
        InvalidUsNewsCatalystResearchRegistrationError,
        OSError,
        sqlite3.Error,
        UnsupportedExperimentLedgerSchemaError,
        UsNewsCatalystProjectionError,
        ValidationError,
        ValueError,
    ):
        _write_report(
            args.output_dir,
            result="blocked",
            eligible_symbol_count=0,
            created=False,
        )
        return 1


def _write_report(
    output_dir: Path,
    *,
    result: str,
    eligible_symbol_count: int,
    created: bool,
) -> None:
    lines = (
        "# US news-catalyst Opportunity",
        "",
        (
            "> recent provider-time news discovery only; no direction, entry price, "
            "profitability claim, or order authority."
        ),
        "",
        f"- 결과: {result}",
        f"- eligible symbols: {eligible_symbol_count}",
        f"- artifact 신규/재사용: {int(created)}/{int(not created)}",
        "- provider request: 0",
        "- credential read: 0",
        "- account read: 0",
        "- order mutation: 0",
        "",
    )
    write_private_stable_report(output_dir / REPORT_NAME, "\n".join(lines))


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


if __name__ == "__main__":
    raise SystemExit(main())
