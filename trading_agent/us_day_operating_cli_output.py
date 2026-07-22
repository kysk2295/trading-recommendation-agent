from __future__ import annotations

import json
from collections.abc import Mapping

from trading_agent.us_day_operating_models import UsDayOperatingResult
from trading_agent.us_day_session_inspection import UsDaySessionInspection

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


def print_operating_result(result: UsDayOperatingResult) -> None:
    print_payload(
        {
            "reasons": list(result.reasons),
            "result": result.status.value,
            "session_id": result.session_id,
            "transitions": [transition.value for transition in result.transitions],
        }
    )


def print_inspection(inspection: UsDaySessionInspection, result: str, reasons: tuple[str, ...]) -> None:
    print_payload(
        {
            "open_orders": len(inspection.broker_state.open_orders),
            "positions": len(inspection.broker_state.positions),
            "reasons": list(reasons),
            "result": result,
        }
    )


def print_payload(payload: Mapping[str, JsonValue]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
