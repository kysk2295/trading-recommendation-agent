from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import override

from trading_agent.experiment_ledger_models import ExperimentTrialEvent
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.us_news_catalyst_trial_models import UsNewsCatalystCohortArtifact
from trading_agent.us_news_catalyst_trial_outcome_models import UsNewsCatalystTrialOutcomeArtifact


class InvalidUsNewsCatalystTrialError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst shadow trial is invalid"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystTrialRegistrationResult:
    created: bool
    registration: MultiMarketExperimentTrialRegistration


@dataclass(frozen=True, slots=True)
class UsNewsCatalystTrialStartResult:
    cohort_created: bool
    event_created: bool
    cohort: UsNewsCatalystCohortArtifact
    event: ExperimentTrialEvent


@dataclass(frozen=True, slots=True)
class UsNewsCatalystTrialFinalizeResult:
    outcome_created: bool
    event_created: bool
    outcome: UsNewsCatalystTrialOutcomeArtifact
    event: ExperimentTrialEvent


def us_news_catalyst_trial_id(strategy_version: str, session_date: dt.date) -> str:
    digest = hashlib.sha256(f"{strategy_version}|{session_date.isoformat()}".encode()).hexdigest()[:16]
    return f"us-news-catalyst-{session_date:%Y%m%d}-{digest}"


def require_us_news_catalyst_trial(
    ledger: ExperimentLedgerReader,
    trial_id: str,
    evaluator_version: str,
) -> MultiMarketExperimentTrialRegistration:
    trial = us_news_catalyst_trial_or_none(ledger, trial_id)
    if trial is None or trial.evaluator_version != evaluator_version:
        raise InvalidUsNewsCatalystTrialError
    return trial


def us_news_catalyst_trial_or_none(
    ledger: ExperimentLedgerReader,
    trial_id: str,
) -> MultiMarketExperimentTrialRegistration | None:
    matches = tuple(
        item.registration for item in ledger.multi_market_trials() if item.registration.trial_id == trial_id
    )
    if len(matches) > 1:
        raise InvalidUsNewsCatalystTrialError
    return None if not matches else matches[0]


__all__ = (
    "InvalidUsNewsCatalystTrialError",
    "UsNewsCatalystTrialFinalizeResult",
    "UsNewsCatalystTrialRegistrationResult",
    "UsNewsCatalystTrialStartResult",
    "require_us_news_catalyst_trial",
    "us_news_catalyst_trial_id",
    "us_news_catalyst_trial_or_none",
)
