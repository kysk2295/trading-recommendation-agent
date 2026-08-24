from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import run_dashboard_publisher
import trading_agent.dashboard_publisher_events as publisher_events
import trading_agent.dashboard_publisher_relay_runtime as relay_runtime
from tests.dashboard_models_v2_fixtures import snapshot_payload
from trading_agent.dashboard_models import DashboardCredentials
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2


class _Socket:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.sent = anyio.Event()

    async def send(self, message: str) -> None:
        self.messages.append(message)
        self.sent.set()


def test_cli_default_and_override_reach_initial_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a dry-run publisher with observable snapshot arguments.
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    override = tmp_path / "explicit-kr-state"
    observed: list[Path | None] = []
    snapshot = DashboardSnapshotV2.model_validate(snapshot_payload())
    monkeypatch.setattr(run_dashboard_publisher, "require_current_main_authority", lambda: None)
    monkeypatch.setattr(
        run_dashboard_publisher,
        "load_dashboard_credentials",
        lambda _path: DashboardCredentials("https://example.test", SecretStr("fixture-value-with-adequate-length")),
    )
    monkeypatch.setattr(run_dashboard_publisher, "load_system_authority_verifier", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_dashboard_publisher, "_cycle_database", lambda _path: None)

    def observe(_outputs: Path, **settings: Path | None) -> DashboardSnapshotV2:
        observed.append(settings.get("kr_day_state_root"))
        return snapshot

    monkeypatch.setattr(run_dashboard_publisher, "collect_dashboard_snapshot_v2", observe)

    # When: the CLI runs once with its default and once with an explicit override.
    runner = CliRunner()
    default = runner.invoke(run_dashboard_publisher.app, ["publish", "--outputs", str(outputs), "--dry-run"])
    explicit = runner.invoke(
        run_dashboard_publisher.app,
        ["publish", "--outputs", str(outputs), "--kr-day-state-root", str(override), "--dry-run"],
    )

    # Then: both roots are explicitly typed into initial collection.
    assert default.exit_code == 0 and explicit.exit_code == 0
    assert observed == [run_dashboard_publisher.DEFAULT_KR_DAY_STATE_ROOT, override]
    assert (
        Path.home() / ".local" / "state" / "trading-agent" / "kr-day-session"
    ) == run_dashboard_publisher.DEFAULT_KR_DAY_STATE_ROOT


@pytest.mark.anyio
async def test_event_watch_watches_and_projects_explicit_kr_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a dedicated KR service root and an observable watcher/projection.
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    state_root = tmp_path / "kr-state"
    state_root.mkdir()
    watched: list[tuple[Path, ...]] = []
    projected: list[Path | None] = []
    socket = _Socket()
    snapshot = DashboardSnapshotV2.model_validate(snapshot_payload())

    async def one_change(*paths: Path, **_settings: int) -> AsyncIterator[frozenset[Path]]:
        watched.append(paths)
        yield frozenset({state_root / "kr-day-decisions.sqlite3"})

    def observe(_outputs: Path, **settings: Path | None) -> DashboardSnapshotV2:
        projected.append(settings.get("kr_day_state_root"))
        return snapshot

    monkeypatch.setattr(publisher_events, "collect_dashboard_snapshot_v2", observe)
    monkeypatch.setattr(publisher_events, "current_code_sha", lambda: "a" * 40)

    # When: the real event loop processes a KR ledger mutation.
    await publisher_events.watch_output_events(
        socket,
        outputs,
        anyio.Lock(),
        one_change,
        kr_day_state_root=state_root,
    )

    # Then: only the selected roots are watched and the same KR root rebuilds the snapshot.
    assert state_root.resolve() in watched[0]
    assert projected == [state_root]
    assert len(socket.messages) == 1


@pytest.mark.anyio
async def test_native_watch_emits_snapshot_when_explicit_kr_ledger_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real native watcher is subscribed to an explicit KR service root.
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    state_root = tmp_path / "kr-state"
    state_root.mkdir()
    socket = _Socket()
    snapshot = DashboardSnapshotV2.model_validate(snapshot_payload())
    monkeypatch.setattr(publisher_events, "collect_dashboard_snapshot_v2", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(publisher_events, "current_code_sha", lambda: "a" * 40)

    async def mutate_ledger() -> None:
        await anyio.sleep(0.1)
        (state_root / "kr-day-decisions.sqlite3").write_text("mutation", encoding="utf-8")

    # When: a real filesystem mutation lands in that root.
    with anyio.fail_after(8):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(
                publisher_events.watch_output_events,
                socket,
                outputs,
                anyio.Lock(),
                None,
                None,
                None,
                state_root,
            )
            tasks.start_soon(mutate_ledger)
            await socket.sent.wait()
            tasks.cancel_scope.cancel()

    # Then: the publisher emits exactly one rebuilt snapshot from the KR change.
    assert len(socket.messages) == 1


@pytest.mark.anyio
async def test_native_watch_emits_snapshot_when_explicit_kr_root_is_created_later(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only the narrow parent of an explicit service root exists at publisher startup.
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    state_parent = tmp_path / "kr-service"
    state_parent.mkdir()
    state_root = state_parent / "state"
    assert publisher_events.watch_roots(outputs, kr_day_state_root=state_root) == (state_parent,)
    socket = _Socket()
    snapshot = DashboardSnapshotV2.model_validate(snapshot_payload())
    monkeypatch.setattr(publisher_events, "collect_dashboard_snapshot_v2", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(publisher_events, "current_code_sha", lambda: "a" * 40)

    async def create_ledger_root() -> None:
        await anyio.sleep(0.1)
        state_root.mkdir()
        (state_root / "kr-day-decisions.sqlite3").write_text("mutation", encoding="utf-8")

    # When: the service creates its root and first immutable ledger after startup.
    with anyio.fail_after(8):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(
                publisher_events.watch_output_events,
                socket,
                outputs,
                anyio.Lock(),
                None,
                None,
                None,
                state_root,
            )
            tasks.start_soon(create_ledger_root)
            await socket.sent.wait()
            tasks.cancel_scope.cancel()

    # Then: the parent creation event rebuilds exactly one snapshot without a broad ancestor watch.
    assert len(socket.messages) == 1


@pytest.mark.anyio
async def test_reconnect_rebuild_uses_explicit_kr_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a relay that disconnects once and records the rebuild root.
    outputs = tmp_path / "outputs"
    state_root = tmp_path / "kr-state"
    observed: list[Path | None] = []
    snapshot = DashboardSnapshotV2.model_validate(snapshot_payload())

    class _Connection:
        async def __aenter__(self) -> _Socket:
            return _Socket()

        async def __aexit__(self, *_args: object) -> None:
            return None

    calls = 0

    async def disconnect(*_args: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("fixture disconnect")
        raise RuntimeError("stop")

    def observe(_outputs: Path, **settings: Path | None) -> DashboardSnapshotV2:
        observed.append(settings.get("kr_day_state_root"))
        return snapshot

    monkeypatch.setattr(relay_runtime, "connect", lambda *_args, **_kwargs: _Connection())
    monkeypatch.setattr(relay_runtime, "collect_dashboard_snapshot_v2", observe)
    monkeypatch.setattr(relay_runtime.anyio, "sleep", lambda _seconds: _completed())

    # When: the connection is rebuilt after the first disconnect.
    with pytest.raises(RuntimeError, match="stop"):
        await relay_runtime.relay_snapshots(
            outputs,
            "https://example.test",
            "redacted",
            snapshot,
            once=False,
            pair_browser=False,
            system_authority_verifier=None,
            event_connection=disconnect,
            pair_browser_once=disconnect,
            kr_day_state_root=state_root,
        )

    # Then: reconnect collection retains the operational KR authority root.
    assert observed == [state_root]
    assert "kr_day_state_root" in inspect.signature(relay_runtime.relay_snapshots).parameters


async def _completed() -> None:
    return None
