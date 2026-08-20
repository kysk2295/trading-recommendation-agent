from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tests.test_us_day_signal_admission import RecordingLiquidityPolicy, _eligible_request
from tests.us_day_operating_fixtures import NaturalPaperSession, OneUseArmConsumer
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.lane_identity_models import LaneId
from trading_agent.models import RecommendationState
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.store import PaperStore
from trading_agent.us_day_agent_operating import (
    UsDayAgentOperatingRequest,
    UsDayAgentOperatingServices,
    operate_us_day_agent,
)
from trading_agent.us_day_operating_coordinator import UsDayOperatingCoordinator, UsDayOperatingCoordinatorConfig
from trading_agent.us_day_operating_models import UsDayOperatingStatus, UsDayOperatingTransition
from trading_agent.us_day_thesis_store import UsDayThesisStore


def test_champion_thesis_runs_complete_paper_lifecycle_and_replays_exactly(tmp_path: Path) -> None:
    # Given
    admission_request = _eligible_request()
    liquidity_policy = RecordingLiquidityPolicy(37)
    admission = __import__(
        "trading_agent.us_day_signal_admission", fromlist=["admit_us_day_signal"]
    ).admit_us_day_signal(admission_request, liquidity_policy)
    session = NaturalPaperSession(admission)
    execution_store = ExecutionStore(tmp_path / "execution.sqlite3")
    delivery_store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    @contextmanager
    def opener(_: AlpacaPaperCredentials, __: ExecutionStore) -> Iterator[PaperOperatingSession]:
        yield session

    coordinator = UsDayOperatingCoordinator(
        UsDayOperatingCoordinatorConfig(
            arm_consumer=OneUseArmConsumer(),
            credentials=AlpacaPaperCredentials("test-key", "test-secret"),
            execution_store=execution_store,
            delivery_store=delivery_store,
            session_opener=opener,
            max_cycles=4,
        )
    )
    services = UsDayAgentOperatingServices(
        coordinator=coordinator,
        thesis_store=UsDayThesisStore(tmp_path / "theses"),
        paper_store=PaperStore(tmp_path / "paper.sqlite3"),
        market_liquidity_policy=liquidity_policy,
    )
    request = UsDayAgentOperatingRequest(
        admission=admission_request,
        arm_request_id="a" * 64,
        actionable_payload_sha256=hashlib.sha256(b"safe-actionable").hexdigest(),
    )

    # When
    result = operate_us_day_agent(request, services)
    replay = operate_us_day_agent(request, services)

    # Then
    assert result.status is UsDayOperatingStatus.COMPLETED
    assert str(result.parent_intent_id) == request.thesis.thesis_id
    assert result.transitions == (
        UsDayOperatingTransition.ACTIONABLE,
        UsDayOperatingTransition.ENTRY_ACKNOWLEDGED,
        UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED,
        UsDayOperatingTransition.FLAT,
        UsDayOperatingTransition.RECONCILED,
        UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
    )
    assert replay == result
    assert session.entry_calls == 1
    changes = services.thesis_store.changes(request.thesis.thesis_id)
    assert len(changes) == 4
    recommendation = services.paper_store.recommendations()[0]
    assert recommendation.recommendation_id == request.thesis.thesis_id
    assert recommendation.state is RecommendationState.TIME_EXIT
    assert len(services.paper_store.events(request.thesis.thesis_id)) == 5
    assert request.admission.lane_id is LaneId.INTRADAY_MOMENTUM
