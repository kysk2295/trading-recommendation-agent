from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH


def parse_runtime_fleet_cycle_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US scanner/profile을 Alpaca SIP GET-only runtime fleet와 M4.4 gate에 연결",
    )
    parser.add_argument("--scanner-store", type=Path, required=True)
    profile_source = parser.add_mutually_exclusive_group(required=True)
    profile_source.add_argument("--profile", action="append", metavar="INSTRUMENT_ID=PATH")
    profile_source.add_argument("--auto-profile-root", type=Path)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--audit-store", type=Path, required=True)
    parser.add_argument("--policy-state-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--research-artifact-root", type=Path)
    parser.add_argument("--conditional-signal-outbox", type=Path)
    parser.add_argument("--actionability-manifest-root", type=Path)
    parser.add_argument("--dynamic-plan-store", type=Path)
    parser.add_argument("--live-actionability-receipt-root", type=Path)
    parser.add_argument("--live-actionability-store", type=Path)
    parser.add_argument("--arm-live-actionability", action="store_true")
    parser.add_argument("--minimum-rvol-bps", type=int, default=15_000)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    parser.add_argument("--capacity", type=int, default=2)
    parser.add_argument("--max-candidate-age-seconds", type=int, default=30)
    parser.add_argument("--minimum-residency-seconds", type=int, default=120)
    parser.add_argument("--eviction-cooldown-seconds", type=int, default=300)
    return parser.parse_args(argv)


__all__ = ("parse_runtime_fleet_cycle_args",)
