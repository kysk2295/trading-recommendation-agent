from __future__ import annotations

import ast
import stat
import subprocess
from decimal import Decimal
from pathlib import Path

import run_kr_day_capsule_shadow as cli
from tests.test_kr_day_capsule_adapter import _request
from tests.test_kr_day_capsule_shadow import _advance, _entry_evaluation
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_models import (
    KrDayCapsuleEvaluation,
    KrDayCapsuleEvaluationRequest,
)
from trading_agent.kr_day_capsule_shadow_store import KrDayCapsuleShadowStore
from trading_agent.private_immutable_file import publish_private_immutable_text

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_day_capsule_shadow.py"


def test_cli_help_exposes_only_local_shadow_artifact_options() -> None:
    # Given
    command = ("uv", "run", "python", str(SCRIPT), "--help")

    # When
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then
    assert completed.returncode == 0
    assert "--request" in completed.stdout
    assert "--store" in completed.stdout
    assert "--output" in completed.stdout
    assert "--order" not in completed.stdout
    assert "--account" not in completed.stdout
    assert "--provider" not in completed.stdout


def test_cli_persists_active_shadow_and_replays_without_new_event(tmp_path: Path) -> None:
    # Given
    request = _request_for(_entry_evaluation())
    request_path = _publish_request(tmp_path, "entry", request)
    store = tmp_path / "store" / "shadow.sqlite3"
    command = _command(request_path, store)

    # When
    first = _run(command)
    replay = _run(command)

    # Then
    assert first.returncode == 0
    assert replay.returncode == 0
    first_payload = _json(first)
    replay_payload = _json(replay)
    assert first_payload.mutation == 0
    assert first_payload.provider_read_only is True
    assert first_payload.research_only is True
    assert first_payload.trading_authority is False
    assert first_payload.created_count == 1
    assert first_payload.events[0].status == "active"
    assert replay_payload.created_count == 0
    assert replay_payload.reused_count == 1
    assert _stable_event(first_payload) == _stable_event(replay_payload)
    assert len(KrDayCapsuleShadowStore(store).events()) == 1


def test_cli_keeps_valid_sibling_when_private_artifact_is_invalid(tmp_path: Path) -> None:
    # Given
    valid = _publish_request(tmp_path, "valid", _request_for(_entry_evaluation()))
    invalid = tmp_path / "requests" / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    invalid.chmod(0o600)
    store = tmp_path / "store" / "shadow.sqlite3"

    # When
    completed = _run(_command(valid, store, invalid))

    # Then
    payload = _json(completed)
    assert completed.returncode == 2
    assert payload.result == "partial"
    assert payload.invalid_request_count == 1
    assert payload.created_count == 1
    assert payload.mutation == 0
    assert len(KrDayCapsuleShadowStore(store).events()) == 1


def test_cli_rejects_more_than_three_requests_before_store_mutation(tmp_path: Path) -> None:
    # Given
    request = _publish_request(tmp_path, "one", _request_for(_entry_evaluation()))
    store = tmp_path / "store" / "shadow.sqlite3"

    # When
    completed = _run(_command(request, store, request, request, request))

    # Then
    payload = _json(completed)
    assert completed.returncode == 2
    assert payload.result == "blocked"
    assert payload.created_count == 0
    assert payload.mutation == 0
    assert not store.exists()


def test_cli_resolves_same_bar_collision_to_stop_and_censors_gap(tmp_path: Path) -> None:
    # Given
    entry = _entry_evaluation()
    collision = _advance(entry, low=Decimal("9900"), high=Decimal("10400"))
    gap = _advance(entry, count=2)
    entry_path = _publish_request(tmp_path, "entry", _request_for(entry))
    collision_path = _publish_request(tmp_path, "collision", _request_for(collision))
    gap_path = _publish_request(tmp_path, "gap", _request_for(gap))
    stopped_store = tmp_path / "stopped" / "shadow.sqlite3"
    censored_store = tmp_path / "censored" / "shadow.sqlite3"

    # When
    _ = _run(_command(entry_path, stopped_store))
    stopped = _run(_command(collision_path, stopped_store))
    _ = _run(_command(entry_path, censored_store))
    censored = _run(_command(gap_path, censored_store))

    # Then
    stopped_event = _json(stopped).events[0]
    censored_event = _json(censored).events[0]
    assert stopped.returncode == 0
    assert stopped_event.status == "stopped"
    assert censored.returncode == 0
    assert censored_event.status == "censored"
    assert censored_event.accepted_bar_cursor != censored_event.attempted_bar_cursor


def test_cli_rejects_unsafe_store_parent_without_chmod(tmp_path: Path) -> None:
    # Given
    request = _publish_request(tmp_path, "entry", _request_for(_entry_evaluation()))
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    store = unsafe / "shadow.sqlite3"

    # When
    completed = _run(_command(request, store))

    # Then
    payload = _json(completed)
    assert completed.returncode == 2
    assert payload.result == "blocked"
    assert stat.S_IMODE(unsafe.stat().st_mode) == 0o755
    assert not store.exists()


def test_cli_blocks_missing_request_without_creating_store(tmp_path: Path) -> None:
    # Given
    store = tmp_path / "store" / "shadow.sqlite3"

    # When
    completed = _run(("uv", "run", "python", str(SCRIPT), "--store", str(store)))

    # Then
    payload = _json(completed)
    assert completed.returncode == 2
    assert payload.result == "blocked"
    assert payload.mutation == 0
    assert not store.exists()


def test_cli_writes_only_content_addressed_private_local_receipt(tmp_path: Path) -> None:
    # Given
    request = _publish_request(tmp_path, "entry", _request_for(_entry_evaluation()))
    store = tmp_path / "store" / "shadow.sqlite3"
    output = tmp_path / "output"

    # When
    completed = _run((*_command(request, store), "--output", str(output)))

    # Then
    payload = _json(completed)
    receipts = tuple(output.glob("kr_day_capsule_shadow_*.json"))
    assert completed.returncode == 0
    assert payload.receipt_id is not None
    assert len(receipts) == 1
    assert payload.receipt_id in receipts[0].name
    assert stat.S_IMODE(receipts[0].stat().st_mode) == 0o600
    assert str(output) not in completed.stdout


def test_cli_import_closure_has_no_order_or_account_authority() -> None:
    # Given
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imports = tuple(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    )

    # When
    forbidden = tuple(
        name
        for name in imports
        if any(token in name.lower() for token in ("alpaca", "broker", "order", "account", "balance"))
    )

    # Then
    assert forbidden == ()


def _request_for(evaluation: KrDayCapsuleEvaluation) -> KrDayCapsuleEvaluationRequest:
    base = _request()
    return KrDayCapsuleEvaluationRequest.model_validate(
        base.model_dump(mode="python")
        | {
            "bars": evaluation.setup_input.bars,
            "market": evaluation.market,
            "evaluated_at": evaluation.evaluated_at,
        }
    )


def _publish_request(tmp_path: Path, name: str, request: KrDayCapsuleEvaluationRequest) -> Path:
    root = tmp_path / "requests"
    root.mkdir(mode=0o700, exist_ok=True)
    target = root / f"{name}.json"
    assert publish_private_immutable_text(target, canonical_experiment_ledger_json(request) + "\n") is True
    return target


def _command(first: Path, store: Path, *others: Path) -> tuple[str, ...]:
    requests = (first, *others)
    return (
        "uv",
        "run",
        "python",
        str(SCRIPT),
        *(part for request in requests for part in ("--request", str(request))),
        "--store",
        str(store),
    )


def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)


def _json(completed: subprocess.CompletedProcess[str]) -> cli._CliResult:
    return cli._CliResult.model_validate_json(completed.stdout)


def _stable_event(payload: cli._CliResult) -> tuple[str | None, ...]:
    event = payload.events[0]
    return (
        event.event_id,
        event.capsule_id,
        event.session_date,
        event.attempted_bar_cursor,
        event.accepted_bar_cursor,
        event.status,
    )
