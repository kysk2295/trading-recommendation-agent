from __future__ import annotations

import datetime as dt

type WorkbenchInput = (
    str
    | int
    | bool
    | None
    | dt.datetime
    | tuple["WorkbenchInput", ...]
    | dict[str, "WorkbenchInput"]
)


def empty_workbench_payload(observed_at: dt.datetime, trace_id: str) -> dict[str, WorkbenchInput]:
    section: dict[str, WorkbenchInput] = {
        "state": "empty",
        "observed_at": observed_at,
        "blocker_code": None,
        "summary": "연구 입력 대기",
        "trace_id": trace_id,
    }
    return {
        "schema_version": 1,
        "selected_view": "market_pulse",
        "market": dict(section),
        "chain": dict(section)
        | {
            "underlying": None,
            "selected_expiration": None,
            "expirations": (),
            "total_count": 0,
            "projected_count": 0,
            "truncated": False,
            "rows": (),
        },
        "scenario": None,
        "agent": dict(section),
        "experiment": dict(section),
        "promotions": (),
    }


__all__ = ("empty_workbench_payload",)
