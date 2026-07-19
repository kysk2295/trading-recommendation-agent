#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.kr_theme_day_session_manifest import (
    InvalidKrThemeDaySessionManifestError,
    load_kr_theme_day_session_manifest,
)
from trading_agent.kr_theme_day_session_verifier import (
    InvalidKrThemeDaySessionVerificationError,
    KrThemeDaySessionVerificationResult,
    verify_kr_theme_day_session,
)
from trading_agent.private_report import write_private_report

REPORT_NAME = "kr_theme_day_session_verification_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR theme day query-only cross-store session verification")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = load_kr_theme_day_session_manifest(args.manifest)
        result = verify_kr_theme_day_session(manifest)
    except (
        InvalidKrThemeDaySessionManifestError,
        InvalidKrThemeDaySessionVerificationError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _write_report(args.output_dir, None)
        return 1
    _write_report(args.output_dir, result)
    return 0 if result.ready else 1


def _write_report(
    output_dir: Path,
    result: KrThemeDaySessionVerificationResult | None,
) -> None:
    verified = result is not None and result.ready
    completed = 0 if result is None else result.completed_count
    blocked = 0 if result is None else result.blocked_count
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme day session verification",
                "",
                "> query-only cross-store verification; child, provider, account와 주문을 호출하지 않습니다.",
                "",
                f"- result: {'verified' if verified else 'blocked'}",
                f"- verified completed phases: {completed}",
                f"- latest blocked phases: {blocked}",
                "- external mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
