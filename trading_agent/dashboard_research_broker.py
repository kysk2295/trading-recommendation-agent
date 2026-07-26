from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import assert_never

from trading_agent.dashboard_research_broker_contract import (
    InvalidResearchBrokerCommandError,
    decode_broker_command,
)


def _main(raw: str) -> int:
    operation, parameters = decode_broker_command(raw)
    experiment = Path(os.environ["DASHBOARD_EXPERIMENT_ROOT"])
    source = Path(os.environ["DASHBOARD_SOURCE_EVIDENCE_ROOT"])
    if not experiment.is_dir() or experiment.is_symlink() or not source.is_dir() or source.is_symlink():
        raise InvalidResearchBrokerCommandError("broker_root_invalid")
    match operation:
        case "evidence-query":
            result = _query_evidence(source, experiment, parameters)
        case "hypothesis-register":
            result = _register_hypothesis(experiment, parameters)
        case "experiment-run":
            result = _run_experiment(experiment, parameters)
        case unexpected:
            assert_never(unexpected)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0

def _query_evidence(source: Path, experiment: Path, refs: tuple[str, ...]) -> dict[str, str | int]:
    digests = tuple(
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source.glob("*.json"))[:32]
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 256 * 1024
    )
    payload = {"operation": "evidence-query", "requested_refs": refs, "source_digests": digests}
    _write_once(experiment / "evidence-query.json", payload)
    return {"operation": "evidence-query", "evidence_count": len(digests)}


def _register_hypothesis(
    experiment: Path,
    parameters: tuple[str, ...],
) -> dict[str, str]:
    trigger_id, family_id, payload_sha256 = parameters
    candidate = experiment / "candidate.json"
    candidate_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else payload_sha256
    record = {
        "agent_family_id": family_id,
        "candidate_sha256": candidate_sha256,
        "operation": "hypothesis-register",
        "payload_sha256": payload_sha256,
        "trigger_id": trigger_id,
    }
    _append_once(experiment / "experiment-ledger.jsonl", trigger_id, record)
    return {"operation": "hypothesis-register", "candidate_sha256": candidate_sha256}


def _run_experiment(experiment: Path, parameters: tuple[str, ...]) -> dict[str, str]:
    trigger_id = parameters[0]
    ledger = experiment / "experiment-ledger.jsonl"
    query = experiment / "evidence-query.json"
    if not ledger.is_file() or not query.is_file():
        raise InvalidResearchBrokerCommandError("experiment_inputs_missing")
    result_sha256 = hashlib.sha256(ledger.read_bytes() + query.read_bytes()).hexdigest()
    result = {"operation": "experiment-run", "result_sha256": result_sha256, "trigger_id": trigger_id}
    _write_once(experiment / "experiment-result.json", result)
    return result


def _append_once(path: Path, key: str, value: dict[str, str]) -> None:
    if path.exists():
        existing = tuple(json.loads(line) for line in path.read_text().splitlines())
        if any(item.get("trigger_id") == key for item in existing):
            raise InvalidResearchBrokerCommandError("hypothesis_already_registered")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise InvalidResearchBrokerCommandError("experiment_ledger_invalid")
        os.write(descriptor, (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_once(path: Path, value: Mapping[str, str | int | tuple[str, ...]]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1]))


__all__ = ()
