from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from pydantic import TypeAdapter, ValidationError

from trading_agent.cftc_tff_models import (
    CftcTffCategory,
    CftcTffCategoryPosition,
    CftcTffError,
    CftcTffPositioningContext,
    CftcTffProviderRow,
    CftcTffRawResponse,
    CftcTffRequest,
)

_ROWS = TypeAdapter(tuple[CftcTffProviderRow, CftcTffProviderRow])
_BPS_PRECISION = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class _NetInput:
    category: CftcTffCategory
    current_net: int
    previous_net: int
    current_open_interest: int


def parse_cftc_tff_context(
    request: CftcTffRequest,
    response: CftcTffRawResponse,
) -> CftcTffPositioningContext:
    try:
        if (
            response.request_id != request.request_id
            or response.status_code != 200
            or response.content_type != "application/json"
        ):
            raise CftcTffError
        current, previous = _ROWS.validate_json(response.raw_payload)
        if not _rows_valid(request, current, previous):
            raise CftcTffError
        categories = tuple(
            _position(item)
            for item in (
                _NetInput(
                    CftcTffCategory.DEALER,
                    current.dealer_positions_long_all - current.dealer_positions_short_all,
                    previous.dealer_positions_long_all - previous.dealer_positions_short_all,
                    current.open_interest_all,
                ),
                _NetInput(
                    CftcTffCategory.ASSET_MANAGER,
                    current.asset_mgr_positions_long - current.asset_mgr_positions_short,
                    previous.asset_mgr_positions_long - previous.asset_mgr_positions_short,
                    current.open_interest_all,
                ),
                _NetInput(
                    CftcTffCategory.LEVERAGED_MONEY,
                    current.lev_money_positions_long - current.lev_money_positions_short,
                    previous.lev_money_positions_long - previous.lev_money_positions_short,
                    current.open_interest_all,
                ),
                _NetInput(
                    CftcTffCategory.OTHER_REPORTABLE,
                    current.other_rept_positions_long - current.other_rept_positions_short,
                    previous.other_rept_positions_long - previous.other_rept_positions_short,
                    current.open_interest_all,
                ),
                _NetInput(
                    CftcTffCategory.NONREPORTABLE,
                    current.nonrept_positions_long_all - current.nonrept_positions_short_all,
                    previous.nonrept_positions_long_all - previous.nonrept_positions_short_all,
                    current.open_interest_all,
                ),
            )
        )
        return CftcTffPositioningContext(
            request_id=request.request_id,
            raw_receipt_id=response.receipt_id,
            contract_market_code=request.contract_market_code,
            market_and_exchange_name=current.market_and_exchange_names,
            contract_units=current.contract_units,
            latest_report_date=current.report_date,
            previous_report_date=previous.report_date,
            latest_open_interest=current.open_interest_all,
            previous_open_interest=previous.open_interest_all,
            observed_at=response.received_at,
            categories=categories,
        )
    except CftcTffError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise CftcTffError from None


def _rows_valid(
    request: CftcTffRequest,
    current: CftcTffProviderRow,
    previous: CftcTffProviderRow,
) -> bool:
    return (
        current.cftc_contract_market_code == previous.cftc_contract_market_code == request.contract_market_code
        and current.market_and_exchange_names == previous.market_and_exchange_names
        and current.contract_units == previous.contract_units
        and previous.report_date < current.report_date <= request.through_date
        and _reconciled(current)
        and _reconciled(previous)
    )


def _reconciled(row: CftcTffProviderRow) -> bool:
    long_total = (
        row.dealer_positions_long_all
        + row.dealer_positions_spread_all
        + row.asset_mgr_positions_long
        + row.asset_mgr_positions_spread
        + row.lev_money_positions_long
        + row.lev_money_positions_spread
        + row.other_rept_positions_long
        + row.other_rept_positions_spread
        + row.nonrept_positions_long_all
    )
    short_total = (
        row.dealer_positions_short_all
        + row.dealer_positions_spread_all
        + row.asset_mgr_positions_short
        + row.asset_mgr_positions_spread
        + row.lev_money_positions_short
        + row.lev_money_positions_spread
        + row.other_rept_positions_short
        + row.other_rept_positions_spread
        + row.nonrept_positions_short_all
    )
    return long_total == row.open_interest_all == short_total


def _position(item: _NetInput) -> CftcTffCategoryPosition:
    bps = (Decimal(item.current_net) * Decimal(10_000) / Decimal(item.current_open_interest)).quantize(
        _BPS_PRECISION, rounding=ROUND_HALF_EVEN
    )
    return CftcTffCategoryPosition(
        category=item.category,
        current_net=item.current_net,
        previous_net=item.previous_net,
        weekly_change=item.current_net - item.previous_net,
        current_net_bps=bps,
    )


__all__ = ("parse_cftc_tff_context",)
