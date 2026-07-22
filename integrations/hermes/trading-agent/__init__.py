from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

_SYMBOL = re.compile(r"^(?:[A-Z0-9][A-Z0-9./-]{0,19}|[0-9]{6})$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_DATABASE = Path("outputs/hermes/delivery.sqlite3")
_ARM_DATABASE = Path("outputs/hermes/arm.sqlite3")
_LANE_REGISTRY = Path("outputs/lane_control/lane_registry.sqlite3")
_EXPERIMENT_LEDGER = Path("outputs/experiment_control/experiment_ledger.sqlite3")
ArmSchemaValue = str | bool | list[str] | dict[str, "ArmSchemaValue"]

_QUERY_SCHEMA = {
    "name": "trading_agent_query",
    "description": "Return separate point-in-time opinions from each trading agent for one symbol.",
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "US ticker or six-digit KR stock code."},
            "observed_at": {
                "type": "string",
                "description": "Optional timezone-aware ISO-8601 query time; defaults to current UTC.",
            },
        },
        "required": ["symbol"],
        "additionalProperties": False,
    },
}
_STATUS_SCHEMA = {
    "name": "trading_agent_status",
    "description": "Report whether the local trading-agent query and delivery surfaces are available.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _arm_schema(name: str, description: str, fields: tuple[str, ...]) -> dict[str, ArmSchemaValue]:
    parameters: dict[str, ArmSchemaValue] = {
        "type": "object",
        "properties": {field: {"type": "string"} for field in fields},
        "required": list(fields),
        "additionalProperties": False,
    }
    return {"name": name, "description": description, "parameters": parameters}


_ARM_PREPARE_SCHEMA = _arm_schema(
    "trading_agent_arm_prepare", "Prepare an expiring owner-bound Paper arm request.", ("session_id", "lane_id")
)
_ARM_CONFIRM_SCHEMA = _arm_schema(
    "trading_agent_arm_confirm", "Confirm one owner-bound Paper arm request.", ("request_id", "confirmation")
)
_ARM_REVOKE_SCHEMA = _arm_schema(
    "trading_agent_arm_revoke", "Revoke an unconsumed Paper arm request.", ("request_id",)
)


class InvalidTradingAgentPluginConfigurationError(ValueError):
    pass


def register(ctx) -> None:
    definitions = (
        ("trading_agent_query", _QUERY_SCHEMA, _query),
        ("trading_agent_status", _STATUS_SCHEMA, _status),
        ("trading_agent_arm_prepare", _ARM_PREPARE_SCHEMA, _arm_prepare),
        ("trading_agent_arm_confirm", _ARM_CONFIRM_SCHEMA, _arm_confirm),
        ("trading_agent_arm_revoke", _ARM_REVOKE_SCHEMA, _arm_revoke),
    )
    for name, schema, handler in definitions:
        ctx.register_tool(name=name, toolset="trading_agent", schema=schema, handler=handler)
    ctx.register_command(
        "trading-status",
        lambda raw: _status({}) if not raw.strip() else _blocked("invalid_arguments"),
        description="Show local trading-agent delivery status",
    )
    skill = Path(__file__).parent / "skills" / "trading-agent" / "SKILL.md"
    ctx.register_skill("trading-agent", skill, "Query separate trading-agent opinions and Paper status safely.")
    if os.environ.get("TRADING_AGENT_HERMES_DELIVERY_ENABLED") == "1":
        _start_delivery_worker()


class _PluginDeliveryStatus(StrEnum):
    DISABLED = "disabled"
    RUNNING = "running"
    BLOCKED_CONFIGURATION = "blocked_configuration"


class _PluginDeliveryState:
    """Mutable runtime status for the process-owned delivery worker."""

    __slots__ = ("status",)

    def __init__(self) -> None:
        self.status = _PluginDeliveryStatus.DISABLED


_DELIVERY_STATE: Final = _PluginDeliveryState()


def _start_delivery_worker() -> None:
    try:
        from .delivery_worker import start_delivery_daemon
        from .telegram_sender import HermesTelegramSender, InvalidHermesTelegramConfigurationError
    except ImportError:
        _DELIVERY_STATE.status = _PluginDeliveryStatus.BLOCKED_CONFIGURATION
        return
    try:
        root = _project_root()
        sender = HermesTelegramSender.from_hermes_config()
        _ = start_delivery_daemon(root / _DATABASE, sender)
        _DELIVERY_STATE.status = _PluginDeliveryStatus.RUNNING
    except (InvalidTradingAgentPluginConfigurationError, InvalidHermesTelegramConfigurationError):
        _DELIVERY_STATE.status = _PluginDeliveryStatus.BLOCKED_CONFIGURATION


def _delivery_worker_status() -> str:
    match _DELIVERY_STATE.status:
        case _PluginDeliveryStatus.RUNNING:
            from .delivery_worker import delivery_daemon_status

            return delivery_daemon_status()
        case _PluginDeliveryStatus.DISABLED | _PluginDeliveryStatus.BLOCKED_CONFIGURATION as status:
            return status.value
        case unreachable:
            assert_never(unreachable)


def _query(args: Mapping[str, str], **context: str) -> str:
    if set(args) - {"symbol", "observed_at"}:
        return _blocked("invalid_arguments")
    symbol = args.get("symbol", "")
    if _SYMBOL.fullmatch(symbol) is None:
        return _blocked("invalid_arguments")
    observed_at = args.get("observed_at") or dt.datetime.now(dt.UTC).isoformat()
    try:
        parsed = dt.datetime.fromisoformat(observed_at)
    except ValueError:
        return _blocked("invalid_arguments")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return _blocked("invalid_arguments")
    return _run_project_cli(
        "run_hermes_delivery.py",
        ("query", "--database", str(_DATABASE), "--symbol", symbol, "--observed-at", observed_at),
    )


def _status(args: Mapping[str, str], **context: str) -> str:
    if args:
        return _blocked("invalid_arguments")
    try:
        root = _project_root()
    except InvalidTradingAgentPluginConfigurationError:
        return _blocked("project_root_invalid")
    return _json(
        {
            "arm_gateway_available": (root / "run_hermes_arm_gateway.py").is_file(),
            "delivery_database_available": (root / _DATABASE).is_file(),
            "delivery_semantics": "at_least_once",
            "delivery_worker_status": _delivery_worker_status(),
            "query_available": (root / "run_hermes_delivery.py").is_file(),
            "result": "ready",
            "version": "1.2.0",
        }
    )


def _arm_prepare(args: Mapping[str, str], **context: str) -> str:
    return _arm(args, context, required=("lane_id", "session_id"))


def _arm_confirm(args: Mapping[str, str], **context: str) -> str:
    return _arm(args, context, required=("confirmation", "request_id"))


def _arm_revoke(args: Mapping[str, str], **context: str) -> str:
    return _arm(args, context, required=("request_id",))


def _arm(args: Mapping[str, str], context: Mapping[str, str], *, required: tuple[str, ...]) -> str:
    if set(args) != set(required) or any(_IDENTIFIER.fullmatch(args.get(name, "")) is None for name in required):
        return _blocked("invalid_arguments")
    owner_binding = _owner_binding(context)
    if owner_binding is None:
        return _blocked("owner_context_missing")
    try:
        gateway = _project_root() / "run_hermes_arm_gateway.py"
    except InvalidTradingAgentPluginConfigurationError:
        return _blocked("project_root_invalid")
    if not gateway.is_file():
        return _blocked("arm_gateway_unavailable")
    common = ("--database", str(_ARM_DATABASE), "--lane-registry", str(_LANE_REGISTRY))
    common += ("--experiment-ledger", str(_EXPERIMENT_LEDGER))
    if required == ("lane_id", "session_id"):
        command = (
            "prepare",
            *common,
            "--owner-id-hash",
            owner_binding,
            "--session-id",
            args["session_id"],
            "--lane-id",
            args["lane_id"],
        )
    elif required == ("confirmation", "request_id"):
        command = (
            "confirm",
            *common,
            "--owner-id-hash",
            owner_binding,
            "--request-id",
            args["request_id"],
            "--confirmation",
            args["confirmation"],
        )
    else:
        command = (
            "revoke",
            *common,
            "--owner-id-hash",
            owner_binding,
            "--request-id",
            args["request_id"],
        )
    return _run_project_cli("run_hermes_arm_gateway.py", command)


def _owner_binding(context: Mapping[str, str]) -> str | None:
    values = tuple(context.get(name, "").strip() for name in ("platform", "sender_id", "session_id"))
    if any(not value for value in values):
        return None
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _run_project_cli(script_name: str, arguments: tuple[str, ...]) -> str:
    try:
        root = _project_root()
        script = root / script_name
        uv = shutil.which("uv")
        if (
            script_name not in {"run_hermes_delivery.py", "run_hermes_arm_gateway.py"}
            or not script.is_file()
            or uv is None
        ):
            raise InvalidTradingAgentPluginConfigurationError
        completed = subprocess.run(
            (uv, "run", "python", str(script), *arguments),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise InvalidTradingAgentPluginConfigurationError
        return _json(payload)
    except (
        InvalidTradingAgentPluginConfigurationError,
        json.JSONDecodeError,
        OSError,
        subprocess.TimeoutExpired,
    ):
        return _blocked("project_cli_failed")


def _project_root() -> Path:
    raw = os.environ.get("TRADING_AGENT_PROJECT_ROOT", "")
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise InvalidTradingAgentPluginConfigurationError from None
    if not raw or not candidate.is_absolute() or resolved != candidate or not resolved.is_dir():
        raise InvalidTradingAgentPluginConfigurationError
    if not (resolved / "AGENTS.md").is_file() or not (resolved / "run_hermes_delivery.py").is_file():
        raise InvalidTradingAgentPluginConfigurationError
    return resolved


def _blocked(reason: str) -> str:
    return _json({"reason": reason, "result": "blocked"})


def _json(payload: Mapping[str, str | bool | int]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
