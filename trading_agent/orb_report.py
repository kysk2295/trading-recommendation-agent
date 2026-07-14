from __future__ import annotations

from pathlib import Path
from typing import Final, assert_never

from trading_agent.metrics import (
    MetricsConfig,
    PaperTrade,
    summarize_performance,
)
from trading_agent.models import RecommendationState
from trading_agent.orb_analysis import TRADE_STATUSES
from trading_agent.orb_flatness import (
    OrbMetricPoint,
    analyze_orb_flatness,
    flatness_fields,
    flatness_row,
)
from trading_agent.orb_models import OrbOutcome, OrbOutcomeStatus, OrbTestConfig
from trading_agent.orb_report_rows import (
    CsvRow,
    config_fields,
    config_row,
    metric_fields,
    metric_row,
    outcome_fields,
    outcome_row,
    parameter_fields,
    sample_counts,
    trade_fields,
    trade_row,
    write_rows,
)

SIDE_COSTS_BPS: Final = (5, 10, 20)
BOOTSTRAP_SAMPLES: Final = 2_000
BOOTSTRAP_SEED: Final = 20_260_713
PERIODS: Final = ("pre_2025", "2025_plus")


class InvalidOrbTradeError(RuntimeError):
    pass


def write_orb_report(
    output_dir: Path,
    outcomes: tuple[OrbOutcome, ...],
    configs: tuple[OrbTestConfig, ...] = (),
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_rows(
        output_dir / "orb_outcomes.csv",
        outcome_fields(),
        tuple(outcome_row(row) for row in outcomes),
    )
    groups = _group_by_config(outcomes, configs)
    parameter_rows: list[CsvRow] = []
    yearly_rows: list[CsvRow] = []
    period_rows: list[CsvRow] = []
    metric_points: list[OrbMetricPoint] = []
    trades: list[PaperTrade] = []
    for config, rows in groups:
        config_trades = tuple(
            _paper_trade(row) for row in rows if row.portfolio_selected and row.status in TRADE_STATUSES
        )
        trades.extend(config_trades)
        for cost in SIDE_COSTS_BPS:
            metrics = summarize_performance(
                config_trades,
                MetricsConfig(cost, BOOTSTRAP_SAMPLES, _seed(config, cost)),
            )
            metric_points.append(OrbMetricPoint(config, metrics))
            parameter_rows.append(
                {
                    **config_row(config),
                    **sample_counts(rows),
                    **metric_row(metrics),
                }
            )
        for year in sorted({trade.exit_at.year for trade in config_trades}):
            year_trades = tuple(trade for trade in config_trades if trade.exit_at.year == year)
            for cost in SIDE_COSTS_BPS:
                metrics = summarize_performance(
                    year_trades,
                    MetricsConfig(cost, BOOTSTRAP_SAMPLES, _seed(config, cost) + year),
                )
                yearly_rows.append(
                    {
                        **config_row(config),
                        "year": year,
                        **metric_row(metrics),
                    }
                )
        for period in PERIODS:
            period_trades = tuple(
                trade for trade in config_trades if (trade.exit_at.year < 2025) == (period == "pre_2025")
            )
            for cost in SIDE_COSTS_BPS:
                metrics = summarize_performance(
                    period_trades,
                    MetricsConfig(
                        cost,
                        BOOTSTRAP_SAMPLES,
                        _seed(config, cost) + (2_024 if period == "pre_2025" else 2_025),
                    ),
                )
                period_rows.append(
                    {
                        **config_row(config),
                        "period": period,
                        **metric_row(metrics),
                    }
                )
    write_rows(
        output_dir / "orb_parameter_results.csv",
        parameter_fields(),
        tuple(parameter_rows),
    )
    write_rows(
        output_dir / "orb_yearly_results.csv",
        (*config_fields(), "year", *metric_fields()),
        tuple(yearly_rows),
    )
    write_rows(
        output_dir / "orb_period_results.csv",
        (*config_fields(), "period", *metric_fields()),
        tuple(period_rows),
    )
    flatness = analyze_orb_flatness(tuple(metric_points))
    write_rows(
        output_dir / "orb_flatness_results.csv",
        flatness_fields(),
        tuple(flatness_row(row) for row in flatness),
    )
    trade_rows = tuple(trade_row(row) for row in trades)
    write_rows(
        output_dir / "orb_trades.csv",
        tuple(trade_rows[0]) if trade_rows else trade_fields(),
        trade_rows,
    )
    complete = sum(row.complete for row in outcomes)
    selected = sum(row.portfolio_selected for row in outcomes)
    lines = (
        "# ORB Forward Paper 성과 진단",
        "",
        "> 확정 수익이나 3년 백테스트가 아닌 KIS 전진수집 진단입니다.",
        "",
        f"- 파라미터·종목 outcome: {len(outcomes)}건",
        f"- 완료 세션 outcome: {complete}건",
        f"- 최대 10포지션 규칙으로 사전 선택된 거래: {selected}건",
        "- 파라미터: OR 1/5/15분, 거래량 1.0/1.5/2.0배, 손절폭 0.75/1.0/1.25배, 목표 1R/2R/3R",
        "- 비용: 진입·청산 편도 5/10/20bp",
        "- 기간분리: 2025년 이전과 2025년 이후를 모든 설정·비용에 대해 별도 기록",
        "- 평탄성: 한 축만 한 단계 다른 이웃 중 최소 4개·75% 양수·최악 평균수익 양수를 별도 표시",
        "",
        "## 실행 규칙",
        "",
        "- 랭킹 선택 뒤 실제 분봉 조회가 끝난 시각 이후에만 신호를 인정합니다.",
        "- 조건부 진입은 신호 관찰 다음 완전한 1분봉부터 허용합니다.",
        "- 같은 봉에서 손절과 목표가 함께 닿으면 손절을 먼저 적용합니다.",
        "- 동시 보유는 신호 시점의 상승률·거래대금 순으로 최대 10개입니다.",
        "",
        "## 해석 제한",
        "",
        "- 완료 세션만 거래 성과에 포함하고 중도절단은 수익 0으로 바꾸지 않습니다.",
        "- 81개 파라미터와 3개 비용을 비교하므로 다중검정·데이터 스누핑 위험이 큽니다.",
        "- KIS 랭킹 상위 표본이며 전체 미국시장 PIT 모집단이 아닙니다.",
        "- 역사 NBBO·halt/LULD를 재현한 3년 백테스트가 아닙니다.",
    )
    _ = (output_dir / "orb_forward_report_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _group_by_config(
    outcomes: tuple[OrbOutcome, ...],
    configs: tuple[OrbTestConfig, ...],
) -> tuple[tuple[OrbTestConfig, tuple[OrbOutcome, ...]], ...]:
    groups: dict[OrbTestConfig, list[OrbOutcome]] = {config: [] for config in configs}
    for row in outcomes:
        groups.setdefault(row.config, []).append(row)
    return tuple((config, tuple(rows)) for config, rows in groups.items())


def _paper_trade(row: OrbOutcome) -> PaperTrade:
    if (
        row.entry_at is None
        or row.exit_at is None
        or row.entry is None
        or row.exit_price is None
        or row.gross_return is None
    ):
        raise InvalidOrbTradeError(row.status)
    match row.status:
        case OrbOutcomeStatus.STOPPED:
            state = RecommendationState.STOPPED
        case OrbOutcomeStatus.TARGET:
            state = RecommendationState.TARGET_2R
        case OrbOutcomeStatus.TIME_EXIT:
            state = RecommendationState.TIME_EXIT
        case (
            OrbOutcomeStatus.CENSORED
            | OrbOutcomeStatus.NO_SIGNAL
            | OrbOutcomeStatus.RISK_REJECTED
            | OrbOutcomeStatus.NO_ENTRY
            | OrbOutcomeStatus.INVALIDATED
        ):
            raise InvalidOrbTradeError(row.status)
        case unreachable:
            assert_never(unreachable)
    identifier = (
        f"orb:{row.config.range_minutes}:{row.config.volume_multiplier}:"
        f"{row.config.stop_multiple}:{row.config.target_r}:"
        f"{row.symbol}:{row.entry_at.isoformat()}"
    )
    return PaperTrade(
        identifier,
        row.symbol,
        "opening_range_breakout",
        row.entry_at,
        row.exit_at,
        row.entry,
        row.exit_price,
        row.gross_return,
        state,
        row.status is OrbOutcomeStatus.TIME_EXIT,
    )


def _seed(config: OrbTestConfig, cost: int) -> int:
    return (
        BOOTSTRAP_SEED
        + config.range_minutes * 100_000
        + int(config.volume_multiplier * 100) * 1_000
        + int(config.stop_multiple * 100) * 10
        + int(config.target_r * 10)
        + cost
    )
