from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Final, Literal, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_IDENTIFIER_PATTERN: Final = r"^[a-zA-Z0-9_.:-]{1,100}$"
_SAFE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"
_DECIMAL_PATTERN: Final = r"^-?[0-9]{1,6}(?:\.[0-9]{1,8})?$"
_NONNEGATIVE_DECIMAL_PATTERN: Final = r"^[0-9]{1,6}(?:\.[0-9]{1,8})?$"
_ISO_DATE_PATTERN: Final = r"^\d{4}-\d{2}-\d{2}$"

type Identifier = Annotated[str, Field(pattern=_IDENTIFIER_PATTERN, min_length=1, max_length=100)]
type SafeCode = Annotated[str, Field(pattern=_SAFE_CODE_PATTERN, min_length=1, max_length=64)]
type DecimalString = Annotated[str, Field(pattern=_DECIMAL_PATTERN, max_length=16)]
type NonnegativeDecimalString = Annotated[str, Field(pattern=_NONNEGATIVE_DECIMAL_PATTERN, max_length=15)]
type IsoDateString = Annotated[str, Field(pattern=_ISO_DATE_PATTERN)]


class InvalidOptionsWorkbenchError(ValueError):
    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__()

    def __str__(self) -> str:
        return self.reason


class StrictOptionsWorkbenchModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class OptionChainCellV2(StrictOptionsWorkbenchModel):
    contract_id: Identifier
    side: Literal["call", "put"]
    provider: Literal["alpaca", "kis", "ls"]
    state: Literal["indicative", "delayed", "current", "stale", "blocked", "unavailable"]
    bid: DecimalString | None = None
    ask: DecimalString | None = None
    last: DecimalString | None = None
    implied_volatility: DecimalString | None = None
    delta: DecimalString | None = None
    gamma: DecimalString | None = None
    theta: DecimalString | None = None
    vega: DecimalString | None = None
    volume: Annotated[int, Field(ge=0)] | None = None
    open_interest: Annotated[int, Field(ge=0)] | None = None
    observed_at: AwareDatetime | None
    trace_id: Identifier
    selectable: bool

    @model_validator(mode="after")
    def validate_selectable_state(self) -> "OptionChainCellV2":
        match self.state:
            case "indicative" | "delayed" | "current":
                quote_usable = True
            case "stale" | "blocked" | "unavailable":
                quote_usable = False
            case unreachable:
                assert_never(unreachable)
        if self.selectable and not quote_usable:
            raise InvalidOptionsWorkbenchError(reason="selectable_quote_not_usable")
        return self


class OptionChainRowV2(StrictOptionsWorkbenchModel):
    strike: NonnegativeDecimalString
    call: OptionChainCellV2 | None
    put: OptionChainCellV2 | None

    @model_validator(mode="after")
    def validate_cells(self) -> "OptionChainRowV2":
        if Decimal(self.strike) <= Decimal("0"):
            raise InvalidOptionsWorkbenchError(reason="chain_row_strike_not_positive")
        if self.call is None and self.put is None:
            raise InvalidOptionsWorkbenchError(reason="empty_chain_row")
        if self.call is not None and self.call.side != "call":
            raise InvalidOptionsWorkbenchError(reason="call_cell_side_mismatch")
        if self.put is not None and self.put.side != "put":
            raise InvalidOptionsWorkbenchError(reason="put_cell_side_mismatch")
        return self


class WorkbenchSectionV2(StrictOptionsWorkbenchModel):
    state: Literal["empty", "error", "blocked", "unavailable", "corrupt", "stale", "populated", "loading"]
    observed_at: AwareDatetime | None
    blocker_code: SafeCode | None
    summary: Annotated[str, Field(min_length=1, max_length=160)]
    trace_id: Identifier

    @model_validator(mode="after")
    def validate_section_state(self) -> "WorkbenchSectionV2":
        match self.state:
            case "empty" | "populated":
                blocker_required = False
                loading = False
            case "loading":
                blocker_required = False
                loading = True
            case "error" | "blocked" | "unavailable" | "corrupt" | "stale":
                blocker_required = True
                loading = False
            case unreachable:
                assert_never(unreachable)
        match self.state:
            case "populated" | "stale":
                observation_required = True
            case "empty" | "error" | "blocked" | "unavailable" | "corrupt" | "loading":
                observation_required = False
            case unreachable:
                assert_never(unreachable)
        if loading and (self.observed_at is not None or self.blocker_code is not None):
            raise InvalidOptionsWorkbenchError(reason="loading_section_metadata_forbidden")
        if blocker_required and self.blocker_code is None:
            raise InvalidOptionsWorkbenchError(reason="section_blocker_required")
        if not blocker_required and self.blocker_code is not None:
            raise InvalidOptionsWorkbenchError(reason="section_blocker_forbidden")
        if observation_required and self.observed_at is None:
            raise InvalidOptionsWorkbenchError(reason="section_observed_at_required")
        return self


class OptionChainViewV2(WorkbenchSectionV2):
    underlying: Annotated[str, Field(pattern=_IDENTIFIER_PATTERN, min_length=1, max_length=100)] | None
    selected_expiration: IsoDateString | None
    expirations: Annotated[tuple[IsoDateString, ...], Field(max_length=12)]
    total_count: Annotated[int, Field(ge=0)]
    projected_count: Annotated[int, Field(ge=0)]
    truncated: bool
    rows: Annotated[tuple[OptionChainRowV2, ...], Field(max_length=41)]

    @model_validator(mode="after")
    def validate_chain_projection(self) -> "OptionChainViewV2":
        for expiration in self.expirations:
            try:
                date.fromisoformat(expiration)
            except ValueError:
                raise InvalidOptionsWorkbenchError(reason="invalid_expiration_date") from None
        if self.selected_expiration is not None:
            try:
                date.fromisoformat(self.selected_expiration)
            except ValueError:
                raise InvalidOptionsWorkbenchError(reason="invalid_selected_expiration_date") from None
        if self.projected_count != len(self.rows):
            raise InvalidOptionsWorkbenchError(reason="chain_projected_count_mismatch")
        if self.total_count < self.projected_count:
            raise InvalidOptionsWorkbenchError(reason="chain_total_count_below_projected")
        if self.truncated != (self.total_count > self.projected_count):
            raise InvalidOptionsWorkbenchError(reason="chain_truncation_mismatch")
        if self.selected_expiration is not None and self.selected_expiration not in self.expirations:
            raise InvalidOptionsWorkbenchError(reason="selected_expiration_not_available")
        return self


class StrategyLegV2(StrictOptionsWorkbenchModel):
    contract_id: Identifier
    action: Literal["long", "short"]
    side: Literal["call", "put"]
    strike: NonnegativeDecimalString
    premium: NonnegativeDecimalString
    quantity: Annotated[int, Field(gt=0, le=100_000)]
    multiplier: Annotated[int, Field(gt=0, le=100_000)]
    trace_id: Identifier

    @model_validator(mode="after")
    def validate_prices(self) -> "StrategyLegV2":
        if Decimal(self.strike) <= Decimal("0"):
            raise InvalidOptionsWorkbenchError(reason="strategy_leg_strike_not_positive")
        return self


class StrategyScenarioV2(StrictOptionsWorkbenchModel):
    state: Literal["research_only"]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$", min_length=3, max_length=3)]
    spot: NonnegativeDecimalString
    legs: Annotated[tuple[StrategyLegV2, ...], Field(min_length=1, max_length=8)]
    scenario_spots: Annotated[tuple[NonnegativeDecimalString, ...], Field(min_length=2, max_length=41)]
    trace_id: Identifier

    @model_validator(mode="after")
    def validate_scenario_spots(self) -> "StrategyScenarioV2":
        decimal_spots = tuple(Decimal(spot) for spot in self.scenario_spots)
        if any(left >= right for left, right in pairwise(decimal_spots)):
            raise InvalidOptionsWorkbenchError(reason="scenario_spots_not_strictly_ascending")
        return self


class PromotionSummaryV2(StrictOptionsWorkbenchModel):
    promotion_id: Identifier
    state: Literal["held", "approved", "rejected", "demoted"]
    passed_gate_count: Annotated[int, Field(ge=0)]
    total_gate_count: Annotated[int, Field(ge=1)]
    blockers: Annotated[tuple[SafeCode, ...], Field(max_length=20)]
    trace_id: Identifier

    @model_validator(mode="after")
    def validate_promotion(self) -> "PromotionSummaryV2":
        if self.passed_gate_count > self.total_gate_count:
            raise InvalidOptionsWorkbenchError(reason="promotion_passed_gate_count_exceeds_total")
        match self.state:
            case "approved":
                approved = True
            case "held" | "rejected" | "demoted":
                approved = False
            case unreachable:
                assert_never(unreachable)
        if approved and self.passed_gate_count != self.total_gate_count:
            raise InvalidOptionsWorkbenchError(reason="promotion_approved_incomplete")
        if approved and self.blockers:
            raise InvalidOptionsWorkbenchError(reason="promotion_approved_has_blockers")
        if not approved and not self.blockers:
            raise InvalidOptionsWorkbenchError(reason="promotion_blocker_required")
        return self


class OptionsWorkbenchV2(StrictOptionsWorkbenchModel):
    schema_version: Literal[1]
    selected_view: Literal["market_pulse", "option_chain", "strategy_agent", "experiment_lab", "promotion_operations"]
    market: WorkbenchSectionV2
    chain: OptionChainViewV2
    scenario: StrategyScenarioV2 | None
    agent: WorkbenchSectionV2
    experiment: WorkbenchSectionV2
    promotions: Annotated[tuple[PromotionSummaryV2, ...], Field(max_length=20)]
