from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trading_agent.kis_live import regular_session_bounds
from trading_agent.strategy_factory import StrategyMode


@dataclass(frozen=True, slots=True)
class EodWaitConfig:
    max_wait: dt.timedelta
    poll_seconds: float
    settlement_delay: dt.timedelta


def wait_for_eod_ready(
    clock: Callable[[], dt.datetime],
    sleeper: Callable[[float], None],
    session_date: dt.date,
    config: EodWaitConfig,
) -> dt.datetime | None:
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        return None
    target = bounds[1] + config.settlement_delay
    observed_at = clock().astimezone(bounds[0].tzinfo)
    if observed_at.date() != session_date or target - observed_at > config.max_wait:
        return None
    while observed_at < target:
        sleeper(min(config.poll_seconds, (target - observed_at).total_seconds()))
        observed_at = clock().astimezone(bounds[0].tzinfo)
        if observed_at.date() != session_date:
            return None
    return observed_at


def eod_catchup_command(
    project_dir: Path,
    output: Path,
    strategy: StrategyMode,
    max_pages: int,
) -> tuple[str, ...]:
    return (
        str(project_dir / "run_kis_eod_catchup.py"),
        "--output-dir",
        str(output),
        "--strategy",
        strategy.value,
        "--max-pages",
        str(max_pages),
    )
