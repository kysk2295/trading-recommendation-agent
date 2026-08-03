from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from trading_agent.research_agent_service_config import (
    RESEARCH_AGENT_SERVICE_LABEL,
    ResearchAgentServiceConfig,
)
from trading_agent.research_agent_sources import ResearchAgentSourcePaths
from trading_agent.research_agent_systematic import SystematicResearchActionConfig

_SOURCE_PATH_OPTIONS = (
    "source-outputs-root",
    "source-market-context-root",
    "source-day-session-root",
    "source-swing-shadow-database",
    "source-swing-review-database",
    "source-experiment-ledger",
    "source-lane-review-database",
)
_SYSTEMATIC_PATH_OPTIONS = (
    "systematic-context",
    "systematic-experiment-ledger",
    "systematic-receipt-root",
    "systematic-strategy-root",
    "systematic-manifest-root",
    "systematic-queue-root",
    "systematic-input-activation",
    "systematic-artifact-root",
    "systematic-review-root",
    "systematic-runs-root",
)


def parse_service_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Six-family persistent research agent runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision", help="private config와 단일 LaunchAgent 생성")
    _add_provision_arguments(provision)
    for name in ("verify", "status", "activate"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--plist", type=Path, required=True)
    replace = commands.add_parser("replace")
    for pair in ("current", "candidate"):
        replace.add_argument(f"--{pair}-config", type=Path, required=True)
        replace.add_argument(f"--{pair}-plist", type=Path, required=True)
    for name in ("tick", "cycle", "run"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
    return parser.parse_args(argv)


def config_from_provision_args(args: argparse.Namespace) -> ResearchAgentServiceConfig:
    project_root = _absolute(args.project_root)
    uv_path = _absolute(args.uv_path)
    hermes = _absolute(args.hermes_executable)
    sources = ResearchAgentSourcePaths(
        outputs_root=_absolute(args.source_outputs_root),
        market_context_root=_absolute(args.source_market_context_root),
        day_session_root=_absolute(args.source_day_session_root),
        swing_shadow_database=_absolute(args.source_swing_shadow_database),
        swing_review_database=_absolute(args.source_swing_review_database),
        experiment_ledger=_absolute(args.source_experiment_ledger),
        lane_review_database=_absolute(args.source_lane_review_database),
    )
    fixture = None if args.systematic_response_fixture is None else _absolute(args.systematic_response_fixture)
    systematic = SystematicResearchActionConfig(
        project_root=project_root,
        uv_executable=uv_path,
        python_executable=_absolute(args.python_executable),
        context=_absolute(args.systematic_context),
        response_fixture=fixture,
        hermes_executable=hermes if fixture is None else None,
        model_id=args.model_id,
        provider_id=args.provider_id,
        experiment_ledger=_absolute(args.systematic_experiment_ledger),
        receipt_root=_absolute(args.systematic_receipt_root),
        strategy_root=_absolute(args.systematic_strategy_root),
        manifest_root=_absolute(args.systematic_manifest_root),
        queue_root=_absolute(args.systematic_queue_root),
        input_activation=_absolute(args.systematic_input_activation),
        artifact_root=_absolute(args.systematic_artifact_root),
        review_root=_absolute(args.systematic_review_root),
        runs_root=_absolute(args.systematic_runs_root),
        max_runtime_seconds=args.max_runtime_seconds,
        max_bars=args.max_bars,
        max_sessions=args.max_sessions,
        rss_limit_gib=args.rss_limit_gib,
    )
    return ResearchAgentServiceConfig(
        label=RESEARCH_AGENT_SERVICE_LABEL,
        project_root=project_root,
        uv_path=uv_path,
        hermes_executable=hermes,
        model_id=args.model_id,
        provider_id=args.provider_id,
        cycle_database=_absolute(args.cycle_database),
        output_root=_absolute(args.output_root),
        hermes_database=_absolute(args.hermes_database),
        source_paths=sources,
        systematic=systematic,
    )


def _add_provision_arguments(parser: argparse.ArgumentParser) -> None:
    for option in (
        "project-root",
        "uv-path",
        "hermes-executable",
        "python-executable",
        "cycle-database",
        "output-root",
        "hermes-database",
        "config",
        "plist",
        *_SOURCE_PATH_OPTIONS,
        *_SYSTEMATIC_PATH_OPTIONS,
    ):
        parser.add_argument(f"--{option}", type=Path, required=True)
    parser.add_argument("--systematic-response-fixture", type=Path)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--max-runtime-seconds", type=float, default=600.0)
    parser.add_argument("--max-bars", type=int, default=100_000)
    parser.add_argument("--max-sessions", type=int, default=60)
    parser.add_argument("--rss-limit-gib", type=float, default=9.5)


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


__all__ = ("config_from_provision_args", "parse_service_args")
