from __future__ import annotations

import datetime as dt
import importlib
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.day_forward_probe_bridge_support import (
    OBSERVED_AT,
    BarMutation,
    CandidateMutation,
    CapsuleMutation,
    EvidenceMutation,
    QuoteMutation,
    mutated_bar_request,
    mutated_candidate_request,
    mutated_capsule_request,
    mutated_evidence_request,
    mutated_quote_request,
    projection_request,
    require_signal,
)
from trading_agent.day_forward_probe_bridge import (
    DaySignalBlocked,
    DaySignalBlockReason,
    DayTargetProjectionPolicy,
    DayTargetRule,
    project_day_trade_signal,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import (
    SignalActionability,
    TradeSignalEnvelope,
)


def test_day_forward_probe_bridge_exposes_typed_host_boundary() -> None:
    # Given: the importable Foundation Task 6 bridge module.
    module = importlib.import_module("trading_agent.day_forward_probe_bridge")
    required_names = (
        "DayCompletedBarLineage",
        "DaySignalBlockReason",
        "DaySignalBlocked",
        "DayTargetProjectionPolicy",
        "DayTargetRule",
        "DayTradeSignalProjectionRequest",
        "project_day_trade_signal",
    )

    # When: its public host-boundary names are enumerated.
    exposed = tuple(name for name in required_names if hasattr(module, name))

    # Then: all request, policy, outcome, and projection types exist.
    assert exposed == required_names


def test_host_projects_us_candidate_into_owned_trade_signal() -> None:
    # Given: a current US completed bar and verified capsule candidate.
    request = projection_request(MarketId.US_EQUITIES)

    # When: the host projects the untrusted candidate.
    result = project_day_trade_signal(request)

    # Then: targets, validity, evidence, and actionability are host-owned.
    signal = require_signal(result)
    assert signal.strategy_lane.market_id is MarketId.US_EQUITIES
    assert signal.symbol == "TEST"
    assert tuple((target.label, target.price) for target in signal.targets) == (
        ("1r", Decimal("11.0")),
        ("2r", Decimal("11.5")),
    )
    assert signal.valid_until == OBSERVED_AT + dt.timedelta(seconds=30)
    assert signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
    assert tuple(reference.namespace for reference in signal.evidence_refs) == (
        "day/cost_model",
        "day/strategy_capsule",
        "market/completed_bar",
        "source/news",
    )


def test_host_projects_kr_candidate_without_execution_authority() -> None:
    # Given: a current Korean completed bar and research-only capsule.
    request = projection_request(MarketId.KR_EQUITIES)

    # When: the shared host bridge projects the candidate.
    result = project_day_trade_signal(request)

    # Then: the same envelope remains market-local and contains no authority field.
    signal = require_signal(result)
    assert signal.strategy_lane.market_id is MarketId.KR_EQUITIES
    assert signal.symbol == "005930"
    assert "authority" not in signal.model_dump(mode="json")


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("future", DaySignalBlockReason.BAR_FUTURE),
        ("stale", DaySignalBlockReason.BAR_STALE),
        ("symbol", DaySignalBlockReason.BAR_IDENTITY_MISMATCH),
        ("timestamp", DaySignalBlockReason.BAR_IDENTITY_MISMATCH),
        ("invalid_symbol", DaySignalBlockReason.SYMBOL_INVALID),
    ),
)
def test_host_blocks_invalid_completed_bar_lineage(
    mutation: BarMutation,
    reason: DaySignalBlockReason,
) -> None:
    # Given: one relational mutation of an otherwise current US bar request.
    request = mutated_bar_request(mutation)

    # When: the host evaluates the causal bar identity.
    result = project_day_trade_signal(request)

    # Then: it returns the stable blocked reason.
    assert result == DaySignalBlocked(reason=reason)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("market", DaySignalBlockReason.CAPSULE_MARKET_MISMATCH),
        ("future", DaySignalBlockReason.CAPSULE_NOT_ACTIVE),
        ("bundle", DaySignalBlockReason.CAPSULE_HOST_BUNDLE_MISMATCH),
    ),
)
def test_host_blocks_capsule_lineage_mismatch(
    mutation: CapsuleMutation,
    reason: DaySignalBlockReason,
) -> None:
    # Given: one invalid capsule relationship to a current US bar.
    request = mutated_capsule_request(mutation)

    # When: the host validates the capsule lineage.
    result = project_day_trade_signal(request)

    # Then: it returns the stable blocked reason.
    assert result == DaySignalBlocked(reason=reason)


@pytest.mark.parametrize("mutation", ("nonfinite", "direction", "rationale"))
def test_host_blocks_invalid_generated_candidate(mutation: CandidateMutation) -> None:
    # Given: a parsed session candidate with invalid numeric or text content.
    request = mutated_candidate_request(mutation)

    # When: the host projects the candidate into its signal contract.
    result = project_day_trade_signal(request)

    # Then: the invalid internal value cannot escape as a signal.
    assert result == DaySignalBlocked(reason=DaySignalBlockReason.CANDIDATE_INVALID)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("stale", DaySignalBlockReason.QUOTE_STALE),
        ("spread", DaySignalBlockReason.SPREAD_TOO_WIDE),
    ),
)
def test_host_blocks_non_actionable_quote(
    mutation: QuoteMutation,
    reason: DaySignalBlockReason,
) -> None:
    # Given: a stale or over-wide quote on a valid candidate.
    request = mutated_quote_request(mutation)

    # When: the host checks current actionability.
    result = project_day_trade_signal(request)

    # Then: it returns the stable blocked reason.
    assert result == DaySignalBlocked(reason=reason)


@pytest.mark.parametrize("mutation", ("unsorted", "future", "reserved"))
def test_host_blocks_untrusted_evidence_lineage(mutation: EvidenceMutation) -> None:
    # Given: evidence that is non-canonical, future-dated, or host-reserved.
    request = mutated_evidence_request(mutation)

    # When: the host validates the evidence boundary.
    result = project_day_trade_signal(request)

    # Then: no signal is projected from that evidence.
    assert result == DaySignalBlocked(reason=DaySignalBlockReason.EVIDENCE_INVALID)


@pytest.mark.parametrize(
    "rules",
    (
        (),
        (
            ("2r", Decimal("2")),
            ("1r", Decimal("1")),
        ),
        (("1r", Decimal("0")),),
    ),
)
def test_target_policy_rejects_unbounded_or_noncanonical_rules(
    rules: tuple[tuple[str, Decimal], ...],
) -> None:
    # Given: a host target policy without bounded canonical rules.

    # When/Then: the typed boundary rejects it before projection.
    with pytest.raises(ValidationError):
        _ = DayTargetProjectionPolicy(
            rules=tuple(
                DayTargetRule(label=label, reward_risk_multiple=multiple)
                for label, multiple in rules
            ),
            valid_for=dt.timedelta(seconds=30),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("position_size", 100),
        ("provider", "alpaca"),
        ("authority", True),
        ("order", {"type": "market"}),
    ),
)
def test_projected_envelope_rejects_execution_owned_fields(
    field: str,
    value: str | int | bool | dict[str, str],
) -> None:
    # Given: a projected host signal plus one execution-owned field.
    result = project_day_trade_signal(projection_request(MarketId.US_EQUITIES))
    payload = require_signal(result).model_dump(mode="python")

    # When/Then: the final envelope rejects the expanded authority surface.
    with pytest.raises(ValidationError):
        _ = TradeSignalEnvelope.model_validate(payload | {field: value})
