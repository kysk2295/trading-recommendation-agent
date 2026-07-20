from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Self, assert_never, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.us_news_catalyst_collection_artifact import (
    collection_plan_path,
    collection_receipt_path,
    load_us_news_catalyst_collection_plan,
    load_us_news_catalyst_collection_receipt,
)
from trading_agent.us_news_catalyst_day_session_audit import UsNewsCatalystDaySessionPhase
from trading_agent.us_news_catalyst_day_session_manifest import UsNewsCatalystDaySessionManifest
from trading_agent.us_news_catalyst_feature_artifact import feature_artifacts_in
from trading_agent.us_news_catalyst_reviewer_artifact import reviews_in
from trading_agent.us_news_catalyst_trial_artifact import cohorts_in, outcomes_in, setup_manifests_in
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystCohortArtifact, UsNewsCatalystCohortStatus
from trading_agent.us_news_catalyst_trial_outcome_models import US_NEWS_CATALYST_EVALUATOR_VERSION

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HORIZON = dt.timedelta(minutes=30)
_MAX_DELAY = dt.timedelta(minutes=2)


class InvalidUsNewsCatalystDaySessionEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day session evidence is invalid"


class UsNewsCatalystDaySessionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: UsNewsCatalystDaySessionPhase
    evidence_sha256: str
    skipped_reason: str | None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if _HEX64.fullmatch(self.evidence_sha256) is None or (
            self.skipped_reason is not None and not _canonical_text(self.skipped_reason)
        ):
            raise InvalidUsNewsCatalystDaySessionEvidenceError
        return self


def resolve_us_news_catalyst_day_session_evidence(
    manifest: UsNewsCatalystDaySessionManifest,
    phase: UsNewsCatalystDaySessionPhase,
    observed_at: dt.datetime,
) -> UsNewsCatalystDaySessionEvidence | None:
    try:
        if not _aware(observed_at):
            raise InvalidUsNewsCatalystDaySessionEvidenceError
        match phase:
            case UsNewsCatalystDaySessionPhase.REGISTER:
                return _registration(manifest)
            case UsNewsCatalystDaySessionPhase.START:
                return _start(manifest)
            case UsNewsCatalystDaySessionPhase.COLLECT:
                return _collect(manifest, observed_at)
            case UsNewsCatalystDaySessionPhase.OBSERVE:
                return _observe(manifest, observed_at)
            case UsNewsCatalystDaySessionPhase.FINALIZE:
                return _finalize(manifest)
            case UsNewsCatalystDaySessionPhase.REVIEW:
                return _review(manifest)
            case unreachable:
                assert_never(unreachable)
    except InvalidUsNewsCatalystDaySessionEvidenceError:
        raise
    except (AttributeError, OSError, TypeError, ValueError):
        raise InvalidUsNewsCatalystDaySessionEvidenceError from None


def _registration(manifest: UsNewsCatalystDaySessionManifest) -> UsNewsCatalystDaySessionEvidence | None:
    if not manifest.paths.experiment_ledger.exists():
        return None
    matches = tuple(
        item.registration
        for item in ExperimentLedgerStore(manifest.paths.experiment_ledger).multi_market_trials()
        if item.registration.trial_id == manifest.trial_id
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    item = matches[0]
    if (
        item.strategy_version != manifest.strategy_version
        or item.planned_start != manifest.session_date
        or item.planned_end != manifest.session_date
        or item.evaluator_version != US_NEWS_CATALYST_EVALUATOR_VERSION
    ):
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    return _evidence(UsNewsCatalystDaySessionPhase.REGISTER, canonical_experiment_ledger_json(item))


def _start(manifest: UsNewsCatalystDaySessionManifest) -> UsNewsCatalystDaySessionEvidence | None:
    if _registration(manifest) is None:
        return None
    ledger = ExperimentLedgerStore(manifest.paths.experiment_ledger)
    events = ledger.multi_market_trial_events(manifest.trial_id)
    cohort = _cohort(manifest)
    if not events:
        return None
    started = next(iter(events))
    if len(events) > 2 or started.event.event_kind is not TrialEventKind.STARTED or cohort is None:
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    return _evidence(
        UsNewsCatalystDaySessionPhase.START,
        canonical_experiment_ledger_json(started.event),
        cohort.artifact_id,
    )


def _collect(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
) -> UsNewsCatalystDaySessionEvidence | None:
    cohort = _cohort(manifest)
    if cohort is None or _start(manifest) is None:
        return None
    if cohort.payload.status is UsNewsCatalystCohortStatus.INSUFFICIENT_CONTROL:
        return _skipped(UsNewsCatalystDaySessionPhase.COLLECT, cohort, "insufficient_control")
    plan_path = collection_plan_path(manifest.paths.plan_root, cohort.artifact_id)
    receipt_path = collection_receipt_path(manifest.paths.receipt_root, cohort.artifact_id)
    if not receipt_path.exists():
        if observed_at > cohort.payload.observed_at + _HORIZON + _MAX_DELAY:
            return _skipped(UsNewsCatalystDaySessionPhase.COLLECT, cohort, "collection_window_missed")
        return None
    if not plan_path.exists():
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    plan = load_us_news_catalyst_collection_plan(plan_path)
    receipt = load_us_news_catalyst_collection_receipt(receipt_path)
    expected = tuple(sorted((*cohort.payload.treatment_symbols, *cohort.payload.control_symbols)))
    actual = tuple(item.symbol for item in receipt.content.features)
    artifacts = {item.artifact_id: item for item in feature_artifacts_in(manifest.paths.feature_root)}
    if (
        plan.content.cohort_artifact_id != cohort.artifact_id
        or receipt.content.plan_id != plan.plan_id
        or receipt.content.cohort_artifact_id != cohort.artifact_id
        or actual != expected
        or any(
            item.artifact_id not in artifacts or artifacts[item.artifact_id].payload.symbol != item.symbol
            for item in receipt.content.features
        )
    ):
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    return _evidence(UsNewsCatalystDaySessionPhase.COLLECT, plan.plan_id, receipt.receipt_id)


def _observe(
    manifest: UsNewsCatalystDaySessionManifest,
    observed_at: dt.datetime,
) -> UsNewsCatalystDaySessionEvidence | None:
    cohort = _cohort(manifest)
    if cohort is None or _start(manifest) is None:
        return None
    if cohort.payload.status is UsNewsCatalystCohortStatus.INSUFFICIENT_CONTROL:
        return _skipped(UsNewsCatalystDaySessionPhase.OBSERVE, cohort, "insufficient_control")
    matches = tuple(
        item
        for item in setup_manifests_in(manifest.paths.artifact_root)
        if item.trial_id == manifest.trial_id
    )
    if not matches:
        if observed_at > cohort.payload.observed_at + _HORIZON + _MAX_DELAY:
            return _skipped(UsNewsCatalystDaySessionPhase.OBSERVE, cohort, "observation_window_missed")
        return None
    if len(matches) != 1 or matches[0].cohort_artifact_id != cohort.artifact_id:
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    return _evidence(UsNewsCatalystDaySessionPhase.OBSERVE, matches[0].manifest_id)


def _finalize(manifest: UsNewsCatalystDaySessionManifest) -> UsNewsCatalystDaySessionEvidence | None:
    outcomes = tuple(
        item
        for item in outcomes_in(manifest.paths.artifact_root)
        if item.payload.trial_id == manifest.trial_id
    )
    if not outcomes:
        return None
    events = ExperimentLedgerStore(manifest.paths.experiment_ledger).multi_market_trial_events(manifest.trial_id)
    if len(outcomes) != 1 or len(events) != 2 or events[1].event.event_kind not in {
        TrialEventKind.COMPLETED, TrialEventKind.CENSORED, TrialEventKind.FAILED,
    }:
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    outcome = outcomes[0]
    if outcome.artifact_id not in events[1].event.artifact_sha256s:
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    return _evidence(
        UsNewsCatalystDaySessionPhase.FINALIZE,
        canonical_experiment_ledger_json(events[1].event),
        outcome.artifact_id,
    )


def _review(manifest: UsNewsCatalystDaySessionManifest) -> UsNewsCatalystDaySessionEvidence | None:
    matches = tuple(
        item for item in reviews_in(manifest.paths.review_root)
        if item.payload.strategy_version == manifest.strategy_version
        and item.payload.as_of_session == manifest.session_date
    )
    if not matches:
        return None
    if len(matches) != 1 or manifest.trial_id not in matches[0].payload.included_trial_ids:
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    return _evidence(UsNewsCatalystDaySessionPhase.REVIEW, matches[0].artifact_id)


def _cohort(manifest: UsNewsCatalystDaySessionManifest) -> UsNewsCatalystCohortArtifact | None:
    matches = tuple(
        item for item in cohorts_in(manifest.paths.artifact_root)
        if item.payload.trial_id == manifest.trial_id
    )
    if len(matches) > 1:
        raise InvalidUsNewsCatalystDaySessionEvidenceError
    return None if not matches else matches[0]


def _skipped(
    phase: UsNewsCatalystDaySessionPhase,
    cohort: UsNewsCatalystCohortArtifact,
    reason: str,
) -> UsNewsCatalystDaySessionEvidence:
    return UsNewsCatalystDaySessionEvidence(
        phase=phase,
        evidence_sha256=_fingerprint((phase.value, cohort.artifact_id, reason)),
        skipped_reason=reason,
    )


def _evidence(phase: UsNewsCatalystDaySessionPhase, *values: str) -> UsNewsCatalystDaySessionEvidence:
    return UsNewsCatalystDaySessionEvidence(
        phase=phase,
        evidence_sha256=_fingerprint((phase.value, *values)),
        skipped_reason=None,
    )


def _fingerprint(values: tuple[str, ...]) -> str:
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(char in value for char in "\r\n\t")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidUsNewsCatalystDaySessionEvidenceError",
    "UsNewsCatalystDaySessionEvidence",
    "resolve_us_news_catalyst_day_session_evidence",
)
