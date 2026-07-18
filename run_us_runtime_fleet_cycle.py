#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import (
    ALPACA_DATA_URL,
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    load_alpaca_credentials,
)
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_owner import (
    AlpacaSipRuntimeOwnerFactory,
    AlpacaSipRuntimeOwnerFactoryConfig,
)
from trading_agent.private_report import write_private_report
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_market_data_fleet import UsMarketDataFleet
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerProjectionError
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_runtime_fleet_cycle import (
    ProfileArtifactBinding,
    RuntimeFleetCycleError,
    RuntimeFleetCycleRequest,
    execute_runtime_fleet_cycle,
    prepare_runtime_fleet_cycle,
)
from trading_agent.us_subscription_models import SubscriptionPolicyConfig

REPORT_NAME = "us_runtime_fleet_cycle_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US scanner/profile을 Alpaca SIP GET-only runtime fleet와 M4.4 gate에 연결",
    )
    parser.add_argument("--scanner-store", type=Path, required=True)
    parser.add_argument("--profile", action="append", required=True, metavar="INSTRUMENT_ID=PATH")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--audit-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    parser.add_argument("--capacity", type=int, default=2)
    parser.add_argument("--max-candidate-age-seconds", type=int, default=30)
    parser.add_argument("--minimum-residency-seconds", type=int, default=120)
    parser.add_argument("--eviction-cooldown-seconds", type=int, default=300)
    return parser.parse_args(argv)


def create_data_client() -> httpx2.Client:
    return httpx2.Client(
        base_url=ALPACA_DATA_URL,
        follow_redirects=False,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
    client_factory: Callable[[], httpx2.Client] = create_data_client,
) -> int:
    args = parse_args(argv)
    evaluated_at = dt.datetime.now(dt.UTC) if now is None else now
    try:
        prepared = prepare_runtime_fleet_cycle(
            UsOpportunityScannerStore(args.scanner_store),
            RuntimeFleetCycleRequest(
                evaluated_at,
                (),
                (),
                _policy_config(args),
                _profile_bindings(args.profile),
            ),
        )
        credentials = load_alpaca_credentials(args.secret_path)
        with client_factory() as client:
            page_client = AlpacaSipMinutePageClient(
                client,
                credentials,
                clock=lambda: evaluated_at,
            )
            factory = AlpacaSipRuntimeOwnerFactory(
                page_client,
                AlpacaSipRuntimeOwnerFactoryConfig(
                    args.runtime_root,
                    args.canonical_root,
                    evaluated_at.astimezone(NEW_YORK).date(),
                    lambda: evaluated_at,
                ),
            )
            result = execute_runtime_fleet_cycle(
                prepared,
                UsMarketDataFleet(factory),
                RuntimeFleetAuditStore(args.audit_store),
            )
    except (
        AlpacaSecretFileError,
        MissingAlpacaCredentialsError,
        OSError,
        RuntimeFleetCycleError,
        TypeError,
        UsOpportunityScannerProjectionError,
        ValueError,
    ):
        _report(args.output_dir, ("result: blocked", "account/order mutation: 0"))
        return 1
    ready = result.audit.fleet_status == "ready" and result.audit.gate_status == "ready"
    _report(
        args.output_dir,
        (
            f"result: {'ready' if ready else 'blocked'}",
            f"fleet: {result.audit.fleet_status}",
            f"gate: {result.audit.gate_status}",
            f"owner count: {len(result.audit.owners)}",
            f"audit append: {'new' if result.audit_appended else 'replay'}",
            "account/order mutation: 0",
        ),
    )
    return 0 if ready else 1


def _profile_bindings(values: list[str]) -> tuple[ProfileArtifactBinding, ...]:
    bindings: list[ProfileArtifactBinding] = []
    for value in values:
        instrument_id, separator, raw_path = value.partition("=")
        if not separator or not instrument_id or not raw_path:
            raise RuntimeFleetCycleError
        bindings.append(ProfileArtifactBinding(instrument_id, Path(raw_path).expanduser().absolute()))
    return tuple(bindings)


def _policy_config(args: argparse.Namespace) -> SubscriptionPolicyConfig:
    return SubscriptionPolicyConfig(
        args.capacity,
        dt.timedelta(seconds=args.max_candidate_age_seconds),
        dt.timedelta(seconds=args.minimum_residency_seconds),
        dt.timedelta(seconds=args.eviction_cooldown_seconds),
    )


def _report(output_dir: Path, details: tuple[str, ...]) -> None:
    content = "\n".join(
        (
            "# US runtime fleet cycle",
            "",
            "> Scanner/profile 기반 Alpaca SIP GET-only M4.4 결과입니다.",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
