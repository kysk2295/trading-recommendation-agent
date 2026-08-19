from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.day_hypothesis_models import (
    CostModelDeclaration,
    FreeParameter,
    HypothesisFamily,
    HypothesisVersion,
    SearchBudget,
)
from trading_agent.experiment_ledger_keys import (
    day_hypothesis_family_key,
    day_hypothesis_version_key,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_models import TargetHorizon
from trading_agent.strategy_research_types import ExpectedDirection

CREATED_AT = dt.datetime(2026, 8, 20, 13, 30, tzinfo=dt.UTC)


def test_same_family_can_have_distinct_us_and_kr_versions() -> None:
    # Given: one market-neutral economic mechanism.
    family = family_fixture()

    # When: the mechanism is registered for each market.
    us = version_fixture(family, market_id=MarketId.US_EQUITIES)
    kr = version_fixture(family, market_id=MarketId.KR_EQUITIES)

    # Then: the family remains shared while the version identities remain market-keyed.
    assert us.family_id == kr.family_id == family.family_id
    assert us.hypothesis_version_id != kr.hypothesis_version_id


def test_version_rejects_authority_or_profitability_claim() -> None:
    # Given: an otherwise valid market version.
    family = family_fixture()

    # When / Then: research identity cannot carry execution authority or a profitability claim.
    with pytest.raises(ValidationError, match="hypothesis_version_cannot_grant_authority"):
        _ = version_fixture(family, trading_authority=True)
    with pytest.raises(ValidationError, match="hypothesis_version_cannot_claim_profitability"):
        _ = version_fixture(family, profitability_claim=True)


def test_family_rejects_wrong_canonical_identity() -> None:
    # Given: a canonical market-neutral family payload.
    payload = family_fixture().model_dump(mode="python")

    # When / Then: caller-supplied identities must match the canonical payload.
    with pytest.raises(ValidationError, match="hypothesis_family_id_mismatch"):
        _ = HypothesisFamily.model_validate(payload | {"family_id": "0" * 64})


def test_version_identity_changes_for_market_and_research_mutations() -> None:
    # Given: a valid US market version.
    version = version_fixture(family_fixture())

    # When: each identity-bearing scientific declaration changes independently.
    variants = (
        version.model_copy(update={"predictor": "gap_pct > 0.03"}),
        version.model_copy(update={"target": "next_10m_return"}),
        version.model_copy(update={"threshold": Decimal("2.50")}),
        version.model_copy(
            update={"free_parameters": (FreeParameter(name="relative_volume", values=(Decimal("1.75"),)),)}
        ),
        version.model_copy(
            update={
                "cost_model": CostModelDeclaration(
                    model_id="us_equities_cost_v2",
                    commission_bps=Decimal("1.0"),
                    slippage_bps=Decimal("3.0"),
                )
            }
        ),
        version.model_copy(update={"code_sha256": "b" * 64}),
        version.model_copy(update={"protocol_sha256": "c" * 64}),
        version.model_copy(update={"data_manifest_sha256": "d" * 64}),
    )

    # Then: a copied object with a stale ID is rejected at the validation boundary.
    for variant in variants:
        with pytest.raises(ValidationError, match="hypothesis_version_id_mismatch"):
            _ = HypothesisVersion.model_validate(variant.model_dump(mode="python"))


def test_version_requires_sorted_unique_references_tags_and_finite_decimals() -> None:
    # Given: one valid version payload.
    payload = version_fixture(family_fixture()).model_dump(mode="python")

    # When / Then: canonical collections and monetary declarations reject invalid values.
    for field, value in (
        ("source_refs", ("source:b", "source:a")),
        ("methodology_tags", ("intraday", "intraday")),
        ("threshold", Decimal("NaN")),
    ):
        with pytest.raises(ValidationError):
            _ = HypothesisVersion.model_validate(payload | {field: value})


def test_version_requires_aware_and_temporally_coherent_timestamps() -> None:
    # Given: a valid version payload.
    payload = version_fixture(family_fixture()).model_dump(mode="python")

    # When / Then: naive and future-ineligible registrations cannot enter the ledger.
    with pytest.raises(ValidationError, match="invalid day hypothesis version"):
        _ = HypothesisVersion.model_validate(payload | {"sampling_timestamp": dt.datetime(2026, 8, 20, 13, 29)})
    with pytest.raises(ValidationError, match="invalid day hypothesis version"):
        _ = HypothesisVersion.model_validate(
            payload | {"first_shadow_eligible_at": payload["registration_completed_bar_at"]}
        )


def test_day_hypothesis_ledger_keys_use_canonical_models() -> None:
    # Given: independently validated family and version records.
    family = family_fixture()
    version = version_fixture(family)

    # When: the ledger keys are derived.
    family_key = day_hypothesis_family_key(family)
    version_key = day_hypothesis_version_key(version)

    # Then: both are stable SHA-256 keys distinct from their domain identities.
    assert len(family_key) == len(version_key) == 64
    assert family_key == day_hypothesis_family_key(family)
    assert version_key == day_hypothesis_version_key(version)
    assert family_key != family.family_id
    assert version_key != version.hypothesis_version_id


def family_fixture() -> HypothesisFamily:
    payload = {
        "family_id": "",
        "parent_family_id": None,
        "canonical_question": "Does opening relative volume predict a same-session continuation?",
        "economic_mechanism": "Institutional order imbalance persists after price discovery.",
        "alternative_explanations": ("market beta", "news reversal"),
        "counterfactual_baseline": "market-adjusted zero-return baseline",
        "created_by": "day_discovery",
        "created_at": CREATED_AT,
        "source_lineage": ("research:market-context", "research:opening-volume"),
    }
    family_id = HypothesisFamily.canonical_id_for(payload)
    return HypothesisFamily.model_validate(payload | {"family_id": family_id})


def version_fixture(
    family: HypothesisFamily,
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
    trading_authority: bool = False,
    profitability_claim: bool = False,
) -> HypothesisVersion:
    payload = {
        "hypothesis_version_id": "",
        "family_id": family.family_id,
        "parent_version_id": None,
        "market_id": market_id,
        "universe_snapshot_id": "liquid-universe-2026-08-20",
        "universe_snapshot_at": CREATED_AT,
        "source_refs": ("source:market-context", "source:opening-volume"),
        "methodology_tags": ("cross_sectional", "intraday"),
        "primary_evaluation_owner": "day_research",
        "evaluation_cadence": "each_completed_bar",
        "predictor": "relative_opening_volume",
        "sampling_timestamp": CREATED_AT + dt.timedelta(minutes=1),
        "target": "next_5m_market_adjusted_return",
        "target_horizon": TargetHorizon(duration=dt.timedelta(minutes=5)),
        "expected_direction": ExpectedDirection.POSITIVE,
        "entry_rule": "enter_next_completed_bar",
        "exit_rule": "exit_at_target_horizon",
        "stop_rule": "exit_when_loss_exceeds_one_r",
        "invalidation_rule": "invalidate_when_spread_missing",
        "threshold": Decimal("2.00"),
        "cost_model": CostModelDeclaration(
            model_id="us_equities_cost_v1",
            commission_bps=Decimal("1.0"),
            slippage_bps=Decimal("2.0"),
        ),
        "free_parameters": (FreeParameter(name="relative_volume", values=(Decimal("1.50"), Decimal("2.00"))),),
        "search_budget": SearchBudget(max_parameter_combinations=2, max_attempts=2, max_cpu_seconds=60),
        "multiple_testing_family": "opening-volume-day-v1",
        "model_sha256": "1" * 64,
        "prompt_sha256": "2" * 64,
        "code_sha256": "3" * 64,
        "data_manifest_sha256": "4" * 64,
        "protocol_sha256": "5" * 64,
        "created_at": CREATED_AT + dt.timedelta(minutes=2),
        "registration_completed_bar_at": CREATED_AT + dt.timedelta(minutes=3),
        "first_shadow_eligible_at": CREATED_AT + dt.timedelta(minutes=4),
        "trading_authority": trading_authority,
        "profitability_claim": profitability_claim,
    }
    version_id = HypothesisVersion.canonical_id_for(payload)
    return HypothesisVersion.model_validate(payload | {"hypothesis_version_id": version_id})
