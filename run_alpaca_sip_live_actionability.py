#!/usr/bin/env -S uv run --python 3.12 python

from __future__ import annotations

import argparse
import datetime as dt
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from trading_agent.alpaca_http import (
    DEFAULT_ALPACA_SECRET_PATH,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
)
from trading_agent.alpaca_private_credentials import (
    PrivateAlpacaCredentialsError,
    load_private_alpaca_credentials,
)
from trading_agent.alpaca_sip_dynamic_backoff import AlpacaSipDynamicBackoffConfig
from trading_agent.alpaca_sip_dynamic_plan_store import (
    AlpacaSipDynamicPlanStore,
    AlpacaSipDynamicPlanStoreError,
)
from trading_agent.alpaca_sip_dynamic_receipt_models import AlpacaSipDynamicReceiptError
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_reconnect_supervisor import (
    AlpacaSipDynamicReconnectSupervisorError,
)
from trading_agent.alpaca_sip_live_actionability import (
    AlpacaSipLiveActionabilityConfig,
    AlpacaSipLiveActionabilityDependencies,
    AlpacaSipLiveActionabilityError,
    AlpacaSipLiveActionabilityRequest,
    AlpacaSipLiveActionabilityStores,
    run_alpaca_sip_live_actionability,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    AlpacaSipQuoteActionabilityManifestError,
    read_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_projection import (
    AlpacaSipQuoteActionabilityProjectionError,
)
from trading_agent.alpaca_sip_quote_actionability_store import (
    AlpacaSipQuoteActionabilityStore,
    AlpacaSipQuoteActionabilityStoreError,
)
from trading_agent.alpaca_sip_trade_stream import connect_alpaca_sip_trade_stream
from trading_agent.kis_live import regular_session_is_open
from trading_agent.private_report import write_private_report
from trading_agent.us_subscription_policy_state import SubscriptionPolicyStateError
from trading_agent.us_subscription_policy_state_store import SubscriptionPolicyStateStore

REPORT_NAME = "alpaca_sip_live_actionability_ko.md"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture one bounded Alpaca SIP quote/trade epoch and project live actionability.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan-store", type=Path, required=True)
    parser.add_argument("--policy-state-store", type=Path, required=True)
    parser.add_argument("--receipt-store", type=Path, required=True)
    parser.add_argument("--actionability-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    parser.add_argument("--max-attempts", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--max-data-frames", type=int, choices=range(1, 11), default=10)
    parser.add_argument("--receive-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--arm-read-only", action="store_true")
    return parser.parse_args(argv)


def default_dependencies(
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> AlpacaSipLiveActionabilityDependencies:
    return AlpacaSipLiveActionabilityDependencies(
        connect_alpaca_sip_trade_stream,
        clock,
        lambda: uuid.uuid4().hex,
        lambda event, seconds: event.wait(seconds),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    dependencies: AlpacaSipLiveActionabilityDependencies | None = None,
) -> int:
    args = parse_args(argv)
    selected = default_dependencies() if dependencies is None else dependencies
    if not args.arm_read_only:
        _report(args.output_dir, "blocked", "0", "disabled")
        return 1
    try:
        if not regular_session_is_open(selected.clock()):
            raise AlpacaSipLiveActionabilityError
        manifest = read_alpaca_sip_quote_actionability_manifest(args.manifest)
        credentials = load_private_alpaca_credentials(args.secret_path)
        result = run_alpaca_sip_live_actionability(
            AlpacaSipLiveActionabilityRequest(
                credentials,
                manifest,
                AlpacaSipLiveActionabilityStores(
                    AlpacaSipDynamicPlanStore(args.plan_store),
                    SubscriptionPolicyStateStore(args.policy_state_store),
                    AlpacaSipDynamicReceiptStore(args.receipt_store),
                    AlpacaSipQuoteActionabilityStore(args.actionability_store),
                ),
                AlpacaSipLiveActionabilityConfig(
                    args.max_attempts,
                    AlpacaSipDynamicBackoffConfig(1.0, 2.0, 4.0),
                    args.max_data_frames,
                    args.receive_timeout_seconds,
                ),
            ),
            selected,
        )
    except (
        AlpacaSecretFileError,
        AlpacaSipDynamicPlanStoreError,
        AlpacaSipDynamicReceiptError,
        AlpacaSipDynamicReconnectSupervisorError,
        AlpacaSipLiveActionabilityError,
        AlpacaSipQuoteActionabilityManifestError,
        AlpacaSipQuoteActionabilityProjectionError,
        AlpacaSipQuoteActionabilityStoreError,
        MissingAlpacaCredentialsError,
        OSError,
        PrivateAlpacaCredentialsError,
        SubscriptionPolicyStateError,
        TypeError,
        ValueError,
    ):
        _report(args.output_dir, "blocked", "0", "blocked")
        return 1
    _report(
        args.output_dir,
        "projected",
        "new" if result.projection.appended else "replay",
        result.connection.status.value,
    )
    return 0


def _report(output_dir: Path, result: str, append: str, connection: str) -> None:
    content = "\n".join(
        (
            "# Alpaca SIP live actionability",
            "",
            f"- result: {result}",
            f"- connection: {connection}",
            f"- actionability append: {append}",
            "- account/order mutation: 0",
            "",
        )
    )
    write_private_report(output_dir / REPORT_NAME, content)


if __name__ == "__main__":
    raise SystemExit(main())
