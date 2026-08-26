#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11", "httpx2[http2,brotli,zstd]", "websockets>=16,<17"]
# ///
# ─── How to run ───
# uv run --offline python run_local_browser_gateway.py --help

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import signal
import sqlite3
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import NoReturn, assert_never

from pydantic import ValidationError

from trading_agent.chrome_devtools_transport import LoopbackChromeHealthProbe
from trading_agent.local_browser_dispatch import BrowserDispatchDependencies, BrowserRequestDispatcher
from trading_agent.local_browser_gateway import (
    LocalBrowserGateway,
    LoopbackBrowserClientFactory,
    canonical_browser_response,
)
from trading_agent.local_browser_gateway_config import (
    InvalidLocalBrowserGatewayConfigError,
    LocalBrowserGatewayConfig,
    LocalBrowserLaunchAgentVerification,
    load_local_browser_gateway_config,
    verify_local_browser_launch_agent,
    write_local_browser_gateway_config,
    write_local_browser_launch_agent,
)
from trading_agent.local_browser_protocol import BrowserStatusRequest
from trading_agent.local_browser_receipts import InvalidLocalBrowserReceiptError, LocalBrowserReceiptStore
from trading_agent.local_browser_socket import (
    InvalidLocalBrowserSocketError,
    LocalBrowserSocketBusyError,
    LocalBrowserSocketClient,
    LocalBrowserSocketServer,
)
from trading_agent.local_chrome_controller import InvalidLocalChromeControllerError, LocalChromeController
from trading_agent.repository_current_main import CurrentMainAuthorityError, current_main_commit

CommandRunner = Callable[[tuple[str, ...]], int]


class LocalBrowserGatewayBusyError(RuntimeError):
    pass


class OperatorCommand(StrEnum):
    PROVISION = "provision"
    VERIFY = "verify"
    RUN = "run"
    STATUS = "status"
    ACTIVATE = "activate"


def main(argv: Sequence[str] | None = None, *, runner: CommandRunner | None = None) -> int:
    try:
        args = _parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    try:
        command = OperatorCommand(args.command)
        match command:
            case OperatorCommand.PROVISION:
                verification = _provision(args)
                print(_verification_json(verification))
                return 0
            case OperatorCommand.VERIFY:
                print(_verification_json(verify_local_browser_launch_agent(args.config, args.plist)))
                return 0
            case OperatorCommand.RUN:
                _run_gateway(load_local_browser_gateway_config(args.config))
                return 0
            case OperatorCommand.STATUS:
                return _status(load_local_browser_gateway_config(args.config))
            case OperatorCommand.ACTIVATE:
                return _activate(args, _default_runner if runner is None else runner)
            case unreachable:
                assert_never(unreachable)
    except LocalBrowserGatewayBusyError:
        return 3
    except (
        CurrentMainAuthorityError,
        InvalidLocalBrowserGatewayConfigError,
        InvalidLocalBrowserReceiptError,
        InvalidLocalBrowserSocketError,
        InvalidLocalChromeControllerError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        return 2
    except Exception:  # noqa: RUF100  # noqa: BROAD_EXCEPT_OK: CLI boundary redacts unexpected failures
        return 2


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operate the private local browser gateway")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision")
    for option in (
        "project-root",
        "uv-path",
        "chrome-executable",
        "state-root",
        "profile-root",
        "socket-path",
        "receipt-database",
        "screenshot-root",
        "config",
        "plist",
    ):
        provision.add_argument(f"--{option}", type=Path, required=True)
    provision.add_argument("--startup-timeout-seconds", type=float, default=30.0)
    provision.add_argument("--command-timeout-seconds", type=float, default=20.0)
    verify = commands.add_parser("verify")
    verify.add_argument("--config", type=Path, required=True)
    verify.add_argument("--plist", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--config", type=Path, required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--config", type=Path, required=True)
    activate.add_argument("--plist", type=Path, required=True)
    return parser.parse_args(argv)


def _provision(args: argparse.Namespace) -> LocalBrowserLaunchAgentVerification:
    config = LocalBrowserGatewayConfig(
        project_root=_absolute(args.project_root),
        uv_path=_absolute(args.uv_path),
        chrome_executable=_absolute(args.chrome_executable),
        state_root=_absolute(args.state_root),
        profile_root=_absolute(args.profile_root),
        socket_path=_absolute(args.socket_path),
        receipt_database=_absolute(args.receipt_database),
        screenshot_root=_absolute(args.screenshot_root),
        startup_timeout_seconds=args.startup_timeout_seconds,
        command_timeout_seconds=args.command_timeout_seconds,
    )
    config_path, plist_path = _absolute(args.config), _absolute(args.plist)
    _ = write_local_browser_gateway_config(config_path, config)
    _ = write_local_browser_launch_agent(plist_path, config, config_path)
    return verify_local_browser_launch_agent(config_path, plist_path)


def _run_gateway(config: LocalBrowserGatewayConfig) -> None:
    controller = LocalChromeController(
        config,
        probe=LoopbackChromeHealthProbe(timeout_seconds=config.command_timeout_seconds),
    )
    dependencies = BrowserDispatchDependencies(
        controller=controller,
        client_factory=LoopbackBrowserClientFactory(timeout_seconds=config.command_timeout_seconds),
        now=lambda: dt.datetime.now(dt.UTC),
    )
    try:
        with (
            LocalBrowserReceiptStore(config.receipt_database) as store,
            LocalBrowserSocketServer(
                config.socket_path,
                LocalBrowserGateway(store, BrowserRequestDispatcher(dependencies, config.screenshot_root)),
            ) as server,
            _gateway_signals(),
        ):
            try:
                _ = controller.ensure_ready()
            except InvalidLocalChromeControllerError as error:
                if error.reason == "local_chrome_profile_busy":
                    raise LocalBrowserGatewayBusyError from None
                raise
            while True:
                try:
                    server.serve_once()
                except InvalidLocalBrowserSocketError:
                    continue
    except LocalBrowserSocketBusyError:
        raise LocalBrowserGatewayBusyError from None
    except KeyboardInterrupt:
        return
    finally:
        controller.close()


def _status(config: LocalBrowserGatewayConfig) -> int:
    request = BrowserStatusRequest(request_id=secrets.token_hex(32))
    response = LocalBrowserSocketClient(
        config.socket_path,
        timeout_seconds=config.command_timeout_seconds,
    ).request(request)
    print(canonical_browser_response(response).decode("utf-8"))
    return 0 if response.status_payload is not None and response.status_payload.ready else 2


def _activate(args: argparse.Namespace, runner: CommandRunner) -> int:
    _ = verify_local_browser_launch_agent(args.config, args.plist)
    config = load_local_browser_gateway_config(args.config)
    _ = current_main_commit(config.project_root)
    domain = f"gui/{os.getuid()}"
    plist = str(_absolute(args.plist))
    if runner(("/bin/launchctl", "bootstrap", domain, plist)) != 0:
        return 2
    target = f"{domain}/{config.label}"
    if runner(("/bin/launchctl", "kickstart", target)) != 0:
        _ = runner(("/bin/launchctl", "bootout", domain, plist))
        return 2
    return 0


@contextmanager
def _gateway_signals() -> Iterator[None]:
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        for number in previous:
            signal.signal(number, _stop_signal)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _stop_signal(_number: int, _frame: FrameType | None) -> NoReturn:
    raise KeyboardInterrupt


def _default_runner(command: tuple[str, ...]) -> int:
    return subprocess.run(
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def _verification_json(verification: LocalBrowserLaunchAgentVerification) -> str:
    return json.dumps(
        {
            "broker_mutation": 0,
            "config_sha256": verification.config_sha256,
            "plist_sha256": verification.plist_sha256,
            "status": "verified",
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


if __name__ == "__main__":
    raise SystemExit(main())
