from __future__ import annotations

from dataclasses import dataclass
from typing import Final, assert_never

from trading_agent.daily_research_models import SessionQuality
from trading_agent.strategy_factory import StrategyMode

EVALUATOR_VERSION: Final = "paper_metrics_trade_bootstrap_v1"
FEED_ENTITLEMENT: Final = "KIS 상승률·거래량 상위 랭킹 읽기 전용; 전체 미국시장 PIT 모집단 아님"


@dataclass(frozen=True, slots=True)
class StrategyResearchContract:
    hypothesis_id: str
    hypothesis: str
    falsification_rule: str
    strategy_version: str
    parameter_set: tuple[str, ...]


def promotion_blockers(
    quality: SessionQuality,
    cumulative_days: int,
    cumulative_trades: int,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not quality.forward_day_eligible:
        blockers.append("data_quality_incomplete")
    if cumulative_days < 60:
        blockers.append(f"minimum_forward_days:{cumulative_days}/60")
    if cumulative_trades < 100:
        blockers.append(f"minimum_completed_trades:{cumulative_trades}/100")
    blockers.extend(
        (
            "broker_paper_ledger_missing",
            "block_bootstrap_missing",
            "dsr_pbo_missing",
            "parameter_plateau_missing",
            "sip_validation_missing",
        )
    )
    return tuple(blockers)


def strategy_contract(strategy: StrategyMode) -> StrategyResearchContract:
    common = (
        "scanner_min_gap_pct=0.04",
        "scanner_min_change_pct=0.04",
        "scanner_min_relative_volume=2.0",
        "scanner_price=1..200",
        "scanner_min_dollar_volume=500000",
        "scanner_max_spread_bps=100",
        "risk_max_loss_pct=0.05",
        "target_2r_multiple=2.0",
    )
    match strategy:
        case StrategyMode.ORB:
            return StrategyResearchContract(
                "H-MOM-ORB-001",
                "시점 가용 급등 후보의 첫 5분 고가를 거래량 확대로 돌파하면 당일 continuation이 발생한다.",
                "60거래일·100건 뒤 편도 20bp PF<1.15, 평균<=0 또는 CI 하한<0이면 기각한다.",
                "orb_5m_buffer5bp_volume1.5_v1",
                (*common, "range_minutes=5", "breakout_buffer_bps=5", "volume_multiplier=1.5"),
            )
        case StrategyMode.VWAP_RECLAIM:
            return StrategyResearchContract(
                "H-MOM-VWAP-001",
                "첫 VWAP 눌림 뒤 거래량 재확대 reclaim은 당일 continuation을 포착한다.",
                "ORB와 동일 기간·위험 비교에서 편도 20bp 평균과 PF가 유지되지 않으면 기각한다.",
                "first_vwap_reclaim_v1",
                (*common, "min_extension_pct=0.01", "touch_tolerance_bps=20", "volume_multiplier=1.2"),
            )
        case StrategyMode.HOD_BREAKOUT:
            return StrategyResearchContract(
                "H-MOM-HOD-001",
                "첫 HOD 뒤 2~8봉 base와 거래량 재확대 돌파는 당일 continuation을 포착한다.",
                "ORB와 동일 기간·위험 비교에서 편도 20bp 평균과 PF가 유지되지 않으면 기각한다.",
                "first_hod_volume_breakout_v1",
                (*common, "min_hod_gain_pct=0.03", "base_bars=2..8", "volume_multiplier=1.5"),
            )
        case StrategyMode.GAP_AND_GO:
            return StrategyResearchContract(
                "H-MOM-GAP-001",
                "첫 5분에 gap을 절반 이상 유지한 급등 후보는 당일 continuation을 보인다.",
                "지속·실패 분류가 편도 20bp 결과를 분리하지 못하면 기각한다.",
                "five_minute_gap_hold_v1",
                (*common, "opening_minutes=5", "min_gap_pct=0.04", "min_gap_retention=0.5"),
            )
        case unreachable:
            assert_never(unreachable)
