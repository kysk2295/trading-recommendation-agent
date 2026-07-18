#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.private_report import write_private_report
from trading_agent.us_market_data_fleet_audit import RuntimeFleetAuditError
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore
from trading_agent.us_runtime_capability_projection import (
    UsRuntimeCapabilityProjectionError,
    project_us_runtime_capability,
)

REPORT_NAME = "us_runtime_capability_registry_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US runtime fleet audit을 append-only data capability registry health로 투영",
    )
    parser.add_argument("--audit-store", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.audit_store.expanduser().absolute() == args.registry.expanduser().absolute():
            raise ValueError
        audit = RuntimeFleetAuditStore(args.audit_store).latest()
        if audit is None:
            raise ValueError
        projection = project_us_runtime_capability(audit)
        store = DataCapabilityRegistryStore(args.registry)
        appended = store.append((projection.capability,), (projection.entitlement,))
        snapshot = store.snapshot(
            as_of=projection.assessed_at,
            source_ids=(projection.capability.source_id,),
        )
    except (
        DataCapabilityRegistryError,
        OSError,
        RuntimeFleetAuditError,
        UsRuntimeCapabilityProjectionError,
        ValueError,
    ):
        _write_report(args.output_dir, result="blocked", details=("projection validation: failed",))
        return 1
    ready_count = sum(item.ready for item in projection.owners)
    _write_report(
        args.output_dir,
        result="complete" if projection.complete else "incomplete",
        details=(
            "projection validation: passed",
            f"capability appended: {appended.capability_assessments}",
            f"entitlement appended: {appended.entitlements}",
            f"capability resolved: {len(snapshot.capabilities)}/1",
            f"entitlement resolved: {len(snapshot.entitlements)}/1",
            f"owner ready: {ready_count}/{len(projection.owners)}",
        ),
    )
    return 0 if projection.complete else 2


def _write_report(output_dir: Path, *, result: str, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# US runtime capability registry",
            "",
            "> existing local fleet audit projection only. "
            "Provider, credential, account, and order access are disabled.",
            "",
            f"- result: {result}",
            *(f"- {detail}" for detail in details),
            "- network access: 0",
            "- broker mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
