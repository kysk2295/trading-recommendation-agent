from __future__ import annotations

import datetime as dt
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.acceptance_evidence import (
    AcceptanceEvidenceBuildRequest,
    AcceptanceEvidenceManifest,
    AcceptanceSessionEvidence,
    acceptance_artifact_sha256,
    build_acceptance_manifest,
    require_clean_repository_commit,
    verify_acceptance_manifest,
)
from trading_agent.hermes_acceptance_gate import (
    HermesAcceptanceAssessment,
    HermesAcceptanceGateStatus,
    HermesAcceptanceSessionEvidence,
    assess_hermes_acceptance,
)
from trading_agent.hermes_query_service import HermesQueryAgentFamily
from trading_agent.kis_kr_session_calendar import next_kr_open_session
from trading_agent.kis_kr_session_calendar_models import KrSessionCalendarSnapshot
from trading_agent.private_query_file import InvalidPrivateQueryFileError, read_private_text_query_only
from trading_agent.private_stable_report import InvalidPrivateStableReportError, write_private_stable_report

HERMES_ACCEPTANCE_POLICY_VERSION = "hermes-aggregate-acceptance-v1"
HERMES_ACCEPTANCE_VERIFIER_VERSION = "hermes-aggregate-acceptance-v1"
_PLUGIN_PATH = Path("outputs/acceptance/hermes/plugin_installation.json")
_QUERY_PATH = Path("outputs/acceptance/hermes/query_and_alert_qa.md")
_RESTART_PATH = Path("outputs/acceptance/soak/restart_and_provider_fault_reconciliation.json")
_REPORT_PATH = Path("outputs/acceptance/hermes/delivery_reconciliation.json")
_US_REPORT_PATH = Path("outputs/acceptance/soak/us_five_session_report.json")
_KR_REPORT_PATH = Path("outputs/acceptance/soak/kr_five_session_report.json")
_PLUGIN_MANIFEST_PATH = Path("integrations/hermes/trading-agent/plugin.yaml")
_RUNTIME_TOOLS = (
    "trading_agent_query",
    "trading_agent_status",
    "trading_agent_arm_prepare",
    "trading_agent_arm_confirm",
    "trading_agent_arm_revoke",
)


class InvalidHermesAcceptanceBuildError(ValueError):
    @override
    def __str__(self) -> str:
        return "Hermes aggregate acceptance evidence is invalid"


class HermesAcceptanceBuildRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    generated_at: AwareDatetime
    sessions: tuple[HermesAcceptanceSessionEvidence, ...]
    plugin_installation_path: Path
    query_and_alert_qa_path: Path
    restart_and_provider_fault_path: Path

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        paths = self.artifact_paths
        if (
            self.plugin_installation_path != _PLUGIN_PATH
            or self.query_and_alert_qa_path != _QUERY_PATH
            or self.restart_and_provider_fault_path != _RESTART_PATH
            or any(path.is_absolute() or ".." in path.parts for path in paths)
            or len(set(paths)) != len(paths)
        ):
            raise InvalidHermesAcceptanceBuildError
        return self

    @property
    def artifact_paths(self) -> tuple[Path, ...]:
        return (
            *(session.reconciliation_artifact_path for session in self.sessions),
            self.plugin_installation_path,
            self.query_and_alert_qa_path,
            self.restart_and_provider_fault_path,
        )


class HermesAcceptanceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    policy_version: Literal["hermes-aggregate-acceptance-v1"] = HERMES_ACCEPTANCE_POLICY_VERSION
    generated_at: AwareDatetime
    sessions: tuple[HermesAcceptanceSessionEvidence, ...]
    plugin_installation_path: Path
    query_and_alert_qa_path: Path
    restart_and_provider_fault_path: Path
    assessment: HermesAcceptanceAssessment


class HermesAcceptanceBuildResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    report: HermesAcceptanceReport
    manifest: AcceptanceEvidenceManifest | None


class HermesPluginInstallationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    enabled: Literal[True]
    observed_at: AwareDatetime
    profile: Literal["stockagent"]
    runtime_tools: tuple[str, ...]
    installed_version: str
    plugin_manifest_path: Path
    plugin_manifest_sha256: str
    source_commit_sha: str

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if (
            self.runtime_tools != _RUNTIME_TOOLS
            or self.plugin_manifest_path != _PLUGIN_MANIFEST_PATH
            or re.fullmatch(r"[0-9a-f]{64}", self.plugin_manifest_sha256) is None
            or re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha) is None
        ):
            raise InvalidHermesAcceptanceBuildError
        return self


def build_hermes_acceptance_evidence(
    request: HermesAcceptanceBuildRequest,
    repository: Path,
    report_output: Path,
    manifest_output: Path,
) -> HermesAcceptanceBuildResult:
    _require_canonical_output(repository, report_output, _REPORT_PATH)
    _require_canonical_output(repository, manifest_output, Path("outputs/acceptance/hermes/manifest.json"))
    if not repository.is_dir():
        raise InvalidHermesAcceptanceBuildError
    parsed_sessions = _parsed_sessions(request, repository)
    _verify_plugin(repository, request.generated_at)
    _verify_restart(repository)
    _verify_query(repository)
    report = _report(request, parsed_sessions)
    try:
        write_private_stable_report(report_output, report.model_dump_json(indent=2) + "\n")
    except InvalidPrivateStableReportError:
        raise InvalidHermesAcceptanceBuildError from None
    if report.assessment.status is not HermesAcceptanceGateStatus.PASSED:
        return HermesAcceptanceBuildResult(report=report, manifest=None)
    _write_market_report(repository, _US_REPORT_PATH, "us_equities", parsed_sessions)
    _write_market_report(repository, _KR_REPORT_PATH, "kr_equities", parsed_sessions)
    manifest = build_acceptance_manifest(
        AcceptanceEvidenceBuildRequest(
            criterion_id="AC-001",
            policy_version=HERMES_ACCEPTANCE_POLICY_VERSION,
            verifier_version=HERMES_ACCEPTANCE_VERIFIER_VERSION,
            generated_at=request.generated_at,
            sessions=_acceptance_sessions(parsed_sessions),
            artifact_paths=(_REPORT_PATH, _PLUGIN_PATH, _QUERY_PATH, _RESTART_PATH, _US_REPORT_PATH, _KR_REPORT_PATH),
        ),
        repository,
        manifest_output,
    )
    return HermesAcceptanceBuildResult(report=report, manifest=manifest)


def verify_hermes_acceptance_evidence(
    report_path: Path,
    manifest_path: Path,
    repository: Path,
) -> HermesAcceptanceReport:
    _require_canonical_output(repository, report_path, _REPORT_PATH)
    _require_canonical_output(repository, manifest_path, Path("outputs/acceptance/hermes/manifest.json"))
    report = _read_report(report_path)
    if report.assessment != assess_hermes_acceptance(report.sessions):
        raise InvalidHermesAcceptanceBuildError
    if report.assessment.status is not HermesAcceptanceGateStatus.PASSED:
        raise InvalidHermesAcceptanceBuildError
    parsed_sessions = _parsed_sessions_from_report(report, repository)
    _verify_plugin(repository, report.generated_at)
    _verify_restart(repository)
    _verify_query(repository)
    _verify_market_report(repository, _US_REPORT_PATH, "us_equities", parsed_sessions)
    _verify_market_report(repository, _KR_REPORT_PATH, "kr_equities", parsed_sessions)
    manifest = _read_manifest(manifest_path)
    if (
        manifest.criterion_id != "AC-001"
        or manifest.policy_version != HERMES_ACCEPTANCE_POLICY_VERSION
        or manifest.verifier_version != HERMES_ACCEPTANCE_VERIFIER_VERSION
        or manifest.generated_at != report.generated_at
        or manifest.fixture_labels
        or manifest.source_artifact_hashes
        or manifest.sessions != _acceptance_sessions(parsed_sessions)
    ):
        raise InvalidHermesAcceptanceBuildError
    expected_paths = {_REPORT_PATH, _PLUGIN_PATH, _QUERY_PATH, _RESTART_PATH, _US_REPORT_PATH, _KR_REPORT_PATH}
    if {artifact.path for artifact in manifest.artifacts} != expected_paths:
        raise InvalidHermesAcceptanceBuildError
    verify_acceptance_manifest(manifest, repository, require_clean_commit=True, require_session_binding=True)
    return report


def _report(
    request: HermesAcceptanceBuildRequest,
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> HermesAcceptanceReport:
    return HermesAcceptanceReport(
        generated_at=request.generated_at,
        sessions=sessions,
        plugin_installation_path=request.plugin_installation_path,
        query_and_alert_qa_path=request.query_and_alert_qa_path,
        restart_and_provider_fault_path=request.restart_and_provider_fault_path,
        assessment=assess_hermes_acceptance(request.sessions),
    )


def _acceptance_sessions(
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> tuple[AcceptanceSessionEvidence, ...]:
    return tuple(
        AcceptanceSessionEvidence(
            session_id=session.session_id,
            market_id=session.market_id,
            kind=session.kind,
            observed_from=session.observed_from,
            observed_through=session.observed_through,
        )
        for session in sessions
    )


def _parsed_sessions(
    request: HermesAcceptanceBuildRequest,
    repository: Path,
) -> tuple[HermesAcceptanceSessionEvidence, ...]:
    return _parsed_sessions_for_expected(request.sessions, repository)


def _parsed_sessions_from_report(
    report: HermesAcceptanceReport,
    repository: Path,
) -> tuple[HermesAcceptanceSessionEvidence, ...]:
    if (
        report.plugin_installation_path != _PLUGIN_PATH
        or report.query_and_alert_qa_path != _QUERY_PATH
        or report.restart_and_provider_fault_path != _RESTART_PATH
    ):
        raise InvalidHermesAcceptanceBuildError
    return _parsed_sessions_for_expected(report.sessions, repository)


def _parsed_sessions_for_expected(
    expected: tuple[HermesAcceptanceSessionEvidence, ...],
    repository: Path,
) -> tuple[HermesAcceptanceSessionEvidence, ...]:
    parsed = tuple(_read_session(repository / item.reconciliation_artifact_path) for item in expected)
    if parsed != expected:
        raise InvalidHermesAcceptanceBuildError
    _verify_kr_calendar_chain(parsed, repository)
    return parsed


def _verify_kr_calendar_chain(sessions: tuple[HermesAcceptanceSessionEvidence, ...], repository: Path) -> None:
    kr = tuple(sorted((item for item in sessions if item.market_id == "kr_equities"), key=lambda item: item.session_id))
    snapshots: dict[Path, KrSessionCalendarSnapshot] = {}
    for session in kr:
        if session.kr_calendar_snapshot_path is None:
            raise InvalidHermesAcceptanceBuildError
        snapshot = _read_kr_calendar(repository, session.kr_calendar_snapshot_path)
        if not any(
            day.session_date == dt.date.fromisoformat(session.session_id[5:])
            and day.business_day
            and day.trading_day
            and day.open_day
            for day in snapshot.payload.days
        ):
            raise InvalidHermesAcceptanceBuildError
        snapshots[session.kr_calendar_snapshot_path] = snapshot
    for previous, current in pairwise(kr):
        if previous.kr_calendar_snapshot_path is None:
            raise InvalidHermesAcceptanceBuildError
        try:
            snapshot = snapshots[previous.kr_calendar_snapshot_path]
            expected = next_kr_open_session(snapshot, dt.date.fromisoformat(previous.session_id[5:]))
        except (KeyError, ValueError):
            raise InvalidHermesAcceptanceBuildError from None
        if current.session_id != f"XKRX-{expected.isoformat()}":
            raise InvalidHermesAcceptanceBuildError


def _read_kr_calendar(repository: Path, path: Path) -> KrSessionCalendarSnapshot:
    try:
        return KrSessionCalendarSnapshot.model_validate_json(read_private_text_query_only(repository / path))
    except (InvalidPrivateQueryFileError, ValidationError, ValueError):
        raise InvalidHermesAcceptanceBuildError from None


def _read_session(path: Path) -> HermesAcceptanceSessionEvidence:
    try:
        return HermesAcceptanceSessionEvidence.model_validate_json(read_private_text_query_only(path))
    except (InvalidPrivateQueryFileError, ValidationError, ValueError):
        raise InvalidHermesAcceptanceBuildError from None


def _read_json(repository: Path, path: Path) -> dict[str, object]:
    try:
        payload = json.loads(read_private_text_query_only(repository / path))
    except (InvalidPrivateQueryFileError, json.JSONDecodeError):
        raise InvalidHermesAcceptanceBuildError from None
    if not isinstance(payload, dict):
        raise InvalidHermesAcceptanceBuildError
    return payload


def _verify_plugin(repository: Path, generated_at: dt.datetime) -> None:
    try:
        receipt = HermesPluginInstallationReceipt.model_validate_json(
            read_private_text_query_only(repository / _PLUGIN_PATH)
        )
        manifest = (repository / _PLUGIN_MANIFEST_PATH).read_text(encoding="utf-8")
        source_commit = require_clean_repository_commit(repository)
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidHermesAcceptanceBuildError from None
    name = re.search(r"^name:\s*([^\s#]+)\s*$", manifest, flags=re.MULTILINE)
    version = re.search(r"^version:\s*([^\s#]+)\s*$", manifest, flags=re.MULTILINE)
    kind = re.search(r"^kind:\s*([^\s#]+)\s*$", manifest, flags=re.MULTILINE)
    tools = tuple(re.findall(r"^\s{2}-\s+(trading_agent_[a-z_]+)\s*$", manifest, flags=re.MULTILINE))
    if (
        name is None
        or version is None
        or kind is None
        or name.group(1) != "trading-agent"
        or kind.group(1) != "standalone"
        or tools != _RUNTIME_TOOLS
        or receipt.installed_version != version.group(1)
        or receipt.plugin_manifest_sha256 != acceptance_artifact_sha256(repository, _PLUGIN_MANIFEST_PATH)
        or receipt.source_commit_sha != source_commit
        or receipt.observed_at > generated_at
    ):
        raise InvalidHermesAcceptanceBuildError


def _verify_restart(repository: Path) -> None:
    payload = _read_json(repository, _RESTART_PATH)
    restart = payload.get("restart")
    provider = payload.get("provider_incident")
    if (
        payload.get("controlled_fixture") is not True
        or payload.get("real_session") is not False
        or payload.get("scenario") != "controlled_fixture"
        or not isinstance(restart, dict)
        or not isinstance(provider, dict)
        or restart.get("duplicate_count") != 0
        or restart.get("omission_count") != 0
        or restart.get("unaccounted_count") != 0
        or restart.get("reply_lineage_verified") is not True
        or restart.get("same_delivery_identity") is not True
        or restart.get("store_reopened") is not True
        or restart.get("retry_after_expired_claim") is not True
        or not isinstance(restart.get("suppression_terminal_count"), int)
        or restart["suppression_terminal_count"] < 1
        or restart.get("acknowledged_or_terminal_count", 0) < 1
        or provider
        != {
            "fixture": "controlled_fixture",
            "kind": "read_only_provider_outage",
            "network_calls": 0,
            "provider_mutation_count": 0,
            "terminal": True,
        }
    ):
        raise InvalidHermesAcceptanceBuildError


def _verify_query(repository: Path) -> None:
    try:
        text = read_private_text_query_only(repository / _QUERY_PATH)
    except InvalidPrivateQueryFileError:
        raise InvalidHermesAcceptanceBuildError from None
    required = (
        "# Hermes query and alert QA",
        "- separate family count: 6",
        "- blended verdict: none",
        "- outbound summary leak count: 0",
        "- generated report leak count: 0",
    )
    family = re.search(r"^- family names: (.+)$", text, flags=re.MULTILINE)
    execution = re.search(r"^- execution aggregate counts: [0-9]+/[0-9]+/0/0$", text, flags=re.MULTILINE)
    expected_families = {item.value for item in HermesQueryAgentFamily}
    if family is None or execution is None or not all(line in text for line in required):
        raise InvalidHermesAcceptanceBuildError
    if {value.strip() for value in family.group(1).split(",")} != expected_families:
        raise InvalidHermesAcceptanceBuildError


def _write_market_report(
    repository: Path,
    path: Path,
    market_id: str,
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> None:
    try:
        write_private_stable_report(
            repository / path,
            json.dumps(_market_report_payload(repository, market_id, sessions), sort_keys=True) + "\n",
        )
    except InvalidPrivateStableReportError:
        raise InvalidHermesAcceptanceBuildError from None


def _verify_market_report(
    repository: Path,
    path: Path,
    market_id: str,
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> None:
    if _read_json(repository, path) != _market_report_payload(repository, market_id, sessions):
        raise InvalidHermesAcceptanceBuildError


def _market_report_payload(
    repository: Path,
    market_id: str,
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> dict[str, object]:
    selected = tuple(item for item in sessions if item.market_id == market_id)
    if len(selected) != 5 or any(item.kind.value != "real" for item in selected):
        raise InvalidHermesAcceptanceBuildError
    reconciliations = tuple(
        {
            "path": str(item.reconciliation_artifact_path),
            "sha256": acceptance_artifact_sha256(repository, item.reconciliation_artifact_path),
        }
        for item in selected
    )
    calendars = tuple(
        {
            "path": str(path),
            "sha256": acceptance_artifact_sha256(repository, path),
        }
        for path in sorted(
            {
                item.kr_calendar_snapshot_path
                for item in selected
                if item.kr_calendar_snapshot_path is not None
            }
        )
    )
    if market_id == "kr_equities" and not calendars:
        raise InvalidHermesAcceptanceBuildError
    if market_id == "us_equities" and calendars:
        raise InvalidHermesAcceptanceBuildError
    return {
        "calendar_artifacts": list(calendars),
        "market_id": market_id,
        "real_session_count": 5,
        "reconciliation_artifacts": list(reconciliations),
        "session_ids": [item.session_id for item in selected],
    }


def _require_canonical_output(repository: Path, path: Path, expected: Path) -> None:
    try:
        relative = path.resolve(strict=False).relative_to(repository.resolve(strict=True))
    except (OSError, ValueError):
        raise InvalidHermesAcceptanceBuildError from None
    if relative != expected:
        raise InvalidHermesAcceptanceBuildError


def _read_report(path: Path) -> HermesAcceptanceReport:
    try:
        return HermesAcceptanceReport.model_validate_json(read_private_text_query_only(path))
    except (InvalidPrivateQueryFileError, ValidationError, ValueError):
        raise InvalidHermesAcceptanceBuildError from None


def _read_manifest(path: Path) -> AcceptanceEvidenceManifest:
    try:
        return AcceptanceEvidenceManifest.model_validate_json(read_private_text_query_only(path))
    except (InvalidPrivateQueryFileError, ValidationError, ValueError):
        raise InvalidHermesAcceptanceBuildError from None
