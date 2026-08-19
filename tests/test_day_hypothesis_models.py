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
    TargetHorizon,
)
from trading_agent.experiment_ledger_keys import (
    day_hypothesis_family_key,
    day_hypothesis_version_key,
)
from trading_agent.research_identity_models import MarketId
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
    updates = (
        {"predictor": "gap_pct > 0.03"},
        {"target": "next_10m_return"},
        {"threshold": Decimal("2.50")},
        {"free_parameters": (FreeParameter(name="relative_volume", values=(Decimal("1.75"),)),)},
        {
            "cost_model": CostModelDeclaration(
                model_id="us_equities_cost_v2",
                commission_bps=Decimal("1.0"),
                slippage_bps=Decimal("3.0"),
            )
        },
        {"code_sha256": "b" * 64},
        {"protocol_sha256": "c" * 64},
        {"data_manifest_sha256": "d" * 64},
    )

    # Then: every scientific mutation requires a distinct canonical identity.
    for update in updates:
        payload = version.model_dump(mode="python") | update
        version_id = HypothesisVersion.canonical_id_for(payload)
        changed = HypothesisVersion.model_validate(payload | {"hypothesis_version_id": version_id})
        assert changed.hypothesis_version_id != version.hypothesis_version_id


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


def test_model_copy_rejects_extra_authority_and_stale_identity_mutations() -> None:
    # Given: validated family and market-version identities.
    family = family_fixture()
    version = version_fixture(family)
    authority_payload = version.model_dump(mode="python") | {"trading_authority": True}
    recomputed_authority_id = HypothesisVersion.canonical_id_for(authority_payload)

    # When / Then: direct copy cannot add family scope, claim authority, or retain a stale identity.
    with pytest.raises(ValidationError):
        _ = family.model_copy(update={"market_id": MarketId.US_EQUITIES})
    with pytest.raises(ValidationError, match="hypothesis_version_cannot_grant_authority"):
        _ = version.model_copy(
            update={"trading_authority": True, "hypothesis_version_id": recomputed_authority_id}
        )
    with pytest.raises(ValidationError, match="hypothesis_version_cannot_claim_profitability"):
        _ = version.model_copy(update={"profitability_claim": True})
    for update in (
        {"predictor": "gap_pct > 0.03"},
        {"target": "next_10m_return"},
        {"threshold": Decimal("2.50")},
        {"free_parameters": (FreeParameter(name="relative_volume", values=(Decimal("1.75"),)),)},
        {
            "cost_model": CostModelDeclaration(
                model_id="us_equities_cost_v2",
                commission_bps=Decimal("1.0"),
                slippage_bps=Decimal("3.0"),
            )
        },
        {"code_sha256": "b" * 64},
        {"protocol_sha256": "c" * 64},
        {"data_manifest_sha256": "d" * 64},
        {"market_id": MarketId.KR_EQUITIES},
    ):
        with pytest.raises(ValidationError, match="hypothesis_version_id_mismatch"):
            _ = version.model_copy(update=update)


def test_model_validate_and_key_helpers_reject_forged_instances() -> None:
    # Given: forged Pydantic instances created without validators.
    forged_family = HypothesisFamily.model_construct(family_id="0" * 64)
    forged_version = HypothesisVersion.model_construct(trading_authority=True)

    # When / Then: direct validation and ledger key helpers revalidate rather than trust the instance.
    with pytest.raises(ValidationError, match="hypothesis_version_cannot_grant_authority"):
        _ = HypothesisVersion.model_validate(forged_version)
    with pytest.raises(ValidationError):
        _ = day_hypothesis_family_key(forged_family)
    with pytest.raises(ValidationError, match="hypothesis_version_cannot_grant_authority"):
        _ = day_hypothesis_version_key(forged_version)


def test_nested_constructed_contracts_and_search_budget_copies_are_revalidated() -> None:
    # Given: a valid version and invalid nested instances constructed without validation.
    version = version_fixture(family_fixture())
    invalid_cost = CostModelDeclaration.model_construct(
        model_id="cost-v1", commission_bps=Decimal("NaN"), slippage_bps=Decimal("1")
    )
    invalid_parameter = FreeParameter.model_construct(name="relative_volume", values=(Decimal("NaN"),))
    invalid_budget = SearchBudget.model_construct(max_parameter_combinations=2, max_attempts=0, max_cpu_seconds=60)
    invalid_horizon = TargetHorizon.model_construct(duration=dt.timedelta(0))

    # When / Then: copy and top-level registration reject every nested invalid contract.
    with pytest.raises(ValidationError):
        _ = SearchBudget(max_parameter_combinations=2, max_attempts=2, max_cpu_seconds=60).model_copy(
            update={"max_attempts": 0}
        )
    for update in (
        {"cost_model": invalid_cost},
        {"free_parameters": (invalid_parameter,)},
        {"search_budget": invalid_budget},
        {"target_horizon": invalid_horizon},
    ):
        with pytest.raises(ValidationError):
            _ = version.model_copy(update=update)


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
