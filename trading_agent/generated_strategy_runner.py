from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Final, Protocol, runtime_checkable

MAX_FRAME_BYTES: Final = 64 * 1024
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


@runtime_checkable
class _Strategy(Protocol):
    def observe(
        self,
        bar: dict[str, JsonValue],
        candidate: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None: ...


def main() -> int:
    if len(sys.argv) != 2:
        _write_failure(0, "runner_arguments_invalid")
        return 1
    try:
        module = _load_module(Path(sys.argv[1]))
        factory = module.__dict__["create_strategy"]
        if not callable(factory):
            raise TypeError
        with redirect_stdout(sys.stderr):
            strategy = factory({"protocol_version": 1})
        if not isinstance(strategy, _Strategy):
            raise TypeError
        observer = strategy.observe
    except (ImportError, OSError, TypeError, ValueError):
        _write_failure(0, "generated_strategy_import_failed")
        return 1
    _write({"kind": "ready", "sequence": 0})
    expected = 1
    while line := sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1):
        try:
            request = _request(line, expected)
        except (KeyError, OSError, TypeError, ValueError):
            _write_failure(expected, "generated_strategy_protocol_failed")
            return 1
        try:
            with redirect_stdout(sys.stderr):
                response = observer(request["bar"], request["candidate"])
        except (ArithmeticError, AssertionError, LookupError, OSError, RuntimeError, TypeError, ValueError):
            _write_failure(expected, "generated_strategy_observe_failed")
            return 1
        try:
            _write(_response(response, expected))
            expected += 1
        except (KeyError, OSError, TypeError, ValueError):
            _write_failure(expected, "generated_strategy_protocol_failed")
            return 1
    return 0


def _load_module(source: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("generated_strategy", source)
    if spec is None or spec.loader is None:
        raise ImportError
    module = importlib.util.module_from_spec(spec)
    with redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
    return module


def _request(line: bytes, expected: int):
    if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
        raise ValueError
    payload = json.loads(line, parse_constant=_reject_constant)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"bar", "candidate", "kind", "sequence"}
        or payload["kind"] != "observe"
        or payload["sequence"] != expected
        or not isinstance(payload["bar"], dict)
        or (payload["candidate"] is not None and not isinstance(payload["candidate"], dict))
    ):
        raise ValueError
    return payload


def _response(value, sequence: int) -> dict[str, str | float | int]:
    if value is None:
        return {"kind": "no_signal", "sequence": sequence}
    if not isinstance(value, dict) or set(value) != {
        "symbol",
        "timestamp",
        "entry",
        "stop",
        "rationale",
    }:
        raise ValueError
    return {
        "kind": "signal",
        "sequence": sequence,
        "symbol": value["symbol"],
        "timestamp": value["timestamp"],
        "entry": value["entry"],
        "stop": value["stop"],
        "rationale": value["rationale"],
    }


def _write(payload: dict[str, str | float | int]) -> None:
    encoded = (json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
    if len(encoded) > MAX_FRAME_BYTES:
        raise ValueError
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _write_failure(sequence: int, reason: str) -> None:
    _write({"kind": "failure", "sequence": sequence, "reason": reason})


def _reject_constant(value: str) -> None:
    del value
    raise ValueError


if __name__ == "__main__":
    raise SystemExit(main())
