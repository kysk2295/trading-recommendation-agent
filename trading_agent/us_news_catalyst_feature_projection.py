from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final, override

from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus, IntradayFeatureSnapshot
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_feature_evidence_models import UsFeatureEvidenceBinding
from trading_agent.us_intraday_volume_profile_models import (
    IntradayVolumeProfileError,
    validate_intraday_volume_profile,
)
from trading_agent.us_news_catalyst_feature_models import (
    InvalidUsNewsCatalystFeatureModelError,
    UsNewsCatalystFeatureArtifact,
    UsNewsCatalystFeaturePayload,
    feature_artifact,
)
from trading_agent.us_news_catalyst_trial_models import (
    UsNewsCatalystCohortArtifact,
    UsNewsCatalystCohortStatus,
)
from trading_agent.us_news_catalyst_trial_outcome_models import (
    US_NEWS_CATALYST_EVALUATOR_VERSION,
    US_NEWS_CATALYST_SETUP_HORIZON,
    UsNewsCatalystSetupFeatureObservation,
    UsNewsCatalystSetupObservationManifest,
    create_us_news_catalyst_setup_observation_manifest,
)

_MAX_FEATURE_AGE: Final = dt.timedelta(minutes=2)


class InvalidUsNewsCatalystFeatureProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst feature projection is blocked"


def project_us_news_catalyst_feature_artifact(
    binding: UsFeatureEvidenceBinding,
) -> UsNewsCatalystFeatureArtifact:
    try:
        snapshot = binding.snapshot
        _validate_binding(binding, snapshot)
        return feature_artifact(
            UsNewsCatalystFeaturePayload(
                symbol=binding.symbol,
                instrument_id=snapshot.instrument_id,
                session_date=snapshot.volume_profile.target_session_date,
                observed_at=snapshot.observed_at,
                source_end_at=_time(snapshot.source_end_at),
                research_input_identity_sha256=snapshot.identity.identity_sha256,
                volume_profile_evidence_sha256=snapshot.volume_profile.evidence_sha256,
                indicator_semantic_version=snapshot.indicator_semantic_version,
                close=_decimal(snapshot.close),
                vwap=_decimal(snapshot.vwap),
                rvol=_decimal(snapshot.rvol),
                breakout_close_above_prior_high=_boolean(
                    snapshot.breakout_close_above_prior_high
                ),
            )
        )
    except (
        AttributeError,
        InvalidUsNewsCatalystFeatureModelError,
        IntradayVolumeProfileError,
        TypeError,
        ValueError,
    ):
        raise InvalidUsNewsCatalystFeatureProjectionError from None


def project_us_news_catalyst_setup_observations(
    cohort: UsNewsCatalystCohortArtifact,
    artifacts: tuple[UsNewsCatalystFeatureArtifact, ...],
    *,
    evaluated_at: dt.datetime,
) -> UsNewsCatalystSetupObservationManifest:
    try:
        selected = _select_cycle(cohort, artifacts, evaluated_at)
        observations = tuple(
            UsNewsCatalystSetupFeatureObservation(
                symbol=artifact.payload.symbol,
                feature_evidence_id=artifact.artifact_id,
                observed_at=artifact.payload.observed_at,
                close=artifact.payload.close,
                vwap=artifact.payload.vwap,
                rvol=artifact.payload.rvol,
                breakout_close_above_prior_high=(
                    artifact.payload.breakout_close_above_prior_high
                ),
            )
            for artifact in selected
        )
        return create_us_news_catalyst_setup_observation_manifest(
            trial_id=cohort.payload.trial_id,
            cohort_artifact_id=cohort.artifact_id,
            evaluator_version=US_NEWS_CATALYST_EVALUATOR_VERSION,
            observations=observations,
        )
    except (AttributeError, InvalidUsNewsCatalystFeatureModelError, TypeError, ValueError):
        raise InvalidUsNewsCatalystFeatureProjectionError from None


def _select_cycle(
    cohort: UsNewsCatalystCohortArtifact,
    artifacts: tuple[UsNewsCatalystFeatureArtifact, ...],
    evaluated_at: dt.datetime,
) -> tuple[UsNewsCatalystFeatureArtifact, ...]:
    payload = cohort.payload
    expected = tuple(sorted((*payload.treatment_symbols, *payload.control_symbols)))
    bounds = regular_session_bounds(payload.session_date)
    if (
        cohort.payload.status is not UsNewsCatalystCohortStatus.READY
        or type(artifacts) is not tuple
        or not _aware(evaluated_at)
        or bounds is None
        or not bounds[0] <= evaluated_at <= bounds[1]
    ):
        raise InvalidUsNewsCatalystFeatureProjectionError
    target = payload.observed_at + US_NEWS_CATALYST_SETUP_HORIZON
    cycles: dict[dt.datetime, dict[str, UsNewsCatalystFeatureArtifact]] = {}
    for artifact in artifacts:
        item = UsNewsCatalystFeatureArtifact.model_validate(artifact.model_dump())
        feature = item.payload
        if (
            feature.symbol not in expected
            or feature.session_date != payload.session_date
            or feature.observed_at > evaluated_at
            or evaluated_at - feature.observed_at > _MAX_FEATURE_AGE
            or feature.source_end_at < target
        ):
            continue
        cycle = cycles.setdefault(feature.observed_at, {})
        if feature.symbol in cycle:
            raise InvalidUsNewsCatalystFeatureProjectionError
        cycle[feature.symbol] = item
    complete = tuple(
        (observed_at, values)
        for observed_at, values in cycles.items()
        if tuple(sorted(values)) == expected
    )
    if not complete:
        raise InvalidUsNewsCatalystFeatureProjectionError
    _observed_at, selected = max(complete, key=lambda item: item[0])
    return tuple(selected[symbol] for symbol in expected)


def _validate_binding(
    binding: UsFeatureEvidenceBinding,
    snapshot: IntradayFeatureSnapshot,
) -> None:
    if (
        type(binding) is not UsFeatureEvidenceBinding
        or type(snapshot) is not IntradayFeatureSnapshot
        or snapshot.status is not FeatureSnapshotStatus.READY
        or type(snapshot.identity) is not ResearchInputIdentity
        or not binding.symbol
        or not snapshot.instrument_id
        or not _aware(snapshot.observed_at)
        or not snapshot.indicator_semantic_version
    ):
        raise InvalidUsNewsCatalystFeatureProjectionError
    validate_intraday_volume_profile(snapshot.volume_profile)
    if snapshot.volume_profile.instrument_id != snapshot.instrument_id:
        raise InvalidUsNewsCatalystFeatureProjectionError


def _decimal(value: Decimal | None) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise InvalidUsNewsCatalystFeatureProjectionError
    return value


def _boolean(value: bool | None) -> bool:
    if type(value) is not bool:
        raise InvalidUsNewsCatalystFeatureProjectionError
    return value


def _time(value: dt.datetime | None) -> dt.datetime:
    if value is None or not _aware(value):
        raise InvalidUsNewsCatalystFeatureProjectionError
    return value


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidUsNewsCatalystFeatureProjectionError",
    "project_us_news_catalyst_feature_artifact",
    "project_us_news_catalyst_setup_observations",
)
