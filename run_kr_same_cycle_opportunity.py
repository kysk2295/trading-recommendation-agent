#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11", "rich>=14.0", "typer>=0.16", "websockets>=16,<17"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import stat
from collections.abc import Callable, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from pydantic import ValidationError

import run_kr_same_cycle_collect
import run_kr_theme_projection
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.kr_same_cycle_opportunity_run import (
    InvalidKrSameCycleOpportunityRunError,
    KrSameCycleOpportunityPreparation,
    load_kr_same_cycle_opportunity_policy,
    prepare_kr_same_cycle_opportunity_run,
)
from trading_agent.kr_theme_research_registration import (
    InvalidKrThemeResearchRegistrationError,
    KrThemeProjectionAuthorityRequest,
    require_registered_kr_theme_strategy,
)
from trading_agent.kr_theme_store import (
    InvalidKrThemeSourceError,
    KrThemeStore,
    UnsupportedKrThemeSchemaError,
)
from trading_agent.private_report import write_private_report
from trading_agent.signal_contract_models import OpportunitySnapshot

REPORT_NAME = "kr_same_cycle_opportunity_ko.md"
Clock = Callable[[], dt.datetime]
KST = ZoneInfo("Asia/Seoul")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR read-only same-cycle evidence를 live Opportunity로 projection")
    parser.add_argument("--collection-cycle-id", required=True)
    parser.add_argument(
        "--collection-date",
        type=dt.date.fromisoformat,
        required=True,
    )
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--experiment-ledger", type=Path, required=True)
    parser.add_argument("--collection-output-dir", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--projection-output-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    opportunity_count = 0
    try:
        _validate_targets(args)
        policy = load_kr_same_cycle_opportunity_policy(args.policy)
        authority_checked_at = clock()
        if args.fixture_root is None and authority_checked_at.astimezone(KST).date() != args.collection_date:
            raise InvalidKrSameCycleOpportunityRunError
        _ = require_registered_kr_theme_strategy(
            ExperimentLedgerReader(args.experiment_ledger),
            KrThemeProjectionAuthorityRequest(
                strategy_version=policy.producer_strategy_version,
                code_version=policy.runtime_code_version,
                projected_at=authority_checked_at,
            ),
        )
        run_kr_same_cycle_collect.main(
            collection_cycle_id=args.collection_cycle_id,
            collection_date=args.collection_date.isoformat(),
            database=str(args.database),
            output_dir=str(args.collection_output_dir),
            fixture_root=(None if args.fixture_root is None else str(args.fixture_root)),
        )
        store = KrThemeStore(args.database)
        prepared_at = (
            clock() if args.fixture_root is None else _exact_cycle_completed_at(store, args.collection_cycle_id)
        )
        prepared = prepare_kr_same_cycle_opportunity_run(
            store,
            KrSameCycleOpportunityPreparation(
                collection_cycle_id=args.collection_cycle_id,
                collection_date=args.collection_date,
                prepared_at=prepared_at,
                run_root=args.run_root.absolute(),
            ),
            policy,
        )
        run_kr_theme_projection.main(
            run_manifest=str(prepared.run_manifest),
            database=str(args.database),
            output_dir=str(args.projection_output_dir),
            experiment_ledger=str(args.experiment_ledger),
        )
        opportunity_count = _cycle_opportunity_count(
            args.projection_output_dir / "opportunities.v1.jsonl",
            args.collection_cycle_id,
        )
    except (
        InvalidExperimentLedgerSourceError,
        InvalidKrSameCycleOpportunityRunError,
        InvalidKrThemeResearchRegistrationError,
        InvalidKrThemeSourceError,
        OSError,
        sqlite3.Error,
        typer.BadParameter,
        typer.Exit,
        TypeError,
        UnsupportedExperimentLedgerSchemaError,
        UnsupportedKrThemeSchemaError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, result="blocked", opportunity_count=0)
        return 1
    result = "ready" if opportunity_count else "no_opportunity"
    _write_report(
        args.output_dir,
        result=result,
        opportunity_count=opportunity_count,
    )
    return 0


def _validate_targets(args: argparse.Namespace) -> None:
    databases = (args.database, args.experiment_ledger)
    artifacts = (
        args.output_dir / REPORT_NAME,
        args.collection_output_dir / "kr_same_cycle_summary_ko.md",
        args.collection_output_dir / "kr_same_cycle_coverage.csv",
        args.projection_output_dir / "opportunities.v1.jsonl",
        args.projection_output_dir / "kr_theme_projection_summary_ko.md",
    )
    database_targets = {
        candidate.expanduser().resolve(strict=False)
        for database in databases
        for candidate in (
            database,
            Path(f"{database}.writer.lock"),
            Path(f"{database}-journal"),
            Path(f"{database}-shm"),
            Path(f"{database}-wal"),
        )
    }
    if len({item.expanduser().resolve(strict=False) for item in databases}) != 2:
        raise InvalidKrSameCycleOpportunityRunError
    if any(artifact.expanduser().resolve(strict=False) in database_targets for artifact in artifacts):
        raise InvalidKrSameCycleOpportunityRunError
    for root in (
        args.collection_output_dir,
        args.run_root,
        args.projection_output_dir,
        args.output_dir,
    ):
        if root.is_symlink():
            raise InvalidKrSameCycleOpportunityRunError


def _cycle_opportunity_count(path: Path, collection_cycle_id: str) -> int:
    if not path.is_file() or path.is_symlink():
        return 0
    metadata = path.lstat()
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise InvalidKrSameCycleOpportunityRunError
    opportunities = tuple(
        OpportunitySnapshot.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line
    )
    return sum(
        any(
            evidence.namespace == "kr/collection_cycle" and evidence.record_id == collection_cycle_id
            for evidence in opportunity.evidence_refs
        )
        for opportunity in opportunities
    )


def _exact_cycle_completed_at(
    store: KrThemeStore,
    collection_cycle_id: str,
) -> dt.datetime:
    matches = tuple(item for item in store.cycles() if item.collection_cycle_id == collection_cycle_id)
    if len(matches) != 1:
        raise InvalidKrSameCycleOpportunityRunError
    return matches[0].completed_at


def _write_report(
    output_dir: Path,
    *,
    result: str,
    opportunity_count: int,
) -> None:
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR same-cycle Opportunity cycle",
                "",
                "> KIS/OpenDART/LS read-only evidence와 local projection만 실행합니다.",
                "",
                f"- result: {result}",
                f"- opportunity count: {opportunity_count}",
                "- source cycle contract: exact four-source terminal",
                "- order authority: false",
                "- domestic account endpoint: false",
                "- external account/order mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
