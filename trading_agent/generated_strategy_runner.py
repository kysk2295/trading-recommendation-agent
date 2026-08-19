from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from contextlib import redirect_stdout
from types import ModuleType
from typing import Final, Protocol, runtime_checkable

MAX_FRAME_BYTES: Final = 64 * 1024
MAX_SOURCE_BYTES: Final = 64 * 1024 * 1024
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


@runtime_checkable
class _Strategy(Protocol):
    def observe(
        self,
        bar: dict[str, JsonValue],
        candidate: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None: ...


def main() -> int:
    if len(sys.argv) != 3:
        _write_failure(0, "runner_arguments_invalid")
        return 1
    try:
        source = _read_source(sys.argv[1], sys.argv[2])
        module = _load_module(source)
        factory = module.__dict__["create_strategy"]
        if not callable(factory):
            raise TypeError
        with redirect_stdout(sys.stderr):
            strategy = factory({"protocol_version": 1})
        if not isinstance(strategy, _Strategy):
            raise TypeError
        observer = strategy.observe
    except (OSError, TypeError, ValueError):
        _write_failure(0, "generated_strategy_source_invalid")
        return 1
    except (ImportError, SyntaxError):
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


def _read_source(descriptor_text: str, expected_sha256: str) -> bytes:
    descriptor = int(descriptor_text)
    if descriptor < 3:
        raise ValueError
    try:
        if len(expected_sha256) != 64:
            raise ValueError
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 1
            or before.st_size > MAX_SOURCE_BYTES
        ):
            raise ValueError
        _ = os.lseek(descriptor, 0, os.SEEK_SET)
        content = bytearray()
        while chunk := os.read(
            descriptor,
            min(64 * 1024, MAX_SOURCE_BYTES + 1 - len(content)),
        ):
            content.extend(chunk)
            if len(content) > MAX_SOURCE_BYTES:
                raise ValueError
        after = os.fstat(descriptor)
        source = bytes(content)
        if _source_identity(before) != _source_identity(after) or hashlib.sha256(source).hexdigest() != expected_sha256:
            raise ValueError
        return source
    finally:
        os.close(descriptor)


def _source_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _load_module(source: bytes) -> ModuleType:
    module = ModuleType("generated_strategy")
    code = compile(source, "<generated-strategy>", "exec")
    with redirect_stdout(sys.stderr):
        exec(code, module.__dict__)
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
