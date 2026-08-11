from __future__ import annotations

import argparse


def build_future_session_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare or activate provenance-bound future-session jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Atomically prepare local artifacts.")
    prepare.add_argument("--request", required=True)
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--output-dir", required=True)
    activate = commands.add_parser("activate", help="Install and bootstrap prepared jobs.")
    activate.add_argument("--manifest", required=True)
    prepare_kr = commands.add_parser("prepare-kr", help="Atomically prepare one KR full-session supervisor.")
    prepare_kr.add_argument("--request", required=True)
    prepare_kr.add_argument("--plan", required=True)
    prepare_kr.add_argument("--output-dir", required=True)
    activate_kr = commands.add_parser("activate-kr", help="Install and bootstrap the prepared KR supervisor.")
    activate_kr.add_argument("--manifest", required=True)
    preflight_kr = commands.add_parser(
        "supervise-kr-preflight",
        help="Verify bound KR authorities without executing a session.",
    )
    preflight_kr.add_argument("--manifest", required=True)
    supervise_kr = commands.add_parser("supervise-kr", help="Run or restart the bound KR read-only/shadow session.")
    supervise_kr.add_argument("--manifest", required=True)
    lifecycle_kr = commands.add_parser(
        "bootstrap-kr-lifecycle",
        help="Bind one registered KR Day strategy to an exact future open session.",
    )
    lifecycle_kr.add_argument("--database", required=True)
    lifecycle_kr.add_argument("--calendar-store", required=True)
    lifecycle_kr.add_argument("--rollover-bundle", required=True)
    lifecycle_kr.add_argument("--code-version", required=True)
    lifecycle_kr.add_argument("--strategy-version", required=True)
    lifecycle_kr.add_argument("--target-session", required=True)
    lifecycle_kr.add_argument("--decided-at", required=True)
    coordinate = commands.add_parser(
        "coordinate",
        help="Compile, prepare, activate, or verify one future session.",
    )
    coordinate.add_argument("--request", required=True)
    coordinate.add_argument("--plan", required=True)
    coordinate.add_argument("--launch-agents-dir", required=True)
    return parser


__all__ = ("build_future_session_parser",)
