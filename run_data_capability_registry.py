#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.data_foundation_manifest import (
    InvalidDataFoundationManifestError,
    load_data_foundation_manifest,
)
from trading_agent.private_report import write_private_report
from trading_agent.strategy_data_gate import StrategyDataStatus, evaluate_strategy_data

REPORT_NAME = "data_capability_registry_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="data foundation의 source 계약과 시점별 health를 append-only registry에 확정"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = load_data_foundation_manifest(args.manifest)
        source_ids = tuple(item.source_id for item in manifest.capabilities)
        store = DataCapabilityRegistryStore(args.database)
        appended = store.append(manifest.capabilities, manifest.entitlements)
        snapshot = store.snapshot(as_of=manifest.evaluated_at, source_ids=source_ids)
        decision = evaluate_strategy_data(
            manifest.requirements,
            snapshot.capabilities,
            snapshot.entitlements,
            evaluated_at=manifest.evaluated_at,
        )
    except (
        DataCapabilityRegistryError,
        InvalidDataFoundationManifestError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, result="blocked", details=("registry validation: failed",))
        return 1
    _write_report(
        args.output_dir,
        result=decision.status.value,
        details=(
            "registry validation: passed",
            f"capability appended: {appended.capability_assessments}",
            f"entitlement appended: {appended.entitlements}",
            f"capability resolved: {len(snapshot.capabilities)}/{len(source_ids)}",
            f"entitlement resolved: {len(snapshot.entitlements)}/{len(source_ids)}",
        ),
    )
    return 0 if decision.status is StrategyDataStatus.READY else 2


def _write_report(output_dir: Path, *, result: str, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Data capability registry",
            "",
            "> local manifest/SQLite evaluation only. Provider, credential, account, and order access are disabled.",
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
