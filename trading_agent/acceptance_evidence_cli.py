from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from trading_agent.acceptance_evidence import (
    AcceptanceEvidenceBuildRequest,
    AcceptanceEvidenceFailure,
    AcceptanceEvidenceManifest,
    InvalidAcceptanceEvidenceError,
    build_acceptance_manifest,
    verify_acceptance_manifest,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operational acceptance evidence manifest")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build a manifest from a typed request")
    build.add_argument("--request", type=Path, required=True)
    build.add_argument("--repository", type=Path, default=Path.cwd())
    build.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="verify a manifest against repository evidence")
    verify.add_argument("--criterion", required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--repository", type=Path, default=Path.cwd())
    verify.add_argument("--require-clean-commit", action="store_true")
    verify.add_argument("--require-session-binding", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        match args.command:
            case "build":
                request = AcceptanceEvidenceBuildRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
                manifest = build_acceptance_manifest(request, args.repository, args.output)
                _print_result({"criterion_id": manifest.criterion_id, "result": "built"})
            case "verify":
                manifest = AcceptanceEvidenceManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
                if manifest.criterion_id != args.criterion:
                    raise InvalidAcceptanceEvidenceError(AcceptanceEvidenceFailure.CRITERION_MISMATCH)
                verify_acceptance_manifest(
                    manifest,
                    args.repository,
                    require_clean_commit=args.require_clean_commit,
                    require_session_binding=args.require_session_binding,
                )
                _print_result({"criterion_id": manifest.criterion_id, "result": "verified"})
            case unreachable:
                assert_never(unreachable)
    except (InvalidAcceptanceEvidenceError, OSError, UnicodeError, ValidationError) as error:
        reason = error.reason.value if isinstance(error, InvalidAcceptanceEvidenceError) else "invalid_manifest"
        _print_result({"reason": reason, "result": "blocked"})
        return 1
    return 0


def _print_result(payload: dict[str, str]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
