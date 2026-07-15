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
    append_trade_signal_publication,
)
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


def _publication(*, signal_id: str) -> TradeSignalPublication:
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
        entry_price=Decimal("10.5"),
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
