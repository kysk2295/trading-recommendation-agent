from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Final

from trading_agent.alpaca_scanner_quality_models import (
    ScannerQualityConfig,
    ScannerQualityOutcome,
)

BOOTSTRAP_SAMPLES: Final = 2_000
CsvValue = str | int | float | bool | None
CsvRow = dict[str, CsvValue]


def write_scanner_quality_report(
    output_dir: Path,
    outcomes: tuple[ScannerQualityOutcome, ...],
    configs: tuple[ScannerQualityConfig, ...],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "scanner_quality_outcomes.csv",
        _outcome_fields(),
        tuple(_outcome_row(row) for row in outcomes),
    )
    summaries = tuple(_summary_row(config, outcomes) for config in configs)
    _write_csv(
        output_dir / "scanner_quality_summary.csv",
        tuple(summaries[0]) if summaries else _summary_fields(),
        summaries,
    )
    yearly = tuple(
        {"year": year, **_summary_row(config, outcomes, year)}
        for config in configs
        for year in sorted({row.session_date.year for row in outcomes if row.config == config})
    )
    _write_csv(
        output_dir / "scanner_quality_yearly.csv",
        ("year", *_summary_fields()),
        yearly,
    )
    report = (
        "# Alpaca 급등 스캐너 후보 품질 진단",
        "",
        "> 매매 전략 PF가 아니라 스캐너가 고른 후보의 후행 장중 경로 품질 진단입니다.",
        "",
        f"- 인접 임계값 조합: {len(configs)}개",
        f"- 종목·날짜·설정별 선정 결과: {len(outcomes)}건",
        f"- 완전 경로: {sum(row.complete for row in outcomes)}건",
        "- 매 설정마다 당일 전체 결정표에서 상승률·ADV 비율·거래대금 순 최대 10개를 다시 선정",
        "- 기준: 상승률 2/4/6/8%, 최대가격 $20/$50/$100, 거래대금 $0.25/$0.5/$1M, volume/ADV 1/5/10%",
        "- 09:30 ET 스캔에서는 알려진 gap과 전일 종가 대비 상승률이 동일하므로 독립 변수로 중복 시험하지 않음",
        "- 경로 시작: 스캔 뒤 첫 1분을 건너뛴 09:31 ET 시가",
        "",
        "## 해석 제한",
        "",
        "- 기존 selected 거래를 사후 필터링하지 않고 전체 결정표에서 설정별로 재선정합니다.",
        "- 수집 envelope 상한 밖이거나 분봉이 불완전한 경로는 수익 0으로 바꾸지 않고 censored로 남깁니다.",
        "- MFE·MAE·5/15/30분·종가 수익은 후보 품질 진단이며 체결 가능한 독립 전략 성과가 아닙니다.",
        "- active+inactive Assets는 완전한 point-in-time 상장 유니버스가 아니며 상폐·ticker 변경 생존편향이 남습니다.",
        "- 108개 조합을 반복 비교하므로 bootstrap CI만으로 다중검정·과최적화가 해소되지 않습니다.",
        "- 실제 NBBO·spread·halt/LULD는 ORB 등 진입 전략 단계에서 별도 위험 게이트로 다룹니다.",
    )
    _ = (output_dir / "scanner_quality_report_ko.md").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )


def _summary_row(
    config: ScannerQualityConfig,
    outcomes: tuple[ScannerQualityOutcome, ...],
    year: int | None = None,
) -> CsvRow:
    selected = tuple(
        row for row in outcomes if row.config == config and (year is None or row.session_date.year == year)
    )
    complete = tuple(row for row in selected if row.complete)
    eod = tuple(row.eod_return for row in complete if row.eod_return is not None)
    ci_low, ci_high = _bootstrap_ci(eod, _seed(config, year))
    return {
        **_config_row(config),
        "selected_session_count": len({row.session_date for row in selected}),
        "selection_count": len(selected),
        "complete_count": len(complete),
        "censored_count": len(selected) - len(complete),
        "path_coverage_rate": None if not selected else len(complete) / len(selected),
        "positive_5m_rate": _positive_rate(tuple(row.return_5m for row in complete if row.return_5m is not None)),
        "positive_15m_rate": _positive_rate(tuple(row.return_15m for row in complete if row.return_15m is not None)),
        "positive_30m_rate": _positive_rate(tuple(row.return_30m for row in complete if row.return_30m is not None)),
        "positive_eod_rate": _positive_rate(eod),
        "average_5m_return": _mean(tuple(row.return_5m for row in complete if row.return_5m is not None)),
        "average_15m_return": _mean(tuple(row.return_15m for row in complete if row.return_15m is not None)),
        "average_30m_return": _mean(tuple(row.return_30m for row in complete if row.return_30m is not None)),
        "average_eod_return": _mean(eod),
        "average_mfe": _mean(tuple(row.mfe for row in complete if row.mfe is not None)),
        "average_mae": _mean(tuple(row.mae for row in complete if row.mae is not None)),
        "mean_ci_low": ci_low,
        "mean_ci_high": ci_high,
    }


def _config_row(config: ScannerQualityConfig) -> CsvRow:
    return {
        "min_change_pct": config.min_change_pct,
        "min_price": config.min_price,
        "max_price": config.max_price,
        "min_dollar_volume": config.min_dollar_volume,
        "min_adv_fraction": config.min_adv_fraction,
    }


def _outcome_row(row: ScannerQualityOutcome) -> CsvRow:
    return {
        **_config_row(row.config),
        "session_date": row.session_date.isoformat(),
        "symbol": row.symbol,
        "rank": row.rank,
        "bar_count": row.bar_count,
        "complete": row.complete,
        "entry_at": row.entry_at.isoformat(),
        "entry": row.entry,
        "return_5m": row.return_5m,
        "return_15m": row.return_15m,
        "return_30m": row.return_30m,
        "eod_return": row.eod_return,
        "mfe": row.mfe,
        "mae": row.mae,
    }


def _summary_fields() -> tuple[str, ...]:
    return (
        "min_change_pct",
        "min_price",
        "max_price",
        "min_dollar_volume",
        "min_adv_fraction",
        "selected_session_count",
        "selection_count",
        "complete_count",
        "censored_count",
        "path_coverage_rate",
        "positive_5m_rate",
        "positive_15m_rate",
        "positive_30m_rate",
        "positive_eod_rate",
        "average_5m_return",
        "average_15m_return",
        "average_30m_return",
        "average_eod_return",
        "average_mfe",
        "average_mae",
        "mean_ci_low",
        "mean_ci_high",
    )


def _outcome_fields() -> tuple[str, ...]:
    return (
        "min_change_pct",
        "min_price",
        "max_price",
        "min_dollar_volume",
        "min_adv_fraction",
        "session_date",
        "symbol",
        "rank",
        "bar_count",
        "complete",
        "entry_at",
        "entry",
        "return_5m",
        "return_15m",
        "return_30m",
        "eod_return",
        "mfe",
        "mae",
    )


def _write_csv(path: Path, fields: tuple[str, ...], rows: tuple[CsvRow, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _positive_rate(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(value > 0.0 for value in values) / len(values)


def _bootstrap_ci(values: tuple[float, ...], seed: int) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(BOOTSTRAP_SAMPLES))
    return means[int(0.025 * (len(means) - 1))], means[int(0.975 * (len(means) - 1))]


def _seed(config: ScannerQualityConfig, year: int | None) -> int:
    return (
        int(config.min_change_pct * 10_000) * 1_000_000
        + int(config.max_price) * 10_000
        + int(config.min_dollar_volume)
        + int(config.min_adv_fraction * 10_000)
        + (year or 0)
    )
