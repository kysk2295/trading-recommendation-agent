from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.contract_outbox import (
    ContractOutboxConflictError,
    ContractOutboxFormatError,
    append_opportunity_snapshot,
    append_quote_actionability_assessment,
    append_trade_signal_publication,
    append_us_quote_snapshot,
)
from trading_agent.kis_us_quote import KisUsLevelOneQuote
from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    SourceCoverage,
    TradeSignalEnvelope,
    TradeTarget,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability import (
    QuoteAssessmentStatus,
    UsQuoteActionabilityDecision,
    assess_us_quote,
    provider_failed_assessment,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 14, 0, tzinfo=dt.UTC)


def test_opportunity_outbox_appends_once_and_parses_structurally(tmp_path: Path) -> None:
    path = tmp_path / "opportunities.v1.jsonl"
    snapshot = _opportunity()

    assert append_opportunity_snapshot(path, snapshot) is True
    assert append_opportunity_snapshot(path, snapshot) is False

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["opportunity_id"] == snapshot.opportunity_id
    assert payload["candidates"][0]["symbol"] == "ACME"
    assert path.read_bytes().endswith(b"\n")


def test_outbox_rejects_the_same_identity_with_different_content(tmp_path: Path) -> None:
    path = tmp_path / "opportunities.v1.jsonl"
    snapshot = _opportunity()
    changed = OpportunitySnapshot.model_validate(
        {
            **snapshot.model_dump(mode="json"),
            "producer_strategy_version": "kis-risk-screen-v2",
        }
    )
    assert append_opportunity_snapshot(path, snapshot) is True

    with pytest.raises(ContractOutboxConflictError):
        append_opportunity_snapshot(path, changed)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize(
    "content",
    (
        "{not-json}\n",
        "[]\n",
        '{"schema_version": 1}\n',
    ),
)
def test_outbox_fails_closed_on_malformed_existing_records(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "opportunities.v1.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ContractOutboxFormatError):
        append_opportunity_snapshot(path, _opportunity())

    assert path.read_text(encoding="utf-8") == content


def test_signal_outbox_writes_one_json_record_and_a_safe_korean_card(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trade-signals.v1.jsonl"
    cards = tmp_path / "trade-signal-cards-ko"
    publication = _publication(signal_id="folder/unsafe")

    assert append_trade_signal_publication(path, cards, publication) is True
    assert append_trade_signal_publication(path, cards, publication) is False

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["signal"]["signal_id"] == "folder/unsafe"
    assert payload["signal"]["actionability"] == "conditional"
    card_paths = tuple(cards.glob("*.ko.md"))
    assert len(card_paths) == 1
    assert card_paths[0].parent == cards
    assert "/" not in card_paths[0].name
    card = card_paths[0].read_text(encoding="utf-8")
    assert "미국 주식 조건부 트레이딩 신호" in card
    assert "시장: us_equities" in card
    assert "전략: us_equities/day_trading/orb" in card
    assert f"신호 관측 시각: {OBSERVED_AT.isoformat()}" in card
    assert f"발행 시각: {publication.published_at.isoformat()}" in card
    assert f"유효 종료: {publication.signal.valid_until.isoformat()}" in card
    assert "종목: ACME" in card
    assert "조건부 진입: stop_trigger 10.5" in card
    assert "손절: 10" in card
    assert "목표: 1r 11 / 2r 11.5" in card
    assert "무효화: 진입 전 10 이하이면 무효" in card
    assert "근거: ORB와 거래량 확대" in card
    assert "현재 bid/ask" not in card
    assert card == _expected_conditional_card(publication)


def test_quote_snapshot_outbox_replays_and_rejects_conflict(tmp_path: Path) -> None:
    path = tmp_path / "us-quote-snapshots.v2.jsonl"
    decision = _quote_decision()
    assert decision.snapshot is not None
    snapshot = decision.snapshot

    assert append_us_quote_snapshot(path, snapshot) is True
    assert append_us_quote_snapshot(path, snapshot) is False
    with pytest.raises(ContractOutboxConflictError):
        append_us_quote_snapshot(
            path,
            snapshot.model_copy(update={"ask": Decimal("10.11")}),
        )

    payloads = tuple(
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    )
    assert len(payloads) == 1
    assert payloads[0]["quote_id"] == snapshot.quote_id
    assert payloads[0]["provider"] == "kis"
    assert payloads[0]["schema_version"] == 2


def test_quote_assessment_outbox_replays_and_rejects_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quote-actionability-assessments.v2.jsonl"
    assessment = _quote_decision().assessment

    assert append_quote_actionability_assessment(path, assessment) is True
    assert append_quote_actionability_assessment(path, assessment) is False
    with pytest.raises(ContractOutboxConflictError):
        append_quote_actionability_assessment(
            path,
            assessment.model_copy(
                update={"status": QuoteAssessmentStatus.PROVIDER_FAILED}
            ),
        )

    payloads = tuple(
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    )
    assert len(payloads) == 1
    assert payloads[0]["assessment_id"] == assessment.assessment_id
    assert payloads[0]["schema_version"] == 2


def test_independent_quote_receipts_append_without_identity_conflict(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "us-quote-snapshots.v2.jsonl"
    signals = tmp_path / "trade-signals.v1.jsonl"
    cards = tmp_path / "trade-signal-cards-ko"
    first = _quote_decision(
        received_at=OBSERVED_AT + dt.timedelta(seconds=5, milliseconds=500)
    )
    second = _quote_decision(
        received_at=OBSERVED_AT + dt.timedelta(seconds=5, milliseconds=700)
    )
    assert first.snapshot is not None
    assert second.snapshot is not None
    assert first.derived_publication is not None
    assert second.derived_publication is not None

    assert append_us_quote_snapshot(snapshots, first.snapshot) is True
    assert append_us_quote_snapshot(snapshots, second.snapshot) is True
    assert (
        append_trade_signal_publication(signals, cards, first.derived_publication)
        is True
    )
    assert (
        append_trade_signal_publication(signals, cards, second.derived_publication)
        is True
    )

    assert len(snapshots.read_text(encoding="utf-8").splitlines()) == 2
    assert len(signals.read_text(encoding="utf-8").splitlines()) == 2


def test_same_base_and_scan_cycle_rejects_second_terminal_assessment(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quote-actionability-assessments.v2.jsonl"
    base = _publication(signal_id="base-signal-assessment")
    first = provider_failed_assessment(
        base,
        scan_started_at=OBSERVED_AT,
        evaluated_at=OBSERVED_AT + dt.timedelta(seconds=6),
    )
    second = provider_failed_assessment(
        base,
        scan_started_at=OBSERVED_AT,
        evaluated_at=OBSERVED_AT + dt.timedelta(seconds=7),
    )

    assert first.assessment_id == second.assessment_id
    assert append_quote_actionability_assessment(path, first) is True
    with pytest.raises(ContractOutboxConflictError):
        append_quote_actionability_assessment(path, second)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_quote_validated_card_contains_current_quote_and_trigger_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trade-signals.v1.jsonl"
    cards = tmp_path / "trade-signal-cards-ko"
    decision = _quote_decision()
    assert decision.derived_publication is not None

    assert (
        append_trade_signal_publication(
            path,
            cards,
            decision.derived_publication,
        )
        is True
    )

    card = next(cards.iterdir()).read_text(encoding="utf-8")
    assert "미국 주식 현재 호가 검증 트레이딩 신호" in card
    assert "현재 bid/ask: 10.08 / 10.1" in card
    assert "spread:" in card
    assert "트리거 상태: 도달" in card
    assert "자동주문" in card
    assert card.index("현재 bid/ask") < card.index("조건부 진입")


def _opportunity() -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id="us-opportunity-20260715T140000000000Z-abcd1234",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="kis-risk-screen-v1",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol="ACME",
                rank=1,
                score=Decimal("0.12"),
                features=(FeatureValue(name="change_pct", value="0.12"),),
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="kis/ranking",
                record_id="updown:NAS:1:ACME",
                observed_at=OBSERVED_AT,
            ),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="kis_updown_nas",
                observed_at=OBSERVED_AT,
                record_count=1,
                complete=True,
            ),
        ),
    )


def _publication(
    *,
    signal_id: str,
    entry_price: Decimal = Decimal("10.5"),
) -> TradeSignalPublication:
    signal = TradeSignalEnvelope(
        signal_id=signal_id,
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        producer_strategy_version="orb-v1",
        symbol="ACME",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=1),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=entry_price,
        stop_price=Decimal("10"),
        targets=(
            TradeTarget(label="1r", price=Decimal("11")),
            TradeTarget(label="2r", price=Decimal("11.5")),
        ),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="진입 전 10 이하이면 무효",
        rationale="ORB와 거래량 확대",
        evidence_refs=(
            EvidenceRef(
                namespace="recommendation",
                record_id="rec-1",
                observed_at=OBSERVED_AT,
            ),
        ),
        opportunity_id=_opportunity().opportunity_id,
    )
    return TradeSignalPublication(
        published_at=OBSERVED_AT + dt.timedelta(seconds=5),
        signal=signal,
    )


def _quote_decision(
    *,
    received_at: dt.datetime = OBSERVED_AT
    + dt.timedelta(seconds=5, milliseconds=500),
) -> UsQuoteActionabilityDecision:
    return assess_us_quote(
        _publication(
            signal_id="base-signal-quote",
            entry_price=Decimal("10.10"),
        ),
        KisUsLevelOneQuote(
            exchange="NAS",
            symbol="ACME",
            provider_observed_at=OBSERVED_AT + dt.timedelta(seconds=5),
            received_at=received_at,
            bid=Decimal("10.08"),
            ask=Decimal("10.10"),
            bid_size=1_200,
            ask_size=900,
        ),
        scan_started_at=OBSERVED_AT - dt.timedelta(seconds=1),
        evaluated_at=OBSERVED_AT + dt.timedelta(seconds=6),
    )


def _expected_conditional_card(publication: TradeSignalPublication) -> str:
    return "\n".join(
        (
            "# 미국 주식 조건부 트레이딩 신호",
            "",
            "> 연구 및 Paper forward-validation 후보이며 확정수익이나 자동주문이 아닙니다.",
            "",
            "- 시장: us_equities",
            "- 전략: us_equities/day_trading/orb",
            "- 전략 버전: orb-v1",
            f"- 신호 관측 시각: {OBSERVED_AT.isoformat()}",
            f"- 발행 시각: {publication.published_at.isoformat()}",
            f"- 유효 종료: {publication.signal.valid_until.isoformat()}",
            "- 종목: ACME",
            "- 실행 가능성: 조건부 (현재 호가 미검증)",
            "- 조건부 진입: stop_trigger 10.5",
            "- 손절: 10",
            "- 목표: 1r 11 / 2r 11.5",
            "- 무효화: 진입 전 10 이하이면 무효",
            "- 근거: ORB와 거래량 확대",
            f"- 기회 ID: {_opportunity().opportunity_id}",
            "",
        )
    )
