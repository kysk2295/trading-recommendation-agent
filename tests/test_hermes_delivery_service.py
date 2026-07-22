from __future__ import annotations

import datetime as dt
import importlib
import json
import plistlib
import stat
import sys
import threading
from pathlib import Path

import pytest

from trading_agent.hermes_delivery_models import HermesDeliveryKind, build_hermes_delivery_event
from trading_agent.hermes_delivery_store import HermesDeliveryStore

AT = dt.datetime(2026, 7, 22, 15, 0, tzinfo=dt.UTC)


class StoppingSender:
    def __init__(self, stop: threading.Event) -> None:
        self.stop = stop

    def send(self, request):
        _ = request
        self.stop.set()
        return _worker_module().HermesPlatformAcknowledgement(message_id="901")


class BlockingSender:
    def __init__(self, entered: threading.Event, release: threading.Event, stop: threading.Event) -> None:
        self.entered = entered
        self.release = release
        self.stop = stop

    def send(self, request):
        _ = request
        self.entered.set()
        assert self.release.wait(timeout=2)
        self.stop.set()
        return _worker_module().HermesPlatformAcknowledgement(message_id="902")


def test_foreground_service_acknowledges_then_releases_process_lease(tmp_path: Path) -> None:
    # Given
    worker = _worker_module()
    store = _store_with_event(tmp_path / "delivery.sqlite3")
    stop = threading.Event()

    # When
    worker.run_delivery_service(
        store.path,
        StoppingSender(stop),
        poll_seconds=0.01,
        stop_event=stop,
    )

    # Then
    assert len(store.attempts()) == 1
    assert store.acknowledgements()[0].platform_message_id == "901"
    assert stat.S_IMODE(Path(f"{store.path}.service.lock").stat().st_mode) == 0o600


def test_process_lifetime_lease_blocks_a_second_service(tmp_path: Path) -> None:
    # Given
    worker = _worker_module()
    store = _store_with_event(tmp_path / "delivery.sqlite3")
    entered = threading.Event()
    release = threading.Event()
    stop = threading.Event()

    def first_service() -> None:
        worker.run_delivery_service(
            store.path,
            BlockingSender(entered, release, stop),
            poll_seconds=0.01,
            stop_event=stop,
        )

    thread = threading.Thread(target=first_service)
    thread.start()
    assert entered.wait(timeout=2)
    second_stop = threading.Event()
    second_stop.set()

    # When / Then
    with pytest.raises(worker.HermesDeliveryServiceLeaseUnavailableError):
        worker.run_delivery_service(
            store.path,
            StoppingSender(second_stop),
            poll_seconds=0.01,
            stop_event=second_stop,
        )
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(store.attempts()) == 1


def test_service_cli_provisions_and_verifies_secret_free_launch_agent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    service = _service_module()
    profile_root = tmp_path / "stockagent"
    profile_root.mkdir()
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.symlink_to(sys.executable)
    database = _store_with_event(tmp_path / "delivery.sqlite3").path
    plist = tmp_path / "ai.trading-agent.hermes-delivery.plist"
    arguments = _deployment_arguments(profile_root, database, plist, python=python)

    # When
    provision_exit = service.main(("provision", *arguments))
    provision_output = json.loads(capsys.readouterr().out)
    verify_exit = service.main(("verify", *arguments))
    verify_output = json.loads(capsys.readouterr().out)

    # Then
    assert provision_exit == 0
    assert verify_exit == 0
    assert provision_output == {"result": "provisioned"}
    assert verify_output == {"result": "verified"}
    payload = plistlib.loads(plist.read_bytes())
    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True
    assert payload["ProgramArguments"][0] == str(python)
    assert payload["ProgramArguments"][-3:] == ["run", "--database", str(database)]
    assert set(payload["EnvironmentVariables"]) == {"HERMES_HOME", "HOME", "PATH", "VIRTUAL_ENV"}
    assert payload["EnvironmentVariables"]["HERMES_HOME"] == str(profile_root)
    assert payload["EnvironmentVariables"]["VIRTUAL_ENV"] == str(venv_bin.parent)
    serialized = json.dumps(payload).casefold()
    assert all(word not in serialized for word in ("token", "secret", "credential", "broker", "order"))
    assert stat.S_IMODE(plist.stat().st_mode) == 0o600


def test_service_cli_runs_only_with_absolute_private_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    service = _service_module()
    database = _store_with_event(tmp_path / "delivery.sqlite3").path
    calls: list[Path] = []

    # When
    valid_exit = service.main(("run", "--database", str(database)), runner=calls.append)
    valid_output = json.loads(capsys.readouterr().out)
    invalid_exit = service.main(("run", "--database", "relative.sqlite3"), runner=calls.append)
    invalid_output = json.loads(capsys.readouterr().out)

    # Then
    assert valid_exit == 0
    assert valid_output == {"result": "stopped"}
    assert invalid_exit == 2
    assert invalid_output == {"reason": "invalid_service_configuration", "result": "blocked"}
    assert calls == [database]


def _store_with_event(path: Path) -> HermesDeliveryStore:
    store = HermesDeliveryStore(path)
    with store.writer() as writer:
        _ = writer.append_event(
            build_hermes_delivery_event(
                kind=HermesDeliveryKind.INCIDENT,
                source_event_id="service-test-event",
                market_id="kr_equities",
                lane_id=None,
                occurred_at=AT,
                payload_sha256="a" * 64,
                rendered_text="service test incident",
            )
        )
    return store


def _deployment_arguments(
    profile_root: Path,
    database: Path,
    plist: Path,
    *,
    python: Path = Path(sys.executable),
) -> tuple[str, ...]:
    return (
        "--label",
        "ai.trading-agent.hermes-delivery",
        "--project-root",
        str(Path(__file__).parents[1]),
        "--python",
        str(python),
        "--profile-root",
        str(profile_root),
        "--database",
        str(database),
        "--plist",
        str(plist),
    )


def _worker_module():
    return importlib.import_module("integrations.hermes.trading-agent.delivery_worker")


def _service_module():
    return importlib.import_module("integrations.hermes.trading-agent.service")
