#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path

from trading_agent.alpaca_http import (
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    load_alpaca_credentials,
)
from trading_agent.alpaca_security_master import (
    collect_alpaca_security_master,
    create_alpaca_security_master_client,
)
from trading_agent.alpaca_security_master_models import AlpacaSecurityMasterError
from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore
from trading_agent.private_report import write_private_report

REPORT_NAME = "alpaca_security_master_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Alpaca Paper의 US asset master를 GET-only raw-first 원장으로 수집"
    )
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    observed_at = clock().astimezone(dt.UTC)
    try:
        credentials = load_alpaca_credentials(args.secret_path)
        store = AlpacaSecurityMasterStore(args.store)
        with create_alpaca_security_master_client() as client:
            snapshot = collect_alpaca_security_master(
                client,
                credentials,
                store,
                observed_at=observed_at,
            )
    except (
        AlpacaSecurityMasterError,
        AlpacaSecretFileError,
        MissingAlpacaCredentialsError,
        OSError,
    ):
        _write_report(
            args.output_dir,
            (
                "result: blocked",
                "network GET: 0 or 1",
                "account/order mutation: 0",
            ),
        )
        return 1
    _write_report(
        args.output_dir,
        (
            "result: ready",
            f"observed at: {snapshot.observed_at.isoformat()}",
            f"active instrument: {len(snapshot.instruments)}",
            f"snapshot id prefix: {snapshot.snapshot_id[:12]}",
            "network GET: 1",
            "account/order mutation: 0",
        ),
    )
    return 0


def _write_report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# Alpaca US security master",
            "",
            "> Paper asset endpoint의 GET-only raw-first 수집 결과입니다.",
            "",
            *(f"- {detail}" for detail in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
