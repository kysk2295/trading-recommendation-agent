from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.intraday_actual_research_plan_models import (
    IntradayActualResearchPlanError,
)
from trading_agent.intraday_research_dataset_catalog_models import (
    MAX_INTRADAY_RESEARCH_CANDIDATE_SESSIONS,
)


def resolve_intraday_actual_research_session_dirs(
    session_dirs: tuple[Path, ...],
    session_root: Path | None,
    required_session_dates: tuple[dt.date, ...],
) -> tuple[Path, ...]:
    if session_root is None:
        if not session_dirs:
            raise IntradayActualResearchPlanError
        return tuple(path.resolve(strict=False) for path in session_dirs)
    root = session_root.absolute()
    if (
        session_dirs
        or not required_session_dates
        or session_root.is_symlink()
        or not root.is_dir()
    ):
        raise IntradayActualResearchPlanError
    latest_required = max(required_session_dates)
    dated: list[tuple[dt.date, Path]] = []
    for path in root.iterdir():
        if (
            path.is_symlink()
            or not path.is_dir()
            or len(path.name) != 8
            or not path.name.isascii()
            or not path.name.isdecimal()
        ):
            continue
        try:
            session_date = dt.datetime.strptime(path.name, "%Y%m%d").date()
        except ValueError:
            continue
        if session_date <= latest_required:
            dated.append((session_date, path))
    selected = tuple(
        path
        for _, path in sorted(dated)[
            -MAX_INTRADAY_RESEARCH_CANDIDATE_SESSIONS:
        ]
    )
    required_names = {
        value.strftime("%Y%m%d") for value in required_session_dates
    }
    if not selected or not required_names.issubset(path.name for path in selected):
        raise IntradayActualResearchPlanError
    return selected


__all__ = ("resolve_intraday_actual_research_session_dirs",)
