from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trading_agent.dashboard_options_workbench_models import (
    OptionChainCellV2,
    OptionChainRowV2,
    OptionChainViewV2,
    OptionsWorkbenchV2,
    PromotionSummaryV2,
    StrategyLegV2,
    StrategyScenarioV2,
    WorkbenchSectionV2,
)

OVERLONG_DECIMAL = "1" * 33


def observed_at() -> datetime:
    return datetime(2026, 8, 3, 14, 30, tzinfo=UTC)


def call_cell() -> OptionChainCellV2:
    return OptionChainCellV2(
        contract_id="AAPL-20260821-C-00225000",
        side="call",
        provider="alpaca",
        state="current",
        bid="1.25",
        ask="1.30",
        last="1.28",
        implied_volatility="0.25",
        delta="0.50",
        gamma="0.01",
        theta="-0.02",
        vega="0.10",
        volume=100,
        open_interest=500,
        observed_at=observed_at(),
        trace_id="trace-1",
        selectable=True,
    )


def put_cell() -> OptionChainCellV2:
    return OptionChainCellV2(
        contract_id="AAPL-20260821-P-00225000",
        side="put",
        provider="alpaca",
        state="current",
        bid="1.10",
        ask="1.15",
        last="1.12",
        implied_volatility="0.26",
        delta="-0.48",
        gamma="0.01",
        theta="-0.02",
        vega="0.10",
        volume=80,
        open_interest=450,
        observed_at=observed_at(),
        trace_id="trace-2",
        selectable=True,
    )


def section() -> WorkbenchSectionV2:
    return WorkbenchSectionV2(
        state="populated",
        observed_at=observed_at(),
        blocker_code=None,
        summary="Ready",
        trace_id="section-trace",
    )


def chain(rows: tuple[OptionChainRowV2, ...] | None = None) -> OptionChainViewV2:
    populated_rows = rows if rows is not None else (OptionChainRowV2(strike="225", call=call_cell(), put=put_cell()),)
    return OptionChainViewV2(
        state="populated",
        observed_at=observed_at(),
        blocker_code=None,
        summary="Current option chain",
        trace_id="chain-trace",
        underlying="AAPL",
        selected_expiration="2026-08-21",
        expirations=("2026-08-21",),
        total_count=len(populated_rows),
        projected_count=len(populated_rows),
        truncated=False,
        rows=populated_rows,
    )


def scenario() -> StrategyScenarioV2:
    return StrategyScenarioV2(
        state="research_only",
        currency="USD",
        spot="225",
        legs=(
            StrategyLegV2(
                contract_id="AAPL-20260821-C-00225000",
                action="long",
                side="call",
                strike="225",
                premium="1.25",
                quantity=1,
                multiplier=100,
                trace_id="leg-trace",
            ),
        ),
        scenario_spots=("220", "225", "230"),
        trace_id="scenario-trace",
    )


def promotion() -> PromotionSummaryV2:
    return PromotionSummaryV2(
        promotion_id="promotion-1",
        state="approved",
        passed_gate_count=2,
        total_gate_count=2,
        blockers=(),
        trace_id="promotion-trace",
    )


def test_option_chain_row_accepts_same_strike_call_and_put() -> None:
    # Given: matching cells; When: parsed; Then: both sides remain available.
    row = OptionChainRowV2(strike="225", call=call_cell(), put=put_cell())
    assert row.strike == "225"


def test_option_chain_cell_rejects_selectable_stale_quote_with_exact_reason() -> None:
    # Given: stale selectable quote; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="selectable_quote_not_usable"):
        OptionChainCellV2.model_validate(call_cell().model_dump() | {"state": "stale"})


def test_option_chain_cell_rejects_quote_longer_than_32_characters() -> None:
    # Given: an oversized quote; When: parsed; Then: its numeric-string boundary rejects it.
    with pytest.raises(ValidationError):
        OptionChainCellV2.model_validate(call_cell().model_dump() | {"bid": OVERLONG_DECIMAL})


def test_option_chain_row_rejects_call_side_mismatch() -> None:
    # Given: put in call column; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="call_cell_side_mismatch"):
        OptionChainRowV2(strike="225", call=put_cell(), put=None)


def test_option_chain_row_rejects_put_side_mismatch() -> None:
    # Given: call in put column; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="put_cell_side_mismatch"):
        OptionChainRowV2(strike="225", call=None, put=call_cell())


def test_option_chain_row_rejects_empty_row() -> None:
    # Given: strike without cells; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="empty_chain_row"):
        OptionChainRowV2(strike="225", call=None, put=None)


def test_option_chain_rejects_more_than_41_rows() -> None:
    # Given: 42 rows; When: parsed; Then: length validation rejects them.
    rows = tuple(OptionChainRowV2(strike=str(index + 1), call=call_cell(), put=None) for index in range(42))
    with pytest.raises(ValidationError):
        chain(rows)


def test_option_chain_rejects_more_than_12_expirations() -> None:
    # Given: 13 expirations; When: parsed; Then: length validation rejects them.
    with pytest.raises(ValidationError):
        OptionChainViewV2.model_validate(
            chain().model_dump() | {"expirations": tuple(f"2026-08-{index:02d}" for index in range(1, 14))}
        )


def test_option_chain_rejects_count_and_truncation_mismatch() -> None:
    # Given: counts requiring truncation; When: false is supplied; Then: exact error is raised.
    with pytest.raises(ValidationError, match="chain_truncation_mismatch"):
        OptionChainViewV2.model_validate(
            chain().model_dump() | {"total_count": 2, "projected_count": 1, "truncated": False}
        )


def test_option_chain_rejects_unlisted_selected_expiration() -> None:
    # Given: absent selected expiration; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="selected_expiration_not_available"):
        OptionChainViewV2.model_validate(
            chain().model_dump() | {"selected_expiration": "2026-09-18"}
        )


def test_unavailable_option_chain_accepts_empty_rows() -> None:
    # Given: unavailable empty chain; When: parsed; Then: it remains a valid unavailable snapshot.
    assert OptionChainViewV2.model_validate(
        chain().model_dump()
        | {
            "state": "unavailable",
            "observed_at": None,
            "blocker_code": "provider_unavailable",
            "underlying": None,
            "selected_expiration": None,
            "expirations": (),
            "total_count": 0,
            "projected_count": 0,
            "rows": (),
        }
    ).rows == ()


def test_workbench_section_requires_blocker_for_non_usable_state() -> None:
    # Given: unavailable section without blocker; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="section_blocker_required"):
        WorkbenchSectionV2(
            state="unavailable", observed_at=None, blocker_code=None, summary="Unavailable", trace_id="section-trace"
        )


def test_workbench_section_rejects_blocker_for_usable_state() -> None:
    # Given: populated section with blocker; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="section_blocker_forbidden"):
        WorkbenchSectionV2(
            state="populated",
            observed_at=observed_at(),
            blocker_code="provider_unavailable",
            summary="Ready",
            trace_id="section-trace",
        )


@pytest.mark.parametrize("scenario_spots", (("225", "220"), ("220", "220")))
def test_strategy_scenario_rejects_unsorted_or_duplicate_spots(scenario_spots: tuple[str, str]) -> None:
    # Given: unordered or duplicate spots; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="scenario_spots_not_strictly_ascending"):
        StrategyScenarioV2(
            state="research_only",
            currency="USD",
            spot="225",
            legs=scenario().legs,
            scenario_spots=scenario_spots,
            trace_id="scenario-trace",
        )


def test_strategy_leg_rejects_numeric_string_longer_than_32_characters() -> None:
    # Given: an oversized strategy premium; When: parsed; Then: its numeric-string boundary rejects it.
    with pytest.raises(ValidationError):
        StrategyLegV2.model_validate(scenario().legs[0].model_dump() | {"premium": OVERLONG_DECIMAL})


def test_strategy_scenario_rejects_numeric_string_longer_than_32_characters() -> None:
    # Given: an oversized scenario spot; When: parsed; Then: its numeric-string boundary rejects it.
    with pytest.raises(ValidationError):
        StrategyScenarioV2.model_validate(scenario().model_dump() | {"spot": OVERLONG_DECIMAL})


def test_promotion_rejects_impossible_gate_counts() -> None:
    # Given: passed gates exceed total; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="promotion_passed_gate_count_exceeds_total"):
        PromotionSummaryV2(
            promotion_id="promotion-1",
            state="held",
            passed_gate_count=3,
            total_gate_count=2,
            blockers=("risk_review",),
            trace_id="promotion-trace",
        )


def test_promotion_rejects_nonapproved_state_without_blocker() -> None:
    # Given: held promotion without blocker; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="promotion_blocker_required"):
        PromotionSummaryV2(
            promotion_id="promotion-1",
            state="held",
            passed_gate_count=1,
            total_gate_count=2,
            blockers=(),
            trace_id="promotion-trace",
        )


def test_promotion_rejects_approved_state_with_incomplete_gates() -> None:
    # Given: incomplete approved promotion; When: parsed; Then: exact error is raised.
    with pytest.raises(ValidationError, match="promotion_approved_incomplete"):
        PromotionSummaryV2(
            promotion_id="promotion-1",
            state="approved",
            passed_gate_count=1,
            total_gate_count=2,
            blockers=(),
            trace_id="promotion-trace",
        )


def test_options_workbench_accepts_fully_valid_snapshot() -> None:
    # Given: valid panel snapshots; When: aggregate is parsed; Then: selected view is retained.
    workbench = OptionsWorkbenchV2(
        schema_version=1,
        selected_view="option_chain",
        market=section(),
        chain=chain(),
        scenario=scenario(),
        agent=section(),
        experiment=section(),
        promotions=(promotion(),),
    )
    assert workbench.selected_view == "option_chain"


def test_options_workbench_rejects_extra_fields() -> None:
    # Given: an undeclared field; When: aggregate is parsed; Then: extra-field validation rejects it.
    with pytest.raises(ValidationError):
        OptionsWorkbenchV2.model_validate_json(
            '{"schema_version":1,"selected_view":"option_chain",'
            '"market":{"state":"populated","observed_at":"2026-08-03T14:30:00Z",'
            '"blocker_code":null,"summary":"Ready","trace_id":"market-trace"},'
            '"chain":{"state":"unavailable","observed_at":null,"blocker_code":"provider_unavailable",'
            '"summary":"Unavailable","trace_id":"chain-trace","underlying":null,'
            '"selected_expiration":null,"expirations":[],"total_count":0,"projected_count":0,'
            '"truncated":false,"rows":[]},"scenario":null,'
            '"agent":{"state":"populated","observed_at":"2026-08-03T14:30:00Z",'
            '"blocker_code":null,"summary":"Ready","trace_id":"agent-trace"},'
            '"experiment":{"state":"populated","observed_at":"2026-08-03T14:30:00Z",'
            '"blocker_code":null,"summary":"Ready","trace_id":"experiment-trace"},'
            '"promotions":[],"unexpected":true}'
        )
