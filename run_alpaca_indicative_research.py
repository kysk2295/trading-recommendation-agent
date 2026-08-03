#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.alpaca_http import DEFAULT_ALPACA_SECRET_PATH
from trading_agent.alpaca_indicative_research import (
    IndicativeResearchCollection,
    IndicativeResearchCollectionError,
    IndicativeResearchPlan,
    collect_indicative_research,
    indicative_research_requires_network,
    plan_indicative_research,
)
from trading_agent.alpaca_indicative_research_service_config import (
    IndicativeResearchServiceConfig,
    InvalidIndicativeResearchServiceError,
    load_indicative_research_service_config,
    verify_indicative_research_launch_agent,
    write_indicative_research_launch_agent,
    write_indicative_research_service_config,
)
from trading_agent.alpaca_option_chain_client import (
    AlpacaOptionChainClient,
    AlpacaOptionChainTransportError,
    create_alpaca_option_chain_http_client,
)
from trading_agent.alpaca_option_chain_models import AlpacaOptionChainError
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStoreError
from trading_agent.alpaca_option_contract_client import AlpacaOptionContractClient
from trading_agent.alpaca_option_contract_collection import AlpacaOptionContractTransportError
from trading_agent.alpaca_option_contract_models import AlpacaOptionContractError
from trading_agent.alpaca_option_contract_store import AlpacaOptionContractStoreError
from trading_agent.alpaca_paper_config import create_alpaca_paper_read_client
from trading_agent.alpaca_private_credentials import (
    PrivateAlpacaCredentialsError,
    load_private_alpaca_credentials,
)
from trading_agent.private_stable_report import InvalidPrivateStableReportError, write_private_stable_report

REPORT_NAME = "alpaca_indicative_research_service_ko.md"
Clock = Callable[[], dt.datetime]
CollectionRunner = Callable[
    [IndicativeResearchServiceConfig, IndicativeResearchPlan],
    IndicativeResearchCollection,
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="무료 Alpaca indicative 옵션 연구 데이터 서비스")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision", help="private config와 LaunchAgent plist 생성")
    provision.add_argument("--label", required=True)
    provision.add_argument("--project-root", type=Path, required=True)
    provision.add_argument("--uv-path", type=Path, required=True)
    provision.add_argument("--outputs-root", type=Path, required=True)
    provision.add_argument("--credentials-path", type=Path, default=DEFAULT_ALPACA_SECRET_PATH)
    provision.add_argument("--runtime-output-root", type=Path, required=True)
    provision.add_argument("--config", type=Path, required=True)
    provision.add_argument("--plist", type=Path, required=True)
    provision.add_argument("--output-dir", type=Path, required=True)
    tick = commands.add_parser("tick", help="뉴욕 정규장 기준 무료 indicative 체인 one-shot 수집")
    tick.add_argument("--config", type=Path, required=True)
    tick.add_argument("--output-dir", type=Path, required=True)
    verify = commands.add_parser("verify", help="private config와 LaunchAgent 계약 검증")
    verify.add_argument("--config", type=Path, required=True)
    verify.add_argument("--plist", type=Path, required=True)
    verify.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
    collection_runner: CollectionRunner | None = None,
) -> int:
    args = parse_args(argv)
    try:
        match args.command:
            case "provision":
                _provision(args)
                return 0 if _write_report(args.output_dir, "provision", "ready", "none", None) else 1
            case "verify":
                verified = verify_indicative_research_launch_agent(args.config, args.plist)
                state = "verified" if verified.ready else "blocked"
                return 0 if verified.ready and _write_report(args.output_dir, "verify", state, "none", None) else 1
            case "tick":
                config = load_indicative_research_service_config(args.config)
                plan = plan_indicative_research(clock())
                if plan is None:
                    return 0 if _write_report(args.output_dir, "tick", "waiting_session", "none", None) else 1
                runner = _production_collection if collection_runner is None else collection_runner
                result = runner(config, plan)
                state = "replayed" if result.replayed else "collected"
                return 0 if _write_report(args.output_dir, "tick", state, "none", result) else 1
            case unreachable:
                assert_never(unreachable)
    except (
        AlpacaOptionChainError,
        AlpacaOptionChainStoreError,
        AlpacaOptionChainTransportError,
        AlpacaOptionContractError,
        AlpacaOptionContractStoreError,
        AlpacaOptionContractTransportError,
        IndicativeResearchCollectionError,
        InvalidIndicativeResearchServiceError,
        OSError,
        PrivateAlpacaCredentialsError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        _ = _write_report(args.output_dir, args.command, "blocked", "collection_invalid", None)
        return 1


def _provision(args: argparse.Namespace) -> None:
    config_path = _absolute(args.config)
    config = IndicativeResearchServiceConfig(
        label=args.label,
        project_root=_absolute(args.project_root),
        uv_path=_absolute(args.uv_path),
        outputs_root=_absolute(args.outputs_root),
        credentials_path=_absolute(args.credentials_path),
        report_root=_absolute(args.runtime_output_root),
    )
    _ = write_indicative_research_service_config(config_path, config)
    _ = write_indicative_research_launch_agent(_absolute(args.plist), config, config_path)
    _ = verify_indicative_research_launch_agent(config_path, _absolute(args.plist))


def _production_collection(
    config: IndicativeResearchServiceConfig,
    plan: IndicativeResearchPlan,
) -> IndicativeResearchCollection:
    if not indicative_research_requires_network(plan, config.outputs_root):
        return collect_indicative_research(plan, config.outputs_root, None, None)
    credentials = load_private_alpaca_credentials(config.credentials_path)
    with create_alpaca_paper_read_client() as catalog_http, create_alpaca_option_chain_http_client() as chain_http:
        return collect_indicative_research(
            plan,
            config.outputs_root,
            AlpacaOptionContractClient(catalog_http, credentials),
            AlpacaOptionChainClient(chain_http, credentials),
        )


def _write_report(
    output_dir: Path,
    operation: str,
    state: str,
    reason: str,
    result: IndicativeResearchCollection | None,
) -> bool:
    try:
        write_private_stable_report(
            output_dir / REPORT_NAME,
            "\n".join(
                (
                    "# Alpaca indicative options research service",
                    "",
                    "> Free delayed/indicative research evidence only; never OPRA or order authority.",
                    "",
                    f"- operation: {operation}",
                    f"- result: {state}",
                    f"- reason: {reason}",
                    f"- session date: {'none' if result is None else result.session_date.isoformat()}",
                    f"- expiration date: {'none' if result is None else result.expiration_date.isoformat()}",
                    f"- option snapshots: {0 if result is None else result.chain_snapshots}",
                    f"- option contracts: {0 if result is None else result.contracts}",
                    f"- network sources: {0 if result is None else result.network_sources}",
                    "- source feed: indicative",
                    "- OPRA authority: false",
                    "- provider operation: GET-only",
                    "- broker, account, allocation, or order mutation: none",
                    "",
                )
            ),
        )
        return True
    except InvalidPrivateStableReportError:
        return False


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


if __name__ == "__main__":
    raise SystemExit(main())
