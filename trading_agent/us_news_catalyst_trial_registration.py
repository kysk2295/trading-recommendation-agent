from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Final

from pydantic import ValidationError

from trading_agent.experiment_ledger_models import TrialKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.multi_market_experiment_keys import multi_market_strategy_version_registration_key
from trading_agent.multi_market_experiment_models import MultiMarketStrategyVersionRegistration
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_news_catalyst_research_registration import (
    UsNewsCatalystProjectionAuthorityRequest,
    require_registered_us_news_catalyst_strategy,
)
from trading_agent.us_news_catalyst_trial_contract import (
    InvalidUsNewsCatalystTrialError,
    UsNewsCatalystTrialRegistrationResult,
    us_news_catalyst_trial_id,
    us_news_catalyst_trial_or_none,
)
from trading_agent.us_news_catalyst_trial_models import (
    InvalidUsNewsCatalystTrialModelError,
    UsNewsCatalystDailyTrialRegistrationRequest,
)
from trading_agent.us_news_catalyst_trial_outcome_models import US_NEWS_CATALYST_EVALUATOR_VERSION

_FEED_ENTITLEMENT: Final = "alpaca_news_bounded_plus_canonical_intraday_features"
_EVIDENCE_BUDGET: Final = tuple(
    sorted(
        (
            "cohort_artifact:1",
            "outcome_artifact:1",
            "setup_observation:all_cohort_symbols",
            "zero_news_equal_count_v1",
        )
    )
)


def register_us_news_catalyst_daily_trial(
    ledger: ExperimentLedgerStore,
    request: UsNewsCatalystDailyTrialRegistrationRequest,
) -> UsNewsCatalystTrialRegistrationResult:
    try:
        checked = UsNewsCatalystDailyTrialRegistrationRequest.model_validate(request.model_dump())
        bounds = regular_session_bounds(checked.session_date)
        if bounds is None:
            raise InvalidUsNewsCatalystTrialError
        version = require_registered_us_news_catalyst_strategy(
            ledger,
            UsNewsCatalystProjectionAuthorityRequest(
                strategy_version=checked.strategy_version,
                code_version=checked.code_version,
                projected_at=checked.registered_at,
            ),
        )
        hypothesis = next(
            item.registration
            for item in ledger.multi_market_hypotheses()
            if item.registration.hypothesis_id == version.hypothesis_id
        )
        trial_id = us_news_catalyst_trial_id(checked.strategy_version, checked.session_date)
        existing = us_news_catalyst_trial_or_none(ledger, trial_id)
        registration = MultiMarketExperimentTrialRegistration(
            trial_id=trial_id,
            strategy_version=checked.strategy_version,
            trial_kind=TrialKind.SHADOW_FORWARD,
            experiment_scope=hypothesis.experiment_scope,
            experiment_scope_key=hypothesis.experiment_scope_key,
            strategy_lane=version.strategy_lane,
            evaluator_version=US_NEWS_CATALYST_EVALUATOR_VERSION,
            data_version=_data_version(version, checked.session_date),
            feed_entitlement=_FEED_ENTITLEMENT,
            planned_start=checked.session_date,
            planned_end=checked.session_date,
            registered_at=checked.registered_at if existing is None else existing.registered_at,
            evidence_budget=_EVIDENCE_BUDGET,
        )
        if existing is not None:
            if existing != registration:
                raise InvalidUsNewsCatalystTrialError
            return UsNewsCatalystTrialRegistrationResult(False, existing)
        if checked.registered_at >= bounds[0]:
            raise InvalidUsNewsCatalystTrialError
        with ledger.writer() as writer:
            created = writer.register_multi_market_trial(registration)
        return UsNewsCatalystTrialRegistrationResult(created, registration)
    except (
        AttributeError,
        InvalidUsNewsCatalystTrialModelError,
        StopIteration,
        ValidationError,
        ValueError,
    ):
        raise InvalidUsNewsCatalystTrialError from None


def _data_version(version: MultiMarketStrategyVersionRegistration, session_date: dt.date) -> str:
    payload = (
        str(multi_market_strategy_version_registration_key(version)),
        session_date.isoformat(),
        US_NEWS_CATALYST_EVALUATOR_VERSION,
        *_EVIDENCE_BUDGET,
    )
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


__all__ = ("register_us_news_catalyst_daily_trial",)
