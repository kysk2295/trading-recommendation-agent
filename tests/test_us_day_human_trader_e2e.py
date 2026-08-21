from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tests.day_agent_loop_e2e_support import loop_evaluation
from tests.day_agent_version_learning_support import LeaderAuthor, diagnostics
from tests.day_strategy_capsule_support import bar, proposal
from tests.test_day_agent_runtime import ScriptedDayReasoner, _thesis_call
from tests.test_day_historical_evidence import SHA_A, SHA_B
from tests.test_day_learning_report_models import _payload
from tests.test_us_day_signal_admission import RecordingLiquidityPolicy
from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets, _valid_response
from tests.us_day_operating_fixtures import NaturalPaperSession, OneUseArmConsumer
from tests.us_forward_shadow_support import signal_source
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.day_agent_challenger_publisher import (
    DayAgentFutureShadowSession,
    DayAgentGeneratedCapsulePublisher,
)
from trading_agent.day_agent_loop_engineer import DayAgentLoopServices
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_runtime import DayAgentToolRuntime
from trading_agent.day_agent_version_models import AgentVersion
from trading_agent.day_historical_evidence_models import DayHistoricalEvidenceSeal
from trading_agent.day_learning_report_models import MarketCloseReportPayload
from trading_agent.day_research_review import (
    DayExecutionSessionContext,
    build_execution_eligibility,
    seal_owner_authority_event,
    seal_promotion_decision,
)
from trading_agent.day_research_review_models import (
    DayOwnerAuthorityEventPayload,
    ExecutionEligibility,
    PromotionDecision,
)
from trading_agent.day_research_review_types import DayExecutionAuthorityClass, DayPromotionStatus
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.lane_identity_models import LaneId
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.store import PaperStore
from trading_agent.us_day_agent_operating import UsDayAgentOperatingServices
from trading_agent.us_day_agent_service import (
    CanonicalUsDaySource,
    LocalUsDaySourceReader,
    UsDayAgentServiceConfig,
    UsDayAgentTickRequest,
    UsDayCloseBindings,
    UsDayExecutionAuthority,
    UsDayLocalStores,
    UsDayModelBindings,
    UsDayPaperBindings,
    UsDayProductionConfig,
    UsDayStrategyBinding,
    build_us_day_agent_service,
)
from trading_agent.us_day_operating_coordinator import UsDayOperatingCoordinator, UsDayOperatingCoordinatorConfig
from trading_agent.us_day_signal_admission import UsDaySignalAdmissionRequest, admit_us_day_signal
from trading_agent.us_day_thesis_models import UsDayChampion, UsDayPlaybook
from trading_agent.us_day_thesis_runtime import reason_trade_thesis
from trading_agent.us_day_thesis_store import UsDayThesisStore


class _PaperControl:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def recover_and_reconcile(self, evaluated_at: dt.datetime) -> None:
        self.calls.append("recover")

    def block_new_entries(self, evaluated_at: dt.datetime) -> str:
        self.calls.append("cutoff")
        return "entries_blocked"

    def flatten(self, evaluated_at: dt.datetime) -> str:
        self.calls.append("eod")
        return "flat"

    def finalize(self, evaluated_at: dt.datetime) -> str:
        self.calls.append("finalize")
        return "finalized"


@dataclass(frozen=True, slots=True)
class _ThesisReasoner:
    version_id: str

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        return _valid_response() | {"agent_version_id": self.version_id}


@dataclass(frozen=True, slots=True)
class _AuthorityReader:
    authority: UsDayExecutionAuthority

    def read(
        self,
        source: CanonicalUsDaySource,
        thesis: object,
        champion: UsDayChampion,
        evaluated_at: dt.datetime,
    ) -> UsDayExecutionAuthority:
        return self.authority


@dataclass(frozen=True, slots=True)
class _CloseReader:
    payload: MarketCloseReportPayload

    def read(self, request: UsDayAgentTickRequest, champion: AgentVersion) -> MarketCloseReportPayload:
        return self.payload


def test_canonical_vertical_persists_real_task_thesis_paper_hermes_dashboard_and_replays(tmp_path: Path) -> None:
    # Given: canonical local sources, real host stores/runtimes, and fake model/market/broker boundaries.
    fixture = loop_evaluation(tmp_path / "loop")
    situation = _project(_inputs())
    source = CanonicalUsDaySource(situation=situation, current_markets=_markets())
    source_path = tmp_path / "source.json"
    assert publish_private_immutable_text(source_path, source.model_dump_json())
    playbook = UsDayPlaybook(playbook_id="leader_breakout", title="대장주 돌파", entry_type="stop_trigger")
    lane = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id="leader_breakout",
    )
    day_champion = UsDayChampion(
        version_id=fixture.baseline.version_id,
        strategy_version=playbook.playbook_id,
        strategy_lane=lane,
        deployed=True,
        playbooks=(playbook,),
    )
    promotion, eligibility = _authority(fixture.baseline.version_id)
    response = _valid_response() | {"agent_version_id": fixture.baseline.version_id}
    thesis_result = reason_trade_thesis(response, day_champion, situation, _markets())
    assert thesis_result.signal is not None
    market = next(item for item in _markets() if item.symbol == thesis_result.signal.symbol)
    admission_request = UsDaySignalAdmissionRequest(
        situation.session_id,
        LaneId.INTRADAY_MOMENTUM,
        thesis_result.thesis,
        thesis_result.signal,
        day_champion,
        situation,
        market,
        promotion,
        eligibility,
        EVALUATED_AT,
    )
    liquidity = RecordingLiquidityPolicy(37)
    admitted = admit_us_day_signal(admission_request, liquidity)
    broker_session = NaturalPaperSession(admitted)

    @contextmanager
    def opener(_: AlpacaPaperCredentials, __: ExecutionStore) -> Iterator[NaturalPaperSession]:
        yield broker_session

    outputs = tmp_path / "outputs"
    (outputs / "us_day").mkdir(parents=True)
    outputs.chmod(0o700)
    (outputs / "us_day").chmod(0o700)
    thesis_store = UsDayThesisStore(outputs / "us_day" / "theses")
    paper_store = PaperStore(tmp_path / "paper.sqlite3")
    delivery_store = HermesDeliveryStore(tmp_path / "hermes.sqlite3")
    operating = UsDayAgentOperatingServices(
        coordinator=UsDayOperatingCoordinator(
            UsDayOperatingCoordinatorConfig(
                arm_consumer=OneUseArmConsumer(),
                credentials=AlpacaPaperCredentials("test-key", "test-secret"),
                execution_store=ExecutionStore(tmp_path / "execution.sqlite3"),
                delivery_store=delivery_store,
                session_opener=opener,
                max_cycles=4,
            )
        ),
        thesis_store=thesis_store,
        paper_store=paper_store,
        market_liquidity_policy=liquidity,
    )
    control = _PaperControl()
    base_close_payload = _payload()
    close_payload = base_close_payload.model_copy(
        update={
            "agent_version_id": fixture.baseline.version_id,
            "diagnostics": diagnostics(),
            "next_session": base_close_payload.next_session.model_copy(
                update={"active_capsule_ids": (fixture.champion_capsule.capsule_id,), "queued_capsule_ids": ()}
            ),
        }
    )
    future_sessions = tuple(
        DayAgentFutureShadowSession(
            session_date=session_date,
            calendar_snapshot_id="calendar://official/XNYS/2026-v1",
            effective_at=dt.datetime.combine(session_date, dt.time(13, 30), tzinfo=dt.UTC),
        )
        for session_date in (dt.date(2026, 8, 21), dt.date(2026, 8, 24))
    )
    production = UsDayProductionConfig(
        stores=UsDayLocalStores(
            outputs,
            DayAgentTaskStore(outputs / "us_day" / "day_agent.sqlite3"),
            thesis_store,
            paper_store,
            fixture.store,
        ),
        models=UsDayModelBindings(
            ScriptedDayReasoner((_thesis_call(),)),
            _ThesisReasoner(fixture.baseline.version_id),
            DayAgentToolRuntime((), lambda: EVALUATED_AT),
        ),
        strategy=UsDayStrategyBinding(playbook.playbook_id, lane, (playbook,)),
        source_reader=LocalUsDaySourceReader(),
        paper=UsDayPaperBindings(
            operating,
            _AuthorityReader(
                UsDayExecutionAuthority(
                    promotion,
                    eligibility,
                    LaneId.INTRADAY_MOMENTUM,
                    "a" * 64,
                    hashlib.sha256(b"actionable").hexdigest(),
                )
            ),
            control,
        ),
        close=UsDayCloseBindings(
            _CloseReader(close_payload),
            DayAgentLoopServices(
                fixture.store,
                LeaderAuthor(),
                DayAgentGeneratedCapsulePublisher(
                    services=fixture.controller.services,
                    proposal_template=proposal(signal_source()),
                    replay_bars=(bar(),),
                    future_sessions=future_sessions,
                ),
            ),
        ),
    )
    service = build_us_day_agent_service(
        production,
        UsDayAgentServiceConfig(outputs / "us_day" / "tick_receipts"),
        lambda: EVALUATED_AT,
    )

    # When: the real production composition runs and the process is reconstructed for exact replay.
    first = service.tick_from_source(source_path)
    replay = build_us_day_agent_service(production, service.config, lambda: EVALUATED_AT).tick_from_source(source_path)
    next_tick = service.tick(
        UsDayAgentTickRequest(
            situation_path=source_path,
            evaluated_at=EVALUATED_AT + dt.timedelta(minutes=1),
            source_sha256="e" * 64,
        )
    )
    close = service.tick(
        UsDayAgentTickRequest(
            situation_path=source_path,
            evaluated_at=close_payload.finalized_at,
            source_sha256="f" * 64,
        )
    )

    # Then: every canonical artifact exists once with exact thesis-to-Paper/Hermes lineage.
    assert first == replay
    assert first.status == "accepted"
    assert first.paper_status == "completed"
    assert first.recommendation_id == thesis_result.thesis.thesis_id
    assert next_tick.recommendation_id == first.recommendation_id
    assert next_tick.paper_status is None
    assert control.calls == ["recover", "recover", "recover", "finalize"]
    steps = DayAgentTaskStore(outputs / "us_day" / "day_agent.sqlite3").reader().steps(fixture.baseline.task_id)
    assert len(steps) == 2
    assert thesis_store.theses() == (thesis_result.thesis,)
    assert paper_store.recommendations()[0].recommendation_id == thesis_result.thesis.thesis_id
    assert broker_session.entry_calls == 1
    assert len(delivery_store.events()) == 2
    assert first.hermes_delivery_id in {item.delivery_id for item in delivery_store.events()}
    assert tuple((outputs / "us_day" / "dashboard").glob("*.json"))
    assert close.market_close_report_id is not None
    assert close.challenger_version_id == fixture.challenger.version_id
    assert tuple((outputs / "us_day" / "close_reports").glob("*.json"))


def _authority(version_id: str) -> tuple[PromotionDecision, ExecutionEligibility]:
    from tests.day_research_review_support import promotion_payload

    base = promotion_payload(status=DayPromotionStatus.PAPER_CHAMPION_CANDIDATE)
    reveal = base.historical_evidence_seal.payload.holdout_reveal.model_copy(
        update={"hypothesis_version_id": version_id}
    )
    seal_payload = base.historical_evidence_seal.payload.model_copy(
        update={"hypothesis_version_id": version_id, "holdout_reveal": reveal}
    )
    seal = DayHistoricalEvidenceSeal(seal_id=seal_payload.content_sha256, payload=seal_payload)
    payload = base.model_copy(update={"hypothesis_version_id": version_id, "historical_evidence_seal": seal})
    promotion = seal_promotion_decision(payload)
    owner = seal_owner_authority_event(
        DayOwnerAuthorityEventPayload(
            decision_id=promotion.decision_id,
            capsule_id=payload.capsule_id,
            hypothesis_version_id=version_id,
            market_id=MarketId.US_EQUITIES,
            authority_class=DayExecutionAuthorityClass.PAPER_CHAMPION,
            owner_id="owner_1",
            approved_at=EVALUATED_AT - dt.timedelta(seconds=2),
            effective_after_session=payload.effective_after_session,
        )
    )
    eligibility = build_execution_eligibility(
        promotion,
        DayExecutionSessionContext(
            session_date=EVALUATED_AT.date(),
            sequence=1,
            previous_event_id=None,
            clean_commit_sha256=SHA_A,
            risk_policy_sha256=SHA_B,
            effective_at=EVALUATED_AT - dt.timedelta(seconds=1),
            expires_at=EVALUATED_AT + dt.timedelta(minutes=1),
        ),
        owner,
    )
    return promotion, eligibility
