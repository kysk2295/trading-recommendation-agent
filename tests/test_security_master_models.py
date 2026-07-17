from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.security_master_models import (
    AssetClass,
    CorporateAction,
    CorporateActionType,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasResolutionError,
    InstrumentAliasType,
    InstrumentId,
    resolve_instrument_alias,
)

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
CHANGE = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
END = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)


def test_instrument_identity_is_opaque_and_point_in_time() -> None:
    instrument = _instrument()

    assert instrument.value == "us-eq-fixture-0001"
    assert instrument.model_dump(mode="json") == {
        "schema_version": 1,
        "value": "us-eq-fixture-0001",
        "market_domain": "us_equities",
        "asset_class": "equity",
        "venue": "XNAS",
        "currency": "USD",
        "timezone": "America/New_York",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    with pytest.raises(ValidationError):
        instrument.value = "AAPL"


@pytest.mark.parametrize(
    "override",
    (
        {"value": "AAPL with spaces"},
        {"venue": "xnas"},
        {"currency": "US"},
        {"timezone": "Mars/Olympus"},
        {"valid_from": START.replace(tzinfo=None)},
        {"valid_to": START},
        {"unexpected": "field"},
    ),
)
def test_instrument_rejects_noncanonical_or_invalid_point_in_time_fields(
    override: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "value": "us-eq-fixture-0001",
        "market_domain": DataMarketDomain.US_EQUITIES,
        "asset_class": AssetClass.EQUITY,
        "venue": "XNAS",
        "currency": "USD",
        "timezone": "America/New_York",
        "valid_from": START,
        "valid_to": END,
    }
    payload.update(override)

    with pytest.raises(ValidationError):
        InstrumentId.model_validate(payload)


def test_alias_resolution_uses_namespace_and_half_open_time() -> None:
    aliases = (
        _alias("OLD", START, CHANGE),
        _alias("NEW", CHANGE, END),
    )

    assert resolve_instrument_alias(aliases, namespace="sip", value="OLD", as_of=START) == _instrument().value
    assert resolve_instrument_alias(aliases, namespace="sip", value="NEW", as_of=CHANGE) == _instrument().value
    with pytest.raises(InstrumentAliasResolutionError):
        resolve_instrument_alias(aliases, namespace="sip", value="OLD", as_of=CHANGE)


def test_alias_resolution_rejects_ambiguous_overlap_even_for_one_instrument() -> None:
    aliases = (
        _alias("ABC", START, END),
        _alias("ABC", CHANGE, None),
    )

    with pytest.raises(InstrumentAliasResolutionError, match="종목 alias를 정확히 하나로 해석하지 못했습니다"):
        resolve_instrument_alias(aliases, namespace="sip", value="ABC", as_of=CHANGE)


@pytest.mark.parametrize(
    "override",
    (
        {"effective_from": START.replace(tzinfo=None)},
        {"effective_to": START},
        {"namespace": "SIP"},
    ),
)
def test_alias_contract_rejects_invalid_time_or_namespace(override: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "instrument_id": "us-eq-fixture-0001",
        "namespace": "sip",
        "alias_type": InstrumentAliasType.SYMBOL,
        "value": "ABC",
        "effective_from": START,
        "effective_to": END,
    }
    payload.update(override)

    with pytest.raises(ValidationError):
        InstrumentAlias.model_validate(payload)


def test_split_requires_only_a_positive_ratio() -> None:
    split = _action(
        CorporateActionType.SPLIT,
        ratio_numerator=Decimal("4"),
        ratio_denominator=Decimal("1"),
    )

    assert split.ratio_numerator == Decimal("4")
    with pytest.raises(ValidationError):
        _action(CorporateActionType.SPLIT)
    with pytest.raises(ValidationError):
        _action(
            CorporateActionType.SPLIT,
            ratio_numerator=Decimal("4"),
            ratio_denominator=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        _action(
            CorporateActionType.SPLIT,
            ratio_numerator=Decimal("4"),
            ratio_denominator=Decimal("1"),
            cash_amount=Decimal("1"),
            currency="USD",
        )


def test_cash_dividend_requires_only_positive_cash_and_currency() -> None:
    dividend = _action(
        CorporateActionType.CASH_DIVIDEND,
        cash_amount=Decimal("0.25"),
        currency="USD",
    )

    assert dividend.cash_amount == Decimal("0.25")
    with pytest.raises(ValidationError):
        _action(CorporateActionType.CASH_DIVIDEND, cash_amount=Decimal("0.25"))
    with pytest.raises(ValidationError):
        _action(CorporateActionType.CASH_DIVIDEND, cash_amount=Decimal("NaN"), currency="USD")


def test_symbol_change_and_delisting_forbid_optional_payloads() -> None:
    _ = _action(CorporateActionType.SYMBOL_CHANGE)
    _ = _action(CorporateActionType.DELISTING)

    with pytest.raises(ValidationError):
        _action(CorporateActionType.SYMBOL_CHANGE, successor_instrument_id="us-eq-fixture-0002")
    with pytest.raises(ValidationError):
        _action(CorporateActionType.DELISTING, successor_instrument_id="us-eq-fixture-0002")


@pytest.mark.parametrize("action_type", (CorporateActionType.MERGER, CorporateActionType.SPIN_OFF))
def test_reorganization_requires_a_distinct_successor(action_type: CorporateActionType) -> None:
    action = _action(action_type, successor_instrument_id="us-eq-fixture-0002")

    assert action.successor_instrument_id == "us-eq-fixture-0002"
    with pytest.raises(ValidationError):
        _action(action_type)
    with pytest.raises(ValidationError):
        _action(action_type, successor_instrument_id=_instrument().value)


def test_corporate_action_rejects_naive_or_reversed_timestamps() -> None:
    with pytest.raises(ValidationError):
        _action(CorporateActionType.DELISTING, announced_at=START.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _action(CorporateActionType.DELISTING, effective_at=START - dt.timedelta(seconds=1))


def _instrument() -> InstrumentId:
    return InstrumentId(
        value="us-eq-fixture-0001",
        market_domain=DataMarketDomain.US_EQUITIES,
        asset_class=AssetClass.EQUITY,
        venue="XNAS",
        currency="USD",
        timezone="America/New_York",
        valid_from=START,
        valid_to=END,
    )


def _alias(
    value: str,
    effective_from: dt.datetime,
    effective_to: dt.datetime | None,
) -> InstrumentAlias:
    return InstrumentAlias(
        instrument_id="us-eq-fixture-0001",
        namespace="sip",
        alias_type=InstrumentAliasType.SYMBOL,
        value=value,
        effective_from=effective_from,
        effective_to=effective_to,
    )


def _action(
    action_type: CorporateActionType,
    *,
    announced_at: dt.datetime = START,
    effective_at: dt.datetime = CHANGE,
    ratio_numerator: Decimal | None = None,
    ratio_denominator: Decimal | None = None,
    cash_amount: Decimal | None = None,
    currency: str | None = None,
    successor_instrument_id: str | None = None,
) -> CorporateAction:
    return CorporateAction(
        action_id=f"fixture-{action_type.value}-0001",
        action_type=action_type,
        instrument_id="us-eq-fixture-0001",
        announced_at=announced_at,
        effective_at=effective_at,
        ratio_numerator=ratio_numerator,
        ratio_denominator=ratio_denominator,
        cash_amount=cash_amount,
        currency=currency,
        successor_instrument_id=successor_instrument_id,
    )
