from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from tests.strategy_research_contract_fixtures import (
    NOW,
    SHA_A,
    SHA_B,
)
from tests.strategy_research_contract_fixtures import (
    hypothesis as _hypothesis,
)
from tests.strategy_research_contract_fixtures import (
    observation as _observation,
)
from tests.strategy_research_contract_fixtures import (
    source as _source,
)
from trading_agent.strategy_research_catalog import STRATEGY_RESEARCH_CATALOG
from trading_agent.strategy_research_models import (
    EvidenceObservation,
    EvidenceRef,
    FreeParameter,
    ImmutableHypothesis,
    PreregistrationManifest,
)
from trading_agent.strategy_research_results import ResearchAttempt, TerminalResearchResult
from trading_agent.strategy_research_types import (
    AttemptStatus,
    EvidenceKind,
    EvidenceUse,
    ResearchAgentId,
    SafeTerminalReason,
    TerminalOutcome,
)


def test_catalog_has_six_distinct_identity_methodology_and_cadence_contracts() -> None:
    # Given: the canonical strategy-research catalog.
    rows = STRATEGY_RESEARCH_CATALOG

    # When: identity-bearing values are projected.
    identities = {row.identity for row in rows}
    methodologies = {row.methodology for row in rows}
    cadence_semantics = {row.agent_id: (row.cadence.trigger, row.cadence.delay_minutes) for row in rows}

    # Then: all six identities and owner-specific cadence grammars are distinct.
    assert {row.agent_id for row in rows} == set(ResearchAgentId)
    assert len(identities) == len(methodologies) == len(ResearchAgentId)
    assert cadence_semantics == {
        ResearchAgentId.INTRADAY_MOMENTUM: ("completed liquid five-minute bar with fresh spread", 5),
        ResearchAgentId.INTRADAY_MEAN_REVERSION: (
            "completed five-minute bar after displacement and coverage gate",
            5,
        ),
        ResearchAgentId.CATALYST_EVENT: ("new immutable catalyst receipt", 15),
        ResearchAgentId.SWING_TREND_REGIME: ("completed NYSE session or ex-ante regime change", 30),
        ResearchAgentId.CROSS_SECTIONAL_QUANT: ("point-in-time universe snapshot maturity", 45),
        ResearchAgentId.DERIVATIVES_VOLATILITY: ("new complete option or futures surface", 0),
    }


def test_observation_and_hypothesis_require_universe_and_target_times() -> None:
    # Given: distinct universe, predictor, owner-observation, and target-maturity instants.
    observation = _observation()
    universe_observed_at = NOW - dt.timedelta(minutes=20)
    target_matures_at = observation.predictor_observed_at + dt.timedelta(days=2)

    # When: the explicit PIT grammar is parsed at both boundaries.
    enriched_observation = EvidenceObservation.model_validate(
        observation.model_dump()
        | {"universe_observed_at": universe_observed_at, "target_matures_at": target_matures_at}
    )
    hypothesis = _hypothesis()
    enriched_hypothesis = ImmutableHypothesis.model_validate(
        hypothesis.model_dump()
        | {"universe_observed_at": universe_observed_at, "target_matures_at": target_matures_at}
    )

    # Then: timestamps are typed and ordering violations fail closed.
    assert enriched_hypothesis.universe_observed_at == enriched_observation.universe_observed_at
    assert enriched_hypothesis.target_matures_at == enriched_observation.target_matures_at
    with pytest.raises(ValidationError):
        _ = EvidenceObservation.model_validate(
            enriched_observation.model_dump() | {"target_matures_at": observation.predictor_observed_at}
        )
    with pytest.raises(ValidationError):
        _ = EvidenceObservation.model_validate(
            enriched_observation.model_dump() | {"universe_observed_at": universe_observed_at.replace(tzinfo=None)}
        )
    shifted_target = target_matures_at + dt.timedelta(minutes=1)
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(
            enriched_hypothesis.model_dump()
            | {
                "target_matures_at": shifted_target,
                "observation": enriched_observation.model_dump() | {"target_matures_at": shifted_target},
            }
        )


def test_canonical_hash_normalizes_equal_aware_instants_to_utc() -> None:
    # Given: one immutable source represented in UTC and at the equal +09:00 instant.
    utc_source = _source()
    korea = dt.timezone(dt.timedelta(hours=9))
    offset_source = EvidenceRef.model_validate(
        utc_source.model_dump()
        | {"as_of": utc_source.as_of.astimezone(korea), "available_at": utc_source.available_at.astimezone(korea)}
    )

    # When/Then: canonical identity follows the instant, not its display offset.
    assert utc_source.content_sha256 == offset_source.content_sha256


def test_canonical_hash_preserves_datetime_shaped_identity_strings() -> None:
    # Given: distinct opaque evidence IDs that resemble equal datetime instants.
    utc_identity = EvidenceRef.model_validate(
        _source().model_dump() | {"evidence_id": "2026-08-19T14:00:00+00:00"}
    )
    korea_identity = EvidenceRef.model_validate(
        _source().model_dump() | {"evidence_id": "2026-08-19T23:00:00+09:00"}
    )

    # When/Then: canonicalization preserves ordinary string identity bytes.
    assert utc_identity.content_sha256 != korea_identity.content_sha256


def test_real_evidence_declares_task3_live_eligibility_policy() -> None:
    # Given/When: a REAL immutable evidence reference is inspected.
    source = _source()

    # Then: it cannot ambiguously claim current/live eligibility at the Task 2 boundary.
    assert source.live_eligibility_policy.value == "task3_current_session_gate_required"


def test_observation_requires_aware_point_in_time_order_and_coverage() -> None:
    # Given: an observation with explicit stable timestamps.
    observation = _observation()

    # When: point-in-time order or timezone awareness is invalidated.
    with pytest.raises(ValidationError):
        _ = EvidenceObservation.model_validate(
            observation.model_dump() | {"observed_at": NOW.replace(tzinfo=None)}
        )

    with pytest.raises(ValidationError):
        _ = EvidenceObservation.model_validate(
            observation.model_dump() | {"predictor_observed_at": observation.observed_at + dt.timedelta(seconds=1)}
        )

    # Then: the valid source-bound observation remains content-addressed.
    assert observation.content_sha256 == _observation().content_sha256


def test_fixture_and_synthetic_evidence_are_wiring_only() -> None:
    # Given: non-real evidence at the parsing boundary.
    fixture = _source(kind=EvidenceKind.FIXTURE)

    # When/Then: it is explicitly wiring-only and cannot claim research use.
    assert fixture.evidence_use is EvidenceUse.WIRING_ONLY
    with pytest.raises(ValidationError):
        _ = EvidenceRef.model_validate(fixture.model_dump() | {"evidence_use": EvidenceUse.RESEARCH})


def test_hypothesis_is_complete_frozen_non_profitable_and_content_addressed() -> None:
    # Given: a complete source-bound preregistered hypothesis.
    hypothesis = _hypothesis()

    # When: semantically equal objects and a manifest are built.
    equal = _hypothesis()
    manifest = PreregistrationManifest.from_hypothesis(hypothesis, preregistered_at=NOW)

    # Then: hashes are deterministic, nested contracts are frozen, and authority stays false.
    assert hypothesis.content_sha256 == equal.content_sha256 == manifest.hypothesis_sha256
    assert hypothesis.trading_authority is False
    assert hypothesis.profitability_claim is False
    with pytest.raises(ValidationError):
        hypothesis.primary_metric = "changed after preregistration"
    with pytest.raises(ValidationError):
        hypothesis.observation.coverage_fraction = 0.5


def test_hypothesis_rejects_missing_required_field_and_shortcuts() -> None:
    # Given: the canonical complete payload.
    payload = _hypothesis().model_dump()

    # When/Then: a required mechanism cannot be omitted.
    payload.pop("economic_mechanism")
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(payload)

    complete_payload = _hypothesis().model_dump()
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(complete_payload | {"source_refs": ()})
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(complete_payload | {"trading_authority": True})
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(complete_payload | {"profitability_claim": True})

    # Given: a fixed one-value search and naive normal-only sufficiency shortcut.
    hypothesis = _hypothesis()
    with pytest.raises(ValidationError):
        _ = FreeParameter(name="fixed_threshold", candidate_values=(1.0,), lower_bound=1.0, upper_bound=1.0)
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(
            hypothesis.model_dump() | {"power_or_ci_gate": "naive normal CI"}
        )


def test_hypothesis_rejects_source_time_and_split_mismatches() -> None:
    # Given: a complete source-bound hypothesis.
    hypothesis = _hypothesis()

    # When/Then: owner/source identity, creation order, and train/validation order are fixed.
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(
            hypothesis.model_dump() | {"agent_id": ResearchAgentId.INTRADAY_MOMENTUM}
        )
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(
            hypothesis.model_dump() | {"created_at": hypothesis.observation.observed_at - dt.timedelta(seconds=1)}
        )
    with pytest.raises(ValidationError):
        _ = ImmutableHypothesis.model_validate(
            hypothesis.model_dump()
            | {"validation_period": {"start": hypothesis.train_period.end, "end": hypothesis.validation_period.end}}
        )


@pytest.mark.parametrize(
    "status",
    [
        AttemptStatus.STARTED,
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.ABORTED,
        AttemptStatus.TIMED_OUT,
        AttemptStatus.CANCELLED,
        AttemptStatus.CENSORED,
    ],
)
def test_attempt_contract_retains_every_typed_status(status: AttemptStatus) -> None:
    # Given/When: every attempt status is represented by an immutable value contract.
    attempt = ResearchAttempt(
        attempt_id=f"attempt-{status.value}",
        hypothesis_id=_hypothesis().hypothesis_id,
        branch_index=0,
        input_hashes=(SHA_A,),
        code_sha256=SHA_A,
        data_manifest_sha256=SHA_B,
        started_at=NOW,
        finished_at=None if status is AttemptStatus.STARTED else NOW + dt.timedelta(seconds=1),
        status=status,
        artifact_refs=() if status is AttemptStatus.STARTED else ("artifact://safe/result",),
        error_class=None if status in {AttemptStatus.STARTED, AttemptStatus.SUCCEEDED} else "BoundedAttemptFailure",
        max_cpu_seconds=60,
    )

    # Then: the status survives the boundary and the record is content-addressed.
    assert attempt.status is status
    assert len(attempt.content_sha256) == 64


def test_terminal_result_exposes_no_holdout_values_or_authority() -> None:
    # Given/When: a sanitized owner-visible terminal result is constructed.
    result = TerminalResearchResult(
        result_id="result-catalyst-001",
        hypothesis_id=_hypothesis().hypothesis_id,
        owner_agent_id=ResearchAgentId.CATALYST_EVENT,
        outcome=TerminalOutcome.INCONCLUSIVE,
        reason_codes=(SafeTerminalReason.CI_WIDTH_TOO_WIDE,),
        artifact_refs=(f"artifact://safe/{SHA_A}",),
        evaluated_at=NOW,
        trading_authority=False,
        profitability_claim=False,
    )

    # Then: only sanitized state is present.
    assert "holdout" not in result.model_dump_json()
    assert result.trading_authority is result.profitability_claim is False


@pytest.mark.parametrize(
    ("reason_codes", "artifact_refs"),
    [
        (("holdout_metric=0.031",), (f"artifact://safe/{SHA_A}",)),
        (("HOLDOUT_PATTERN_POSITIVE_ONLY",), (f"artifact://safe/{SHA_A}",)),
        (("CI_WIDTH_TOO_WIDE",), ("artifact://private/holdout-metrics.json",)),
        (("CI_WIDTH_TOO_WIDE",), ("artifact://raw/holdout-contributions.json",)),
    ],
)
def test_terminal_result_rejects_holdout_leakage(
    reason_codes: tuple[str, ...], artifact_refs: tuple[str, ...]
) -> None:
    # Given: verifier-shaped exact metrics, patterns, or private/raw holdout refs.
    payload = TerminalResearchResult(
        result_id="result-catalyst-safe-baseline",
        hypothesis_id=_hypothesis().hypothesis_id,
        owner_agent_id=ResearchAgentId.CATALYST_EVENT,
        outcome=TerminalOutcome.INCONCLUSIVE,
        reason_codes=(SafeTerminalReason.CI_WIDTH_TOO_WIDE,),
        artifact_refs=(f"artifact://safe/{SHA_A}",),
        evaluated_at=NOW,
    ).model_dump()

    # When/Then: the owner-feedback boundary rejects the leaking shape.
    with pytest.raises(ValidationError):
        _ = TerminalResearchResult.model_validate(
            payload | {"reason_codes": reason_codes, "artifact_refs": artifact_refs}
        )


def test_manual_contract_surface() -> None:
    # Given/When/Then: one pytest command prints catalog identities and a source-bound hash.
    for row in STRATEGY_RESEARCH_CATALOG:
        print(f"{row.agent_id.value}|{row.methodology}|{row.cadence.trigger}|{row.content_sha256}")
    print(f"hypothesis|{_hypothesis().hypothesis_id}|{_hypothesis().content_sha256}")
