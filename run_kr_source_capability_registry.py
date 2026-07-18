#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from trading_agent.data_capability_models import DataHealthState
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.kr_source_capability_projection import (
    KrSourceCapabilityProjectionError,
    project_kr_source_capabilities,
)
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_source_capability_registry_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KR terminal source run을 append-only data capability registry health로 투영"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--collection-cycle-id", required=True)
    parser.add_argument("--collection-date", type=_date, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.database.expanduser().absolute() == args.registry.expanduser().absolute():
            raise ValueError
        runs = KrThemeStore(args.database).source_runs(args.collection_cycle_id)
        projection = project_kr_source_capabilities(runs)
        if projection.collection_date != args.collection_date:
            raise ValueError
        store = DataCapabilityRegistryStore(args.registry)
        appended = store.append(projection.capabilities, projection.entitlements)
        source_ids = tuple(item.source_id for item in projection.capabilities)
        snapshot = store.snapshot(as_of=projection.assessed_at, source_ids=source_ids)
    except (DataCapabilityRegistryError, KrSourceCapabilityProjectionError, OSError, ValueError):
        _write_report(args.output_dir, result="blocked", details=("projection validation: failed",))
        return 1
    _write_report(
        args.output_dir,
        result="complete" if projection.complete else "incomplete",
        details=(
            "projection validation: passed",
            f"capability appended: {appended.capability_assessments}",
            f"entitlement appended: {appended.entitlements}",
            f"source resolved: {len(snapshot.capabilities)}/{len(source_ids)}",
            f"failed source: {sum(item.health_state is DataHealthState.FAILED for item in snapshot.capabilities)}",
        ),
    )
    return 0 if projection.complete else 2


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("collection-date는 YYYY-MM-DD여야 합니다") from None


def _write_report(output_dir: Path, *, result: str, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# KR source capability registry",
            "",
            "> existing local ledger projection only. Provider, credential, account, and order access are disabled.",
            "",
            f"- 결과: {result}",
            *(f"- {detail}" for detail in details),
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
