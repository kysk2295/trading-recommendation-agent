from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.acceptance_evidence import (
    AcceptanceEvidenceBuildRequest,
    AcceptanceEvidenceManifest,
    AcceptanceSessionEvidence,
    acceptance_artifact_sha256,
    build_acceptance_manifest,
    require_clean_repository_commit,
)
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.us_day_acceptance_models import (
    US_DAY_POLICY_VERSION,
    InvalidUsDayAcceptanceEvidenceError,
    UsDayAcceptanceBuildRequest,
    UsDayEvidenceEnvelope,
    UsDayFinalReconciliationEvidence,
    UsDayHermesOutcomeReceiptEvidence,
    UsDayNaturalPaperLifecycleEvidence,
    UsDaySessionTerminal,
    UsDayThreeSessionReport,
)
from trading_agent.us_day_acceptance_models import (
    UsDayTerminalStatus as UsDayTerminalStatus,
)

US_DAY_VERIFIER_VERSION = "us-day-acceptance-v1"


@dataclass(frozen=True, slots=True)
class UsDayAcceptanceEvidenceBundle:
    three_session_report_path: Path
    natural_paper_lifecycle_path: Path
    final_reconciliation_path: Path
    hermes_outcome_receipt_path: Path
    manifest_path: Path
    manifest: AcceptanceEvidenceManifest
    report: UsDayThreeSessionReport


def build_three_session_report(
    terminals: tuple[UsDaySessionTerminal, ...],
    generated_at: dt.datetime | None = None,
) -> UsDayThreeSessionReport:
    validated = _validated_terminals(terminals)
    eligible = tuple(item for item in validated if item.is_real_scheduled_session)
    context = _envelope(validated, generated_at)
    delivery_passed = bool(eligible) and all(item.hermes_acknowledged for item in eligible)
    natural_passed = any(item.has_natural_lifecycle for item in eligible)
    reconciliation_passed = bool(eligible) and all(item.is_finally_reconciled for item in eligible)
    complete = len(eligible) >= 3 and delivery_passed and natural_passed and reconciliation_passed
    return UsDayThreeSessionReport(
        commit_sha=context.commit_sha,
        generated_at=context.generated_at,
        session_ids=context.session_ids,
        fixture_labels=context.fixture_labels,
        source_artifact_hashes=context.source_artifact_hashes,
        daily_terminal_count=len(validated),
        eligible_session_count=len(eligible),
        delivery_subgate_passed=delivery_passed,
        natural_paper_lifecycle_passed=natural_passed,
        final_reconciliation_passed=reconciliation_passed,
        operating_product_complete=complete,
    )


def write_us_day_acceptance_evidence(request: UsDayAcceptanceBuildRequest) -> UsDayAcceptanceEvidenceBundle:
    commit_sha = require_clean_repository_commit(request.repository)
    terminals = _load_terminals(request)
    if any(item.commit_sha != commit_sha for item in terminals):
        raise InvalidUsDayAcceptanceEvidenceError
    _verify_sources(request.repository, terminals)
    report = build_three_session_report(terminals, request.generated_at)
    eligible = tuple(item for item in terminals if item.is_real_scheduled_session)
    output_root = request.repository / "outputs/acceptance"
    report_paths = _report_paths(output_root)
    natural = _natural_evidence(report, eligible)
    reconciliation = _reconciliation_evidence(report, eligible)
    receipts = _receipt_evidence(report, eligible)
    _write(report_paths.three_session_report_path, report)
    _write(report_paths.natural_paper_lifecycle_path, natural)
    _write(report_paths.final_reconciliation_path, reconciliation)
    _write(report_paths.hermes_outcome_receipt_path, receipts)
    artifacts = tuple(path.relative_to(request.repository) for path in report_paths.artifact_paths)
    manifest = build_acceptance_manifest(
        AcceptanceEvidenceBuildRequest(
            criterion_id="AC-002",
            policy_version=US_DAY_POLICY_VERSION,
            verifier_version=US_DAY_VERIFIER_VERSION,
            generated_at=request.generated_at,
            sessions=tuple(
                AcceptanceSessionEvidence(
                    session_id=item.session_id,
                    market_id="us_equities",
                    kind=item.session_kind,
                    observed_from=item.observed_from,
                    observed_through=item.observed_through,
                )
                for item in eligible
            ),
            artifact_paths=artifacts,
            fixture_labels=report.fixture_labels,
            source_artifact_hashes=report.source_artifact_hashes,
        ),
        request.repository,
        report_paths.manifest_path,
    )
    return UsDayAcceptanceEvidenceBundle(
        report_paths.three_session_report_path,
        report_paths.natural_paper_lifecycle_path,
        report_paths.final_reconciliation_path,
        report_paths.hermes_outcome_receipt_path,
        report_paths.manifest_path,
        manifest,
        report,
    )


def _validated_terminals(terminals: tuple[UsDaySessionTerminal, ...]) -> tuple[UsDaySessionTerminal, ...]:
    if not terminals:
        raise InvalidUsDayAcceptanceEvidenceError
    try:
        validated = tuple(UsDaySessionTerminal.model_validate(item.model_dump(mode="python")) for item in terminals)
    except ValidationError:
        raise InvalidUsDayAcceptanceEvidenceError from None
    if (
        len({item.session_id for item in validated}) != len(validated)
        or len({item.commit_sha for item in validated}) != 1
    ):
        raise InvalidUsDayAcceptanceEvidenceError
    return tuple(sorted(validated, key=lambda item: item.session_id))


def _envelope(
    terminals: tuple[UsDaySessionTerminal, ...],
    generated_at: dt.datetime | None,
) -> UsDayEvidenceEnvelope:
    source_hashes = tuple(sorted({source.sha256 for item in terminals for source in item.source_artifacts}))
    return UsDayEvidenceEnvelope(
        commit_sha=terminals[0].commit_sha,
        generated_at=max(item.observed_through for item in terminals) if generated_at is None else generated_at,
        session_ids=tuple(item.session_id for item in terminals),
        fixture_labels=tuple(sorted({item.fixture_label for item in terminals})),
        source_artifact_hashes=source_hashes,
    )


def _load_terminals(request: UsDayAcceptanceBuildRequest) -> tuple[UsDaySessionTerminal, ...]:
    terminals: list[UsDaySessionTerminal] = []
    try:
        for relative in request.terminal_paths:
            terminals.append(
                UsDaySessionTerminal.model_validate_json((request.repository / relative).read_text(encoding="utf-8"))
            )
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidUsDayAcceptanceEvidenceError from None
    return _validated_terminals(tuple(terminals))


def _verify_sources(repository: Path, terminals: tuple[UsDaySessionTerminal, ...]) -> None:
    for terminal in terminals:
        for source in terminal.source_artifacts:
            if acceptance_artifact_sha256(repository, source.path) != source.sha256:
                raise InvalidUsDayAcceptanceEvidenceError


def _natural_evidence(
    report: UsDayThreeSessionReport,
    terminals: tuple[UsDaySessionTerminal, ...],
) -> UsDayNaturalPaperLifecycleEvidence:
    qualifying = tuple(item.session_id for item in terminals if item.has_natural_lifecycle)
    return UsDayNaturalPaperLifecycleEvidence(
        commit_sha=report.commit_sha,
        generated_at=report.generated_at,
        session_ids=report.session_ids,
        fixture_labels=report.fixture_labels,
        source_artifact_hashes=report.source_artifact_hashes,
        passed=report.natural_paper_lifecycle_passed,
        qualifying_session_ids=qualifying,
    )


def _reconciliation_evidence(
    report: UsDayThreeSessionReport,
    terminals: tuple[UsDaySessionTerminal, ...],
) -> UsDayFinalReconciliationEvidence:
    failed = tuple(item.session_id for item in terminals if not item.is_finally_reconciled)
    return UsDayFinalReconciliationEvidence(
        commit_sha=report.commit_sha,
        generated_at=report.generated_at,
        session_ids=report.session_ids,
        fixture_labels=report.fixture_labels,
        source_artifact_hashes=report.source_artifact_hashes,
        passed=report.final_reconciliation_passed,
        failed_session_ids=failed,
    )


def _receipt_evidence(
    report: UsDayThreeSessionReport,
    terminals: tuple[UsDaySessionTerminal, ...],
) -> UsDayHermesOutcomeReceiptEvidence:
    return UsDayHermesOutcomeReceiptEvidence(
        commit_sha=report.commit_sha,
        generated_at=report.generated_at,
        session_ids=report.session_ids,
        fixture_labels=report.fixture_labels,
        source_artifact_hashes=report.source_artifact_hashes,
        passed=report.delivery_subgate_passed,
        delivery_ids=tuple(item.outcome_delivery_id for item in terminals),
    )

@dataclass(frozen=True, slots=True)
class _ReportPaths:
    three_session_report_path: Path
    natural_paper_lifecycle_path: Path
    final_reconciliation_path: Path
    hermes_outcome_receipt_path: Path
    manifest_path: Path

    @property
    def artifact_paths(self) -> tuple[Path, ...]:
        return (
            self.three_session_report_path,
            self.natural_paper_lifecycle_path,
            self.final_reconciliation_path,
            self.hermes_outcome_receipt_path,
        )


def _report_paths(output_root: Path) -> _ReportPaths:
    us_day = output_root / "us_day"
    return _ReportPaths(
        us_day / "three_session_report.json",
        us_day / "natural_paper_lifecycle.json",
        us_day / "final_reconciliation.json",
        us_day / "hermes_outcome_receipt.json",
        output_root / "day/manifest.json",
    )


def _write(path: Path, model: UsDayEvidenceEnvelope) -> None:
    write_private_stable_report(path, model.model_dump_json(indent=2) + "\n")
