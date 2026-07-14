from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Final

from trading_agent.forward_outcomes import ForwardOutcome

CHANGE_THRESHOLDS: Final = (0.04, 0.06, 0.08, 0.10)
DOLLAR_THRESHOLDS: Final = (500_000.0, 1_000_000.0, 2_000_000.0, 5_000_000.0)
SIDE_COSTS_BPS: Final = (5, 10, 20)
BOOTSTRAP_SAMPLES: Final = 2_000
CsvValue = str | int | float | bool | None
CsvRow = dict[str, CsvValue]


def write_forward_report(
    output_dir: Path,
    outcomes: tuple[ForwardOutcome, ...],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "scanner_forward_outcomes.csv",
        _outcome_fields(),
        tuple(_outcome_row(row) for row in outcomes),
    )
    summaries = tuple(
        _threshold_row(outcomes, change, dollars)
        for change in CHANGE_THRESHOLDS
        for dollars in DOLLAR_THRESHOLDS
    )
    _write_csv(
        output_dir / "scanner_threshold_summary.csv",
        tuple(summaries[0]),
        summaries,
    )
    complete_count = sum(row.complete for row in outcomes)
    lines = (
        "# 급등 스캐너 Forward Outcome 진단",
        "",
        "> 확정 수익이나 3년 백테스트가 아닌 KIS forward paper 진단입니다.",
        "",
        f"- 전체 선택 관찰: {len(outcomes)}건",
        f"- 장 마감까지 완전한 경로: {complete_count}건",
        f"- 중도절단·미관측 경로: {len(outcomes) - complete_count}건",
        "- 체결 기준: 스캔 뒤 다음 완전한 1분봉 시가",
        "- 표본 단위: 종목·거래일의 최초 실제 선택 1건",
        "- 인접 임계값: 등락률 4/6/8/10%, 거래대금 0.5/1/2/5백만 달러",
        "- 비용: 진입·청산 편도 5/10/20bp",
        "",
        "## 해석 제한",
        "",
        "- 완료 세션만 수익·MFE·MAE와 bootstrap CI에 포함합니다.",
        "- 중도절단 경로를 성과 0으로 간주하거나 제외 사실을 숨기지 않습니다.",
        "- KIS 랭킹 상위 표본이며 미국 전체 종목 point-in-time 모집단이 아닙니다.",
        "- 16개 격자를 반복 비교하므로 다중검정·데이터 스누핑 위험이 있습니다.",
        "- 실제 bid-ask 체결이나 halt를 재현한 전략 백테스트가 아닙니다.",
    )
    _ = (output_dir / "scanner_forward_report_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _threshold_row(
    outcomes: tuple[ForwardOutcome, ...],
    min_change: float,
    min_dollars: float,
) -> CsvRow:
    selected = tuple(
        row
        for row in outcomes
        if row.complete
        and row.eod_return is not None
        and row.change_pct >= min_change
        and row.dollar_volume >= min_dollars
    )
    returns = tuple(row.eod_return for row in selected if row.eod_return is not None)
    mean = _mean(returns)
    ci_low, ci_high = _bootstrap_ci(
        returns,
        int(min_change * 10_000) + int(min_dollars),
    )
    return {
        "min_change_pct": min_change,
        "min_dollar_volume": min_dollars,
        "complete_count": len(selected),
        "win_rate": None if not returns else sum(value > 0.0 for value in returns) / len(returns),
        "average_eod_return": mean,
        "mean_ci_low": ci_low,
        "mean_ci_high": ci_high,
        "average_mfe": _mean(tuple(row.mfe for row in selected if row.mfe is not None)),
        "average_mae": _mean(tuple(row.mae for row in selected if row.mae is not None)),
        "average_eod_net_5bp": _net_mean(returns, 5),
        "average_eod_net_10bp": _net_mean(returns, 10),
        "average_eod_net_20bp": _net_mean(returns, 20),
    }


def _outcome_fields() -> tuple[str, ...]:
    return (
        "observed_at",
        "exchange",
        "symbol",
        "scanner_price",
        "change_pct",
        "spread_bps",
        "dollar_volume",
        "entry_at",
        "entry",
        "bar_count",
        "complete",
        "return_5m",
        "return_15m",
        "return_30m",
        "eod_return",
        "mfe",
        "mae",
    )


def _outcome_row(row: ForwardOutcome) -> CsvRow:
    return {
        "observed_at": row.observed_at.isoformat(),
        "exchange": row.exchange,
        "symbol": row.symbol,
        "scanner_price": row.scanner_price,
        "change_pct": row.change_pct,
        "spread_bps": row.spread_bps,
        "dollar_volume": row.dollar_volume,
        "entry_at": None if row.entry_at is None else row.entry_at.isoformat(),
        "entry": row.entry,
        "bar_count": row.bar_count,
        "complete": row.complete,
        "return_5m": row.return_5m,
        "return_15m": row.return_15m,
        "return_30m": row.return_30m,
        "eod_return": row.eod_return,
        "mfe": row.mfe,
        "mae": row.mae,
    }


def _write_csv(
    path: Path,
    fields: tuple[str, ...],
    rows: tuple[CsvRow, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _net_mean(returns: tuple[float, ...], side_cost_bps: int) -> float | None:
    rate = side_cost_bps / 10_000.0
    net = tuple((1.0 + value) * (1.0 - rate) / (1.0 + rate) - 1.0 for value in returns)
    return _mean(net)


def _bootstrap_ci(
    values: tuple[float, ...],
    seed: int,
) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    rng = random.Random(seed)
    means = sorted(
        sum(rng.choice(values) for _ in values) / len(values)
        for _ in range(BOOTSTRAP_SAMPLES)
    )
    return (
        means[int((len(means) - 1) * 0.025)],
        means[int((len(means) - 1) * 0.975)],
    )
