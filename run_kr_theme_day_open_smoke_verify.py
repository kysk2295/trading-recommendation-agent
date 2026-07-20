#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.kr_theme_day_onboarding import require_exact_kr_theme_day_onboarding
from trading_agent.kr_theme_day_onboarding_models import onboarding_receipt_path
from trading_agent.kr_theme_day_open_smoke import (
    KrThemeDayOpenSmokeEvidence,
    attest_kr_theme_day_open_smoke,
    load_kr_theme_day_open_smoke,
    load_kr_theme_day_open_smoke_query_only,
    publish_kr_theme_day_open_smoke,
)
from trading_agent.kr_theme_day_open_smoke_paths import (
    path_aliases,
    path_resolves,
    path_uses_protected_file,
    require_private_source_path,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_evidence_store import KrThemeDaySessionEvidenceStore
from trading_agent.kr_theme_day_session_manifest import (
    KrThemeDaySessionManifest,
    load_kr_theme_day_session_manifest_query_only,
)
from trading_agent.kr_theme_day_session_verifier import verify_kr_theme_day_session
from trading_agent.private_immutable_alias import publish_private_immutable_alias
from trading_agent.private_stable_report import (
    InvalidPrivateStableReportError,
)
from trading_agent.private_stable_report import (
    write_private_stable_report as write_private_report,
)

Clock = Callable[[], dt.datetime]
REPORT_NAME = "kr_theme_day_open_smoke_verification_ko.md"


@dataclass(frozen=True, slots=True)
class _PublicationTarget:
    destination: Path
    protected_aliases: tuple[Path, ...]
    protected_files: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _EvidencePublication:
    created: bool


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attest a production-manifest KR open-session read-only smoke")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    report_path = args.output_dir / REPORT_NAME
    immutable_inputs = (args.manifest, onboarding_receipt_path(args.manifest))
    report_allowed = False
    publication: _EvidencePublication | None = None
    try:
        if not path_resolves(report_path) or path_uses_protected_file(report_path, immutable_inputs):
            return 1
        report_allowed = True
        manifest = load_kr_theme_day_session_manifest_query_only(args.manifest)
        report_allowed = False
        protected_files = (
            manifest.paths.experiment_ledger,
            manifest.paths.calendar_store,
            manifest.paths.opportunity_outbox,
            manifest.paths.receipt_store,
            manifest.paths.entry_store,
            manifest.paths.exit_store,
            manifest.paths.terminal_store,
            manifest.paths.review_store,
            manifest.paths.audit_store,
            KrThemeDaySessionEvidenceStore(manifest.paths.audit_store).path,
        )
        protected_aliases = (
            *protected_files,
            manifest.paths.output_root,
        )
        if path_aliases(report_path, protected_aliases) or path_uses_protected_file(report_path, protected_files):
            return 1
        report_allowed = True
        if not path_resolves(args.evidence):
            raise ValueError
        if path_uses_protected_file(report_path, (args.evidence,)) or path_uses_protected_file(
            args.evidence,
            (report_path,),
        ):
            report_allowed = False
            return 1
        if path_aliases(args.evidence, protected_aliases) or path_uses_protected_file(
            args.evidence,
            (*immutable_inputs, *protected_files),
        ):
            return 1
        for protected_file in protected_files:
            require_private_source_path(protected_file)
        require_exact_kr_theme_day_onboarding(args.manifest, manifest)
        existing = _existing_evidence(args.evidence)
        verified_at = clock() if existing is None else existing.verified_at
        evidence = _current_evidence(manifest, verified_at)
        if existing is not None and existing != evidence:
            raise ValueError
        publication = (
            _publish_new_stable_evidence(
                manifest,
                evidence,
                _PublicationTarget(
                    destination=args.evidence,
                    protected_aliases=(
                        *immutable_inputs,
                        *protected_aliases,
                        report_path,
                    ),
                    protected_files=(*immutable_inputs, *protected_files, report_path),
                ),
            )
            if existing is None
            else _EvidencePublication(False)
        )
        if existing is not None and _current_evidence(manifest, verified_at) != evidence:
            raise ValueError
    except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
        if report_allowed:
            try:
                _write_report(args.output_dir, None)
            except (OSError, InvalidPrivateStableReportError):
                return 1
        return 1
    if publication is None:
        return 1
    try:
        _write_report(args.output_dir, publication.created)
    except (OSError, InvalidPrivateStableReportError):
        return 0
    return 0


def _existing_evidence(path: Path) -> KrThemeDayOpenSmokeEvidence | None:
    if path.is_symlink() or path.exists():
        return load_kr_theme_day_open_smoke_query_only(path)
    return None


def _current_evidence(
    manifest: KrThemeDaySessionManifest,
    verified_at: dt.datetime,
) -> KrThemeDayOpenSmokeEvidence:
    verification = verify_kr_theme_day_session(manifest)
    events = KrThemeDaySessionAuditStore(manifest.paths.audit_store).events(manifest.session_id)
    attestations = KrThemeDaySessionEvidenceStore(manifest.paths.audit_store).attestations(manifest.session_id)
    return attest_kr_theme_day_open_smoke(
        manifest,
        verification,
        events,
        attestations,
        verified_at,
    )


def _publish_new_stable_evidence(
    manifest: KrThemeDaySessionManifest,
    evidence: KrThemeDayOpenSmokeEvidence,
    target: _PublicationTarget,
) -> _EvidencePublication:
    destination = target.destination
    pending = destination.with_name(f".{destination.name}.{evidence.evidence_id}.pending")
    if path_aliases(pending, target.protected_aliases) or path_uses_protected_file(
        pending,
        target.protected_files,
    ):
        raise ValueError
    pending_created = False
    cleanup_owned_by_caller = True
    final_created = False
    try:
        pending_created = publish_kr_theme_day_open_smoke(pending, evidence)
        if not pending_created:
            raise ValueError
        if _current_evidence(manifest, evidence.verified_at) != evidence:
            raise ValueError
        if load_kr_theme_day_open_smoke(pending) != evidence:
            raise ValueError
        cleanup_owned_by_caller = False
        final_created = publish_private_immutable_alias(pending, destination)
        if not final_created:
            raise ValueError
        return _EvidencePublication(True)
    finally:
        if pending_created and cleanup_owned_by_caller:
            pending.unlink(missing_ok=True)


def _write_report(output_dir: Path, created: bool | None) -> None:
    verified = created is not None
    new_count = int(created is True)
    replay_count = int(created is False)
    write_private_report(
        output_dir / REPORT_NAME,
        "\n".join(
            (
                "# KR theme day open-session smoke verification",
                "",
                "> production manifest와 query-only source attestation만 검증합니다.",
                "",
                f"- result: {'verified' if verified else 'blocked'}",
                f"- evidence 신규/재사용: {new_count}/{replay_count}",
                "- provider request: 0",
                "- external mutation: 0",
                "",
            )
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
