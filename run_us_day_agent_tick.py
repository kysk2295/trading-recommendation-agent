from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final, Literal, override

import typer
from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, ValidationError

from trading_agent.alpaca_paper_config import load_alpaca_paper_credentials
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_models import DayAgentReasoningRequest, DayAgentReasoningResponse
from trading_agent.day_agent_tool_runtime import DayAgentToolRuntime
from trading_agent.day_agent_version_models import AgentVersion
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_arm_authority import LedgerHermesArmAuthorityConfig, LedgerHermesArmAuthorityResolver
from trading_agent.hermes_arm_gateway import HermesArmGateway, HermesArmGatewayConfig
from trading_agent.hermes_arm_signing import HermesArmSigner, load_hermes_arm_signing_key
from trading_agent.hermes_arm_store import HermesArmStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, read_private_text
from trading_agent.researcher_llm import HermesCliProposalClient, ResearcherLlmError
from trading_agent.store import PaperStore
from trading_agent.us_day_agent_cli_bindings import (
    ProductionBindingError,
    ProductionManifest,
    build_close_bindings,
    load_production_manifest,
    resolve_strategy_binding,
)
from trading_agent.us_day_agent_operating import UsDayAgentOperatingServices
from trading_agent.us_day_agent_service import (
    CanonicalUsDaySource,
    LedgerUsDayExecutionAuthorityReader,
    LiveUsDayPaperSessionControl,
    LocalUsDaySourceReader,
    UsDayAgentServiceConfig,
    UsDayAgentServiceError,
    UsDayLocalStores,
    UsDayModelBindings,
    UsDayPaperBindings,
    UsDayProductionConfig,
    build_us_day_agent_service,
    session_phase_at,
)
from trading_agent.us_day_live_models import UsDayStructuredReasoner, UsDayStructuredThesisReasoner
from trading_agent.us_day_operating_arm import StrategyBoundHermesArmConsumer
from trading_agent.us_day_operating_coordinator import UsDayOperatingCoordinator, UsDayOperatingCoordinatorConfig
from trading_agent.us_day_signal_admission import UsDayMarketLiquidityContext
from trading_agent.us_day_thesis_store import UsDayThesisStore
from trading_agent.us_equity_calendar import NEW_YORK

_APP = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
_DAY_RESPONSES = TypeAdapter(tuple[DayAgentReasoningResponse, ...])
_CLAUDE_EXECUTABLE: Final = Path.home() / ".local" / "lib" / "trading-agent" / "claude-code-2.1.222"


class _SourceHeader(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    session_id: str
    evaluated_at: AwareDatetime


class _OneShareLiquidity:
    def allowed_quantity(self, context: UsDayMarketLiquidityContext) -> int:
        return 1


class _CliError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


class _BoundDayResponses:
    role: Literal["reasoning", "coding"] = "reasoning"

    def __init__(self, responses: tuple[DayAgentReasoningResponse, ...]) -> None:
        self._responses = responses
        self._index = 0

    def next_step(self, request: DayAgentReasoningRequest) -> DayAgentReasoningResponse:
        if self._index >= len(self._responses):
            raise _CliError("day_model_responses_exhausted")
        response = self._responses[self._index]
        self._index += 1
        return response


class _BoundThesisResponse:
    def __init__(self, response: Mapping[str, object]) -> None:
        self._response = dict(response)

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        return self._response


def _now(value: str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.UTC)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _CliError("now_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _CliError("now_invalid")
    return parsed.astimezone(dt.UTC)


def _source(path: Path, now: dt.datetime) -> CanonicalUsDaySource:
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            raise _CliError("situation_metadata_invalid")
        payload = path.read_bytes()
        decoded = TypeAdapter(dict[str, object]).validate_json(payload)
        header = _SourceHeader.model_validate(decoded.get("situation", decoded))
    except FileNotFoundError:
        raise _CliError("situation_missing") from None
    except OSError:
        raise _CliError("situation_read_failed") from None
    except ValidationError:
        raise _CliError("situation_invalid") from None
    observed_at = header.evaluated_at.astimezone(dt.UTC)
    if (
        header.session_id != f"XNYS-{now.astimezone(NEW_YORK).date().isoformat()}"
        or observed_at > now
        or now - observed_at > dt.timedelta(minutes=15)
    ):
        raise _CliError("situation_stale")
    try:
        return CanonicalUsDaySource.model_validate_json(payload)
    except (ValidationError, ValueError):
        raise _CliError("situation_invalid") from None


def _model_bindings(
    day_path: Path | None,
    thesis_path: Path | None,
    live_model_provider: str | None,
    champion: AgentVersion,
    clock: dt.datetime,
) -> UsDayModelBindings:
    if day_path is None and thesis_path is None and live_model_provider is not None:
        reasoning = tuple(item for item in champion.model_role_bindings if item.role == "reasoning")
        if len(reasoning) != 1:
            raise _CliError("champion_reasoning_model_binding_invalid")
        try:
            client = HermesCliProposalClient(
                _CLAUDE_EXECUTABLE,
                reasoning[0].model_id,
                live_model_provider,
            )
        except ResearcherLlmError:
            raise _CliError("live_model_configuration_invalid") from None
        return UsDayModelBindings(
            UsDayStructuredReasoner(client),
            UsDayStructuredThesisReasoner(client),
            DayAgentToolRuntime((), lambda: clock),
        )
    if day_path is None or thesis_path is None or live_model_provider is not None:
        raise _CliError("model_bindings_invalid")
    try:
        day = _DAY_RESPONSES.validate_json(read_private_text(day_path))
        thesis = TypeAdapter(dict[str, object]).validate_json(read_private_text(thesis_path))
    except (InvalidPrivateImmutableFileError, ValidationError, ValueError):
        raise _CliError("model_bindings_invalid") from None
    return UsDayModelBindings(
        _BoundDayResponses(day),
        _BoundThesisResponse(thesis),
        DayAgentToolRuntime((), lambda: clock),
    )


def _service(
    outputs: Path,
    version_store_path: Path,
    source: CanonicalUsDaySource,
    production_manifest: Path | None,
    strategy_manifest: Path | None,
    experiment_ledger: Path | None,
    day_model_responses: Path | None,
    thesis_model_response: Path | None,
    live_model_provider: str | None,
    evaluated_at: dt.datetime,
    entry_cutoff_minutes: int,
    eod_minutes: int,
):
    root = outputs.expanduser().absolute()
    private = root / "us_day"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    private.chmod(0o700)
    thesis_store = UsDayThesisStore(private / "theses")
    version_store = DayAgentVersionStore(version_store_path)
    champion = version_store.reader().champion() if version_store.path.is_file() else None
    if champion is None:
        raise _CliError("existing_champion_store_required")
    try:
        manifest = None if production_manifest is None else load_production_manifest(production_manifest)
        close = (
            None
            if manifest is None
            else build_close_bindings(manifest, root, thesis_store, version_store, source.situation.session_date)
        )
        strategy = resolve_strategy_binding(
            manifest.strategy_manifest if manifest is not None else _required_strategy_path(strategy_manifest),
            manifest.experiment_ledger if manifest is not None else _required_strategy_path(experiment_ledger),
            champion,
        )
    except ProductionBindingError as error:
        raise _CliError(error.reason) from None
    paper_store = PaperStore(private / "paper.sqlite3")
    paper = (
        None
        if manifest is None
        else _paper_bindings(
            manifest,
            source,
            root,
            thesis_store,
            paper_store,
            evaluated_at,
            strategy.strategy_version,
        )
    )
    return build_us_day_agent_service(
        UsDayProductionConfig(
            stores=UsDayLocalStores(
                root,
                DayAgentTaskStore(private / "day_agent.sqlite3"),
                thesis_store,
                paper_store,
                version_store,
            ),
            models=_model_bindings(
                day_model_responses,
                thesis_model_response,
                live_model_provider,
                champion,
                evaluated_at,
            ),
            strategy=strategy,
            source_reader=LocalUsDaySourceReader(),
            paper=paper,
            close=close,
        ),
        UsDayAgentServiceConfig(
            private / "tick_receipts",
            dt.timedelta(minutes=entry_cutoff_minutes),
            dt.timedelta(minutes=eod_minutes),
        ),
        lambda: evaluated_at,
    )


def _paper_bindings(
    manifest: ProductionManifest,
    source: CanonicalUsDaySource,
    outputs: Path,
    thesis_store: UsDayThesisStore,
    paper_store: PaperStore,
    evaluated_at: dt.datetime,
    strategy_version: str,
) -> UsDayPaperBindings:
    try:
        signer = HermesArmSigner(load_hermes_arm_signing_key(manifest.arm_signing_key))
        arm_store = HermesArmStore(manifest.arm_database, signer)
        resolver = LedgerHermesArmAuthorityResolver(
            LedgerHermesArmAuthorityConfig(
                repository=manifest.repository,
                lane_registry=manifest.lane_registry,
                experiment_ledger=manifest.experiment_ledger,
            )
        )
        gateway = HermesArmGateway(
            HermesArmGatewayConfig(
                store=arm_store,
                authority_resolver=resolver,
                signer=signer,
                clock=lambda: evaluated_at,
                nonce_factory=lambda: secrets.token_bytes(32),
                ttl_seconds=300,
            )
        )
        consumer = StrategyBoundHermesArmConsumer(gateway, arm_store)
        execution = ExecutionStore(manifest.execution_database)
        credentials = load_alpaca_paper_credentials()
        coordinator = UsDayOperatingCoordinator(
            UsDayOperatingCoordinatorConfig(
                arm_consumer=consumer,
                credentials=credentials,
                execution_store=execution,
                delivery_store=HermesDeliveryStore(manifest.delivery_database),
            )
        )
        return UsDayPaperBindings(
            operating=UsDayAgentOperatingServices(
                coordinator=coordinator,
                thesis_store=thesis_store,
                paper_store=paper_store,
                market_liquidity_policy=_OneShareLiquidity(),
            ),
            authority_reader=LedgerUsDayExecutionAuthorityReader(
                manifest.review_ledger,
                arm_store=arm_store,
            ),
            session_control=LiveUsDayPaperSessionControl(
                outputs=outputs,
                execution_store=execution,
                lane_registry=LaneRegistryStore(manifest.lane_registry),
                session_root=manifest.session_root,
                arm_consumer=consumer,
                safety_arm_request_id=manifest.safety_arm_request_id,
                strategy_version=strategy_version,
                session_id=source.situation.session_id,
            ),
        )
    except (OSError, RuntimeError, ValidationError, ValueError):
        raise _CliError("production_manifest_or_credentials_invalid") from None


def _required_strategy_path(path: Path | None) -> Path:
    if path is None:
        raise ProductionBindingError("strategy_bindings_required")
    return path


def _emit(payload: dict[str, str]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


@_APP.command()
def main(
    situation: Annotated[Path, typer.Option(exists=False, dir_okay=False, readable=True)],
    outputs: Annotated[Path, typer.Option(file_okay=False)] = Path(".private/us-day-agent"),
    version_store: Annotated[Path | None, typer.Option(exists=False, dir_okay=False)] = None,
    production_manifest: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    strategy_manifest: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    experiment_ledger: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    day_model_responses: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    thesis_model_response: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    live_model_provider: Annotated[str | None, typer.Option()] = None,
    now: Annotated[str | None, typer.Option()] = None,
    entry_cutoff_minutes: Annotated[int, typer.Option(min=6, max=59)] = 15,
    eod_minutes: Annotated[int, typer.Option(min=1, max=5)] = 5,
) -> None:
    try:
        evaluated_at = _now(now)
    except _CliError as error:
        _emit({"phase": "closed", "reason": str(error), "status": "blocked"})
        raise typer.Exit(2) from None
    try:
        canonical_source = _source(situation, evaluated_at)
        if (
            live_model_provider is None
            and (day_model_responses is None or thesis_model_response is None)
        ):
            raise _CliError("model_bindings_required")
        result = _service(
            outputs,
            version_store or outputs.expanduser().absolute() / "us_day" / "versions.sqlite3",
            canonical_source,
            production_manifest,
            strategy_manifest,
            experiment_ledger,
            day_model_responses,
            thesis_model_response,
            live_model_provider,
            evaluated_at,
            entry_cutoff_minutes,
            eod_minutes,
        ).tick_from_source(situation.expanduser().absolute())
        _emit(result.compact())
        if result.status == "blocked":
            raise typer.Exit(2)
    except (_CliError, UsDayAgentServiceError) as error:
        _emit({"phase": session_phase_at(evaluated_at).value, "reason": str(error), "status": "blocked"})
        raise typer.Exit(2) from None


if __name__ == "__main__":
    _APP()
