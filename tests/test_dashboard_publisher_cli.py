from __future__ import annotations

import ast
import datetime as dt
import inspect
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner
from websockets.exceptions import WebSocketException

import run_dashboard_publisher
import trading_agent.dashboard_relay as dashboard_relay
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_commands import InteractionPayload
from trading_agent.dashboard_directed_research import AuthoritativeDirectedResearchBroker
from trading_agent.dashboard_directed_research_models import (
    DirectedResearchKind,
    DirectedResearchReceipt,
)
from trading_agent.dashboard_execution_claims import InteractiveClaimStore
from trading_agent.dashboard_native_watch import watch_native_changes
from trading_agent.dashboard_publisher_events import (
    publisher_url,
    reconnect_delay_seconds,
    watch_output_events,
    watch_roots,
)
from trading_agent.dashboard_publisher_pairing import forward_pairing_signals
from trading_agent.dashboard_relay import is_reconnectable_group, pairing_url, run_interaction


class _SendSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.sent = anyio.Event()

    async def send(self, message: str) -> None:
        self.messages.append(message)
        self.sent.set()


class _EventSocket(_SendSocket):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def __aiter__(self) -> _EventSocket:
        return self

    async def __anext__(self) -> str:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


class _FailingDirectedSocket(_SendSocket):
    def __init__(self, fail_kind: str) -> None:
        super().__init__()
        self._fail_kind = fail_kind
        self._failed = False

    async def send(self, message: str) -> None:
        payload = json.loads(message)
        if not self._failed and payload.get("kind") == self._fail_kind:
            self._failed = True
            raise OSError("directed event disconnect")
        await super().send(message)


def test_dashboard_publisher_help() -> None:
    result = CliRunner().invoke(run_dashboard_publisher.app, ["--help"])

    assert result.exit_code == 0
    assert "redacted" in result.stdout
    assert "--once" in result.stdout
    assert "--pair-browser" in result.stdout
    assert "--system-authority-" in result.stdout
    assert "Ed25519" in result.stdout
    assert "--interval-seconds" not in result.stdout


def test_publisher_watch_requires_explicit_system_authority_verifier() -> None:
    parameters = inspect.signature(watch_output_events).parameters

    assert "system_authority_verifier" in parameters


def test_dashboard_conversation_reset_help_bad_and_happy_path(tmp_path: Path) -> None:
    # Given: a local family binding created without network access
    from trading_agent.dashboard_hermes_sessions import HermesSessionBindingStore

    state = tmp_path / "state"
    missing_outputs = tmp_path / "archive-without-outputs"
    bindings = HermesSessionBindingStore(state / "hermes-sessions")
    bindings.capture("market_context", "session-market-context-001")

    # When: help, invalid family, and the exact local reset command run
    help_result = CliRunner().invoke(
        run_dashboard_publisher.app,
        ["--outputs", str(missing_outputs), "reset-conversation", "--help"],
    )
    bad = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(missing_outputs),
            "reset-conversation",
            "--family",
            "delivery",
            "--state-root",
            str(state),
        ],
    )
    happy = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(missing_outputs),
            "reset-conversation",
            "--family",
            "market_context",
            "--state-root",
            str(state),
        ],
    )

    # Then: only the canonical happy path removes the local binding
    assert help_result.exit_code == 0
    assert bad.exit_code != 0
    assert happy.exit_code == 0
    assert "RESET_OK" in happy.stdout
    assert bindings.session_for("market_context") is None


def test_archive_without_outputs_dispatches_autonomous_help_and_rejects_legacy_publish(
    tmp_path: Path,
) -> None:
    # Given: a clean shipped archive with no default output tree
    missing_outputs = tmp_path / "archive-without-outputs"
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=https://example.test\n"
        "DASHBOARD_INGEST_TOKEN=fixture-value-with-adequate-length\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)

    # When: a command-local help path and the legacy root publish path are invoked
    autonomous_help = CliRunner().invoke(
        run_dashboard_publisher.app,
        ["--outputs", str(missing_outputs), "autonomous-agent", "--help"],
    )
    legacy_publish = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(missing_outputs),
            "--credentials",
            str(credentials),
            "--dry-run",
        ],
    )

    # Then: dispatch help works while actual publishing validates its own required input
    assert autonomous_help.exit_code == 0
    assert "--trigger-fixture" in autonomous_help.stdout
    assert legacy_publish.exit_code != 0
    assert "outputs_directory_missing" in legacy_publish.output


def test_dashboard_publisher_rejects_non_https_remote_url(tmp_path: Path) -> None:
    missing_outputs = tmp_path / "archive-without-outputs"
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=http://railway.example\nDASHBOARD_INGEST_TOKEN=token-with-adequate-length-123\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)
    before = tuple(tmp_path.iterdir())

    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(missing_outputs),
            "--credentials",
            str(credentials),
            "--once",
        ],
    )

    assert result.exit_code != 0
    assert "invalid_settings" in result.output
    assert "outputs_directory_missing" not in result.output
    assert tuple(tmp_path.iterdir()) == before


def test_dashboard_publisher_dry_run_emits_canonical_v2_json(tmp_path: Path) -> None:
    # Given a mode-600 publisher boundary and accepted redacted source receipt
    outputs = tmp_path / "outputs"
    source_root = outputs / "experiment_control"
    source_root.mkdir(parents=True)
    receipt = source_root / "dashboard-receipts.v2.jsonl"
    observed_at = dt.datetime.now(dt.UTC)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "snapshot_epoch": "cli-fixture",
                "workspace": "research",
                "item_id": "research.cli",
                "kind": "research",
                "label": "CLI fixture",
                "value": "accepted",
                "observed_at": observed_at.isoformat(),
                "safe_ref": "c" * 64,
                "terminal_kind": "reviewer_decision",
                "state": "populated",
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=https://example.test\nDASHBOARD_INGEST_TOKEN=fixture-value-with-adequate-length\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)

    # When the real CLI dry-run boundary executes
    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(outputs),
            "--credentials",
            str(credentials),
            "--dry-run",
        ],
    )

    # Then it emits a strict canonical v2 payload without sending externally
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 2
    assert payload["workspaces"]["research"]["state"] == "unavailable"


def test_publisher_dry_run_cli_terminates_from_controlled_private_fixture(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir(mode=0o700)
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=https://example.test\n"
        "DASHBOARD_INGEST_TOKEN=fixture-value-with-adequate-length\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)

    result = subprocess.run(
        (
            sys.executable,
            str(Path(run_dashboard_publisher.__file__)),
            "publish",
            "--outputs",
            str(outputs),
            "--credentials",
            str(credentials),
            "--dry-run",
            "--once",
        ),
        check=False,
        capture_output=True,
        cwd=Path(run_dashboard_publisher.__file__).parent,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["schema_version"] == 2


def test_publisher_rejects_ambiguous_root_publish_options_before_opening_a_relay(tmp_path: Path) -> None:
    result = subprocess.run(
        (
            sys.executable,
            str(Path(run_dashboard_publisher.__file__)),
            "--dry-run",
            "--once",
            "publish",
        ),
        check=False,
        capture_output=True,
        cwd=Path(run_dashboard_publisher.__file__).parent,
        env={**os.environ, "HOME": str(tmp_path / "empty-home")},
        text=True,
        timeout=5,
    )

    assert result.returncode != 0
    assert "publish_options_must_follow_subcommand" in result.stderr


def test_publisher_uses_websocket_events_without_periodic_http_or_sleep() -> None:
    source = Path(run_dashboard_publisher.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        root
        for node in ast.walk(tree)
        for root in (
            [name.name.partition(".")[0] for name in node.names]
            if isinstance(node, ast.Import)
            else [node.module.partition(".")[0]]
            if isinstance(node, ast.ImportFrom) and node.module is not None
            else []
        )
    }
    called_names = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "httpx2" not in imported_roots
    assert "post" not in called_names
    assert "websockets" in imported_roots
    assert "watchfiles" not in imported_roots
    assert run_dashboard_publisher.watch_output_events.__module__ == "trading_agent.dashboard_publisher_events"
    assert watch_native_changes.__module__ == "trading_agent.dashboard_native_watch"


def test_publisher_converts_dashboard_urls_to_publish_websockets() -> None:
    https_url = publisher_url("https://observatory.example")
    http_url = publisher_url("http://localhost:3100")
    assert https_url == "wss://observatory.example/api/realtime/publish"
    assert http_url == "ws://localhost:3100/api/realtime/publish"
    assert (
        pairing_url(
            "https://observatory.example",
            "/operator/pair/single-use-ticket",
        )
        == "https://observatory.example/operator/pair/single-use-ticket"
    )


@pytest.mark.anyio
async def test_pairing_browser_open_uses_fixed_macos_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, str], bool]] = []

    class _Completed:
        returncode = 0

    async def run_process(arguments: tuple[str, str], *, check: bool) -> _Completed:
        calls.append((arguments, check))
        return _Completed()

    monkeypatch.setattr(dashboard_relay.anyio, "run_process", run_process)

    await dashboard_relay.open_pairing_url("https://observatory.example/operator/pair/opaque")

    assert calls == [(("/usr/bin/open", "https://observatory.example/operator/pair/opaque"), False)]


@pytest.mark.anyio
async def test_resident_publisher_sigusr1_coalesces_one_ticket_request_and_keeps_ticket_out_of_socket_output() -> None:
    # Given: the already-connected publisher and a received pairing ticket.
    pairing_path = f"/operator/pair/{'x' * 40}"
    socket = _EventSocket([json.dumps({"type": "pairing_ticket", "path": pairing_path})])
    pairing = run_dashboard_publisher.PairingRequestState()
    opened: list[str] = []

    async def signals() -> AsyncIterator[signal.Signals]:
        yield signal.SIGUSR1
        yield signal.SIGUSR1

    async def open_browser(url: str) -> None:
        opened.append(url)

    # When: two operator signals arrive before the one ticket response.
    await forward_pairing_signals(
        signals(),
        run_dashboard_publisher.PairingRequestRuntime(socket, anyio.Lock(), pairing),
    )
    async with anyio.create_task_group() as tasks:
        receiver = run_dashboard_publisher.PublisherEventReceiver(
            run_dashboard_publisher.PairingTicketHandler(
                "https://observatory.example",
                False,
                pairing,
                open_browser,
            ),
            run_dashboard_publisher.InteractionRuntime(
                Path("outputs"),
                anyio.Lock(),
                anyio.CapacityLimiter(1),
                tasks,
                Path("hermes"),
                Path("worktree"),
                Path("state"),
            ),
        )
        await run_dashboard_publisher._receive_events(socket, receiver)

    # Then: the resident socket requests one ticket, opens it once, and writes neither ticket nor path.
    assert socket.messages == ['{"type":"pairing_request"}']
    assert opened == [f"https://observatory.example{pairing_path}"]
    assert pairing.pending is False
    assert pairing_path not in "".join(socket.messages)


def test_publisher_watches_account_ledger_without_periodic_broker_reads(
    tmp_path: Path,
) -> None:
    for name in (
        "live_sessions",
        "source_evidence",
        "experiment_control",
        "lane_control",
        "kr_theme",
        "derivatives",
        "paper",
        "system",
    ):
        (tmp_path / name).mkdir()

    assert watch_roots(tmp_path) == (
        tmp_path / "live_sessions",
        tmp_path / "source_evidence",
        tmp_path / "experiment_control",
        tmp_path / "lane_control",
        tmp_path / "kr_theme",
        tmp_path / "derivatives",
        tmp_path / "paper",
        tmp_path / "system",
    )


@pytest.mark.anyio
async def test_publisher_watch_roots_coalesce_one_mutation_each(
    tmp_path: Path,
) -> None:
    # Given every stable root and the production native event source
    for name in (
        "live_sessions",
        "source_evidence",
        "experiment_control",
        "lane_control",
        "kr_theme",
        "derivatives",
        "paper",
        "system",
    ):
        (tmp_path / name).mkdir()
    roots = watch_roots(tmp_path)
    socket = _SendSocket()

    async def mutate_all_roots() -> None:
        await anyio.sleep(0.1)
        for root in roots:
            mutation = root / "mutation.receipt"
            mutation.write_text(root.name, encoding="utf-8")

    # When real OS file mutations arrive in one burst
    with anyio.fail_after(8):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(
                watch_output_events,
                socket,
                tmp_path,
                anyio.Lock(),
            )
            tasks.start_soon(mutate_all_roots)
            await socket.sent.wait()
            tasks.cancel_scope.cancel()

    # Then the publisher rebuilds and sends exactly one coalesced snapshot event
    assert all((root / "mutation.receipt").read_text(encoding="utf-8") == root.name for root in roots)
    assert len(socket.messages) == 1
    assert json.loads(socket.messages[0])["snapshot"]["schema_version"] == 2


@pytest.mark.anyio
async def test_publisher_idle_watch_does_no_projection_or_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an idle event subscription with counters on observable work
    socket = _SendSocket()
    projections = 0

    async def idle_watch(
        *_paths: Path,
        **_settings: int,
    ) -> AsyncIterator[frozenset[Path]]:
        await anyio.Event().wait()
        yield frozenset()

    def counted_projection(_outputs: Path):
        nonlocal projections
        projections += 1
        return run_dashboard_publisher.collect_dashboard_snapshot_v2(_outputs)

    monkeypatch.setattr("trading_agent.dashboard_publisher_events.collect_dashboard_snapshot_v2", counted_projection)

    # When idleness is observed for a bounded interval
    with anyio.move_on_after(0.05):
        await watch_output_events(
            socket,
            tmp_path,
            anyio.Lock(),
            idle_watch,
        )

    # Then no snapshot/database projection, send, HTTP poll, or model call occurs
    assert projections == 0
    assert socket.messages == []


def test_publisher_bounds_reconnect_backoff() -> None:
    assert [reconnect_delay_seconds(index) for index in range(7)] == [
        5,
        10,
        20,
        40,
        60,
        60,
        60,
    ]


def test_publisher_records_exact_six_family_three_channel_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReadyBoundary:
        def blocker(self, _trigger: object) -> None:
            return None

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(
        run_dashboard_publisher,
        "DEFAULT_AUTONOMOUS_STATE",
        tmp_path / "autonomous",
    )
    monkeypatch.setattr(
        run_dashboard_publisher,
        "current_code_sha",
        lambda: "a" * 40,
    )
    monkeypatch.setattr(
        run_dashboard_publisher,
        "create_production_execution_boundary",
        lambda **_settings: ReadyBoundary(),
    )

    run_dashboard_publisher._record_agent_readiness(outputs)
    snapshot = run_dashboard_publisher.collect_dashboard_snapshot_v2(outputs)

    assert len(tuple((outputs / "system" / "agent-runtime").glob("*.json"))) == 18
    assert tuple(
        agent.runtime_state
        for agent in snapshot.workspaces.command_center.agents
    ) == ("armed",) * 6


def test_publisher_classifies_nested_websocket_failures_for_reconnect() -> None:
    failure = ExceptionGroup(
        "publisher connection",
        [ExceptionGroup("receive", [WebSocketException("publisher replaced")])],
    )

    assert is_reconnectable_group(failure)


@pytest.mark.anyio
async def test_publisher_reports_running_then_terminal_command_state(tmp_path: Path) -> None:
    socket = _SendSocket()
    hermes = tmp_path / "fake-hermes"
    hermes.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'event':'complete','text':'done',"
        "'session_id':'session-market-context-001','failed':False,'error':None}))\n"
    )
    hermes.chmod(0o700)
    interaction = InteractionPayload.model_validate(
        {
            "id": "019c0014-f0f5-7000-8000-000000000001",
            "agent_id": "market_context",
            "mode": "conversation",
            "command": "결손을 요약해줘",
            "state": "queued",
            "response": None,
            "created_at": "2026-07-26T04:00:00Z",
            "updated_at": "2026-07-26T04:00:00Z",
        }
    )

    await run_interaction(
        socket,
        interaction,
        anyio.Lock(),
        anyio.CapacityLimiter(1),
        hermes,
        tmp_path,
        tmp_path / "interactive-state",
        tmp_path,
    )

    states = [json.loads(message)["state"] for message in socket.messages]
    assert states == ["running", "completed"]


@pytest.mark.anyio
async def test_directed_relay_streams_persisted_progress_before_blocked_broker_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Hermes yields a strict plan and the authoritative broker blocks mid-step
    socket = _SendSocket()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def blocked_execute(
        _broker: AuthoritativeDirectedResearchBroker,
        operation: DirectedResearchKind,
        family_id: AgentFamilyId,
    ) -> bytes:
        nonlocal calls
        del family_id
        calls += 1
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError
        return (
            DirectedResearchReceipt(
                operation=operation,
                terminal="completed",
                domain_effects=1,
                evidence_sha256s=("a" * 64,),
                result_sha256="b" * 64,
                summary="authoritative broker completed",
            )
            .model_dump_json()
            .encode()
        )

    monkeypatch.setattr(AuthoritativeDirectedResearchBroker, "execute", blocked_execute)
    hermes = tmp_path / "fake-directed-hermes"
    hermes.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "plan = json.dumps({'schema_version':1,'operation':'hypothesis','intent':'bounded'})\n"
        "print(json.dumps({'event':'complete','text':plan,"
        "'session_id':'session-directed-relay-001','failed':False,'error':None}))\n"
    )
    hermes.chmod(0o700)
    interaction = InteractionPayload.model_validate(
        {
            "id": "019c0014-f0f5-7000-8000-000000000020",
            "agent_id": "opportunity_manager",
            "mode": "hypothesis",
            "command": "가설을 등록해줘",
            "state": "queued",
            "response": None,
            "created_at": "2026-07-26T04:00:00Z",
            "updated_at": "2026-07-26T04:00:00Z",
        }
    )
    state = tmp_path / "interactive-state"
    arguments = (
        socket,
        interaction,
        anyio.Lock(),
        anyio.CapacityLimiter(1),
        hermes,
        tmp_path,
        state,
        tmp_path,
    )

    # When: the relay runs while the broker remains blocked
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run_interaction, *arguments)
        with anyio.fail_after(5):
            while not started.is_set() or len(socket.messages) < 2:
                await anyio.sleep(0.01)
        before_release = [json.loads(message) for message in socket.messages]
        assert [item.get("kind") for item in before_release] == [None, "progress"]
        assert all(item.get("kind") != "result" for item in before_release)
        release.set()

    # Then: progress arrived and persisted first, terminal evidence/result followed, replay did no work
    event_log = (state / "directed-jobs" / interaction.id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in event_log] == [
        "progress",
        "evidence",
        "result",
    ]
    replay_start = len(socket.messages)
    await run_interaction(*arguments)
    replayed = [json.loads(message) for message in socket.messages[replay_start:]]
    assert calls == 1
    assert [item.get("kind") for item in replayed] == [
        None,
        "progress",
        "evidence",
        "result",
        None,
    ]
    assert replayed[-1]["state"] == "completed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fail_kind", "expected_broker_calls", "expected_state"),
    [
        ("progress", 0, "failed"),
        ("evidence", 1, "completed"),
        ("result", 1, "completed"),
    ],
)
async def test_directed_disconnect_persists_one_authoritative_terminal_without_relaunch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_kind: str,
    expected_broker_calls: int,
    expected_state: str,
) -> None:
    # Given: a relay disconnects while sending one persisted directed event
    calls = 0

    def execute_broker(
        _broker: AuthoritativeDirectedResearchBroker,
        operation: DirectedResearchKind,
        family_id: AgentFamilyId,
    ) -> bytes:
        nonlocal calls
        del family_id
        calls += 1
        return (
            DirectedResearchReceipt(
                operation=operation,
                terminal="completed",
                domain_effects=1,
                evidence_sha256s=("a" * 64,),
                result_sha256="b" * 64,
                summary="authoritative broker completed",
            )
            .model_dump_json()
            .encode()
        )

    monkeypatch.setattr(AuthoritativeDirectedResearchBroker, "execute", execute_broker)
    hermes = tmp_path / "fake-disconnect-hermes"
    count = tmp_path / "hermes-count"
    hermes.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"open({str(count)!r}, 'a').write('1\\n')\n"
        "plan = json.dumps({'schema_version':1,'operation':'hypothesis','intent':'bounded'})\n"
        "print(json.dumps({'event':'complete','text':plan,"
        "'session_id':'session-disconnect-001','failed':False,'error':None}))\n"
    )
    hermes.chmod(0o700)
    interaction = InteractionPayload.model_validate(
        {
            "id": "019c0014-f0f5-7000-8000-000000000021",
            "agent_id": "opportunity_manager",
            "mode": "hypothesis",
            "command": "가설을 등록해줘",
            "state": "queued",
            "response": None,
            "created_at": "2026-07-26T04:00:00Z",
            "updated_at": "2026-07-26T04:00:00Z",
        }
    )
    state = tmp_path / "state"
    arguments = (
        interaction,
        anyio.Lock(),
        anyio.CapacityLimiter(1),
        hermes,
        tmp_path,
        state,
        tmp_path,
    )

    # When: the failing relay runs and a fresh socket reconnects the exact interaction
    await run_interaction(
        _FailingDirectedSocket(fail_kind),
        *arguments,
    )
    reconnect = _SendSocket()
    await run_interaction(reconnect, *arguments)

    # Then: one terminal and the matching claim replay without another Hermes or broker launch
    event_log = (state / "directed-jobs" / interaction.id / "events.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in event_log.splitlines()]
    replayed = [json.loads(message) for message in reconnect.messages]
    terminals = [event for event in events if event["kind"] == "result"]
    replayed_terminals = [event for event in replayed if event.get("kind") == "result"]
    claim = InteractiveClaimStore(state / "interactive-claims.sqlite3").get(interaction.id)
    assert len(terminals) == 1
    assert terminals[0]["state"] == expected_state
    assert len(replayed_terminals) == 1
    assert replayed_terminals[0]["state"] == expected_state
    assert replayed[-1]["state"] == expected_state
    assert claim is not None
    assert claim.state == expected_state
    assert calls == expected_broker_calls
    assert count.read_text().splitlines() == ["1"]
