from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
    KisKrMinuteProjectionInput,
    KisKrSnapshotProjectionInput,
)
from trading_agent.kis_kr_market_projection import (
    project_kis_kr_completed_minutes,
    project_kis_kr_market_snapshot,
)
from trading_agent.kr_intraday_market_gate import (
    KrIntradayGateReason,
    assess_kr_shadow_entry,
)
from trading_agent.kr_theme_day_setup import KrThemeDaySetupInput, derive_kr_theme_day_setup
from trading_agent.kr_theme_day_signal import project_kr_theme_day_shadow_signal
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

SEOUL = dt.timezone(dt.timedelta(hours=9))
SESSION = dt.datetime(2026, 7, 20, 9, 0, tzinfo=SEOUL)


def test_kis_raw_receipts_project_completed_bars_and_current_shadow_signal() -> None:
    evaluated_at = SESSION + dt.timedelta(minutes=4, seconds=4)
    bars = project_kis_kr_completed_minutes(
        KisKrMinuteProjectionInput(
            receipts=(_receipt(KisKrMarketReceiptKind.MINUTE_BARS, _minute_body(), seconds=2),),
            evaluated_at=evaluated_at,
        )
    )

    assert len(bars) == 4
    assert bars[-1].start_at == SESSION + dt.timedelta(minutes=3)
    assert tuple(bar.trading_value_krw for bar in bars) == (
        Decimal("10000"),
        Decimal("10100"),
        Decimal("10080"),
        Decimal("18360"),
    )
    setup = derive_kr_theme_day_setup(
        KrThemeDaySetupInput(
            opportunity=_opportunity(),
            bars=bars,
            producer_strategy_version="kr-theme-leader-vwap-reclaim-v1",
            evaluated_at=evaluated_at,
            max_slippage_bps=Decimal("20"),
        )
    )
    assert setup is not None

    market = project_kis_kr_market_snapshot(
        KisKrSnapshotProjectionInput(
            price_receipt=_receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=2),
            quote_receipt=_receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=3),
            evaluated_at=evaluated_at,
        )
    )
    decision = project_kr_theme_day_shadow_signal(
        _opportunity(),
        market,
        setup,
        evaluated_at=evaluated_at,
    )

    assert market.bid_price == Decimal("102.9")
    assert market.ask_price == Decimal("103")
    assert decision.signal is not None
    assert decision.signal.entry_price == Decimal("103")


def test_unrecognized_vi_code_is_preserved_as_unknown_and_blocked() -> None:
    market = project_kis_kr_market_snapshot(
        KisKrSnapshotProjectionInput(
            price_receipt=_receipt(
                KisKrMarketReceiptKind.PRICE_STATUS,
                _price_body(vi_code="9"),
                seconds=2,
            ),
            quote_receipt=_receipt(
                KisKrMarketReceiptKind.ORDER_BOOK,
                _quote_body(vi_code="9"),
                seconds=3,
            ),
            evaluated_at=SESSION + dt.timedelta(minutes=4, seconds=4),
        )
    )

    gate = assess_kr_shadow_entry(market, SESSION + dt.timedelta(minutes=4, seconds=4))

    assert gate.reasons == (KrIntradayGateReason.VI_UNKNOWN,)


@pytest.mark.parametrize("case", ("gap", "forming_only", "receipt_skew", "symbol_mismatch"))
def test_invalid_or_noncausal_kis_receipts_are_rejected(case: str) -> None:
    evaluated_at = SESSION + dt.timedelta(minutes=4, seconds=4)
    if case == "gap":
        body = _minute_body(excluded_hour="090200")
        with pytest.raises(ValueError, match="KIS KR market evidence is invalid"):
            _ = project_kis_kr_completed_minutes(
                KisKrMinuteProjectionInput(
                    receipts=(_receipt(KisKrMarketReceiptKind.MINUTE_BARS, body, seconds=2),),
                    evaluated_at=evaluated_at,
                )
            )
    elif case == "forming_only":
        receipt = _receipt(KisKrMarketReceiptKind.MINUTE_BARS, _minute_body(), seconds=2)
        with pytest.raises(ValueError, match="KIS KR market evidence is invalid"):
            _ = project_kis_kr_completed_minutes(
                KisKrMinuteProjectionInput(
                    receipts=(receipt,),
                    evaluated_at=SESSION + dt.timedelta(seconds=30),
                )
            )
    else:
        quote_seconds = 10 if case == "receipt_skew" else 3
        quote_symbol = "000660" if case == "symbol_mismatch" else "005930"
        with pytest.raises(ValueError, match="KIS KR market evidence is invalid"):
            _ = project_kis_kr_market_snapshot(
                KisKrSnapshotProjectionInput(
                    price_receipt=_receipt(
                        KisKrMarketReceiptKind.PRICE_STATUS,
                        _price_body(),
                        seconds=2,
                    ),
                    quote_receipt=_receipt(
                        KisKrMarketReceiptKind.ORDER_BOOK,
                        _quote_body(symbol=quote_symbol),
                        seconds=quote_seconds,
                    ),
                    evaluated_at=SESSION + dt.timedelta(minutes=4, seconds=11),
                )
            )


def _receipt(kind: KisKrMarketReceiptKind, payload: bytes, *, seconds: int) -> KisKrMarketReceipt:
    return KisKrMarketReceipt(
        kind=kind,
        symbol="005930",
        received_at=SESSION + dt.timedelta(minutes=4, seconds=seconds),
        status_code=200,
        content_type="application/json",
        raw_payload=payload,
    )


def _minute_body(*, excluded_hour: str | None = None) -> bytes:
    rows = (
        _minute_row("090000", "100", "101", "99", "101", "100", "10000"),
        _minute_row("090100", "101", "103", "100", "102", "100", "20100"),
        _minute_row("090200", "102", "102", "100", "100.8", "100", "30180"),
        _minute_row("090300", "101", "104", "101", "103", "180", "48540"),
        _minute_row("090400", "103", "104", "102", "103", "10", "49570"),
    )
    output = [row for row in reversed(rows) if row["stck_cntg_hour"] != excluded_hour]
    return _json_body({"output1": {}, "output2": output})


def _minute_row(
    hour: str,
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: str,
    cumulative_value: str,
) -> dict[str, str]:
    return {
        "stck_bsop_date": "20260720",
        "stck_cntg_hour": hour,
        "stck_prpr": close,
        "stck_oprc": open_price,
        "stck_hgpr": high,
        "stck_lwpr": low,
        "cntg_vol": volume,
        "acml_tr_pbmn": cumulative_value,
    }


def _price_body(*, vi_code: str = "N") -> bytes:
    return _json_body(
        {
            "output": {
                "stck_shrn_iscd": "005930",
                "stck_prpr": "103",
                "stck_sdpr": "95",
                "stck_mxpr": "123.5",
                "stck_llam": "66.5",
                "temp_stop_yn": "N",
                "vi_cls_code": vi_code,
                "invt_caful_yn": "N",
                "mrkt_warn_cls_code": "00",
                "short_over_yn": "N",
                "sltr_yn": "N",
                "mang_issu_cls_code": "00",
            }
        }
    )


def _quote_body(*, symbol: str = "005930", vi_code: str = "N") -> bytes:
    return _json_body(
        {
            "output1": {
                "aspr_acpt_hour": "090403",
                "askp1": "103",
                "bidp1": "102.9",
                "new_mkop_cls_code": "20",
            },
            "output2": {
                "stck_shrn_iscd": symbol,
                "antc_mkop_cls_code": "00",
                "stck_prpr": "103",
                "stck_sdpr": "95",
                "vi_cls_code": vi_code,
            },
        }
    )


def _json_body(outputs: dict[str, dict[str, str] | list[dict[str, str]]]) -> bytes:
    return json.dumps(
        {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok", **outputs},
        separators=(",", ":"),
    ).encode()


def _opportunity() -> OpportunitySnapshot:
    observed = SESSION - dt.timedelta(minutes=1)
    return OpportunitySnapshot(
        opportunity_id="KR-THEME-OPPORTUNITY-001",
        strategy_lane=KR_THEME_OPPORTUNITY_LANE,
        producer_strategy_version="kr-theme-manager-v1",
        observed_at=observed,
        valid_until=SESSION + dt.timedelta(minutes=10),
        candidates=(
            OpportunityCandidate(
                symbol="005930",
                rank=1,
                score=Decimal("100"),
                features=(FeatureValue(name="theme_name", value="semiconductor"),),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="kr/theme/state", record_id="theme-1", observed_at=observed),),
        source_coverage=(SourceCoverage(source_id="kr_theme", observed_at=observed, record_count=1, complete=True),),
    )
