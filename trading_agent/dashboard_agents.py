from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from typing import Literal

from trading_agent.dashboard_models import AgentId, AgentView, JobRow

JOB_DATE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def agent_views(jobs: Iterable[JobRow], today: dt.date) -> tuple[AgentView, ...]:
    groups: tuple[tuple[AgentId, str, tuple[str, ...]], ...] = (
        ("kr-theme", "한국 테마", ("kr-m3",)),
        ("us-intraday", "미국 장중", ("us-forward", "forward-progress")),
        ("us-systematic", "미국 시스템", ("us-systematic",)),
        ("us-swing", "미국 스윙", ("us-swing",)),
        ("research", "실증 연구", ("actual-research", "post-closeout-research")),
        ("delivery", "알림 전달", ("hermes-delivery",)),
    )
    rows = tuple(jobs)
    result: list[AgentView] = []
    for agent_id, label, needles in groups:
        matches = tuple(row for row in rows if any(needle in row[0] for needle in needles))
        if not matches:
            continue
        name, pid, exit_code = max(matches, key=lambda row: (row[1] is not None, row[0]))
        state: Literal["running", "armed", "idle", "failed"]
        scheduled_date = _scheduled_date(name)
        if scheduled_date is not None and scheduled_date > today:
            state = "armed"
        elif exit_code not in (0, -15):
            state = "failed"
        elif pid is not None:
            state = "running"
        elif exit_code == 0:
            state = "armed"
        else:
            state = "idle"
        result.append(
            AgentView(
                agent_id=agent_id,
                label=label,
                state=state,
                scheduled_label=name,
            )
        )
    return tuple(result)


def _scheduled_date(label: str) -> dt.date | None:
    matches = JOB_DATE.findall(label)
    if not matches:
        return None
    return dt.datetime.strptime(matches[-1], "%Y%m%d").date()
