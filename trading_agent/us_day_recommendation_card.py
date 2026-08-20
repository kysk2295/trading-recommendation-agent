from __future__ import annotations

import json

from trading_agent.models import Recommendation, RecommendationAlert, RecommendationState
from trading_agent.store import PaperStore
from trading_agent.us_day_thesis_models import DayTradeDecision, ThesisChangeKind, UsDayTradeThesis
from trading_agent.us_day_thesis_store import InvalidUsDayThesisStoreError, UsDayThesisStore

_CHANGE_KO = {
    ThesisChangeKind.HOLD: "유지",
    ThesisChangeKind.CANCEL_ENTRY: "진입 취소",
    ThesisChangeKind.INVALIDATE_LOGIC: "논리 무효화",
    ThesisChangeKind.PARTIAL_EXIT: "부분 청산",
    ThesisChangeKind.CLOSE: "종료",
}


def render_change_kind_korean(kind: ThesisChangeKind) -> str:
    return _CHANGE_KO[kind]


def render_thesis_card_korean(thesis: UsDayTradeThesis) -> str:
    refs = ", ".join(
        f"{item.namespace}/{item.record_id}@{item.observed_at.isoformat()}" for item in thesis.evidence_refs
    )
    if thesis.decision is not DayTradeDecision.RECOMMEND:
        return (
            f"## 미국 데이 트레이딩 결정: {thesis.decision.value}\n\n"
            f"- Thesis ID: {thesis.thesis_id}\n"
            f"- Situation ID: {thesis.situation_id}\n"
            f"- 판단 시각: {thesis.observed_at.isoformat()}\n"
            f"- 사유 코드: {thesis.reason_code}\n"
            f"- 판단: {thesis.invalidation_rule}\n"
            f"- 확신도: {thesis.confidence_bps / 100:.2f}%\n"
            f"- Agent 버전: {thesis.agent_version_id}\n"
            "- 주문 권한: 없음\n"
        )
    assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
    assert thesis.theme_rationale is not None
    assert thesis.catalyst_rationale is not None
    assert thesis.leader_rationale is not None
    assert thesis.flow_rationale is not None
    targets = ", ".join(f"{item.label} {item.price}" for item in thesis.targets)
    return (
        f"## {thesis.symbol} · 미국 데이 트레이딩 Trade Thesis\n\n"
        f"- Thesis ID: {thesis.thesis_id}\n"
        f"- Situation ID: {thesis.situation_id}\n"
        f"- 판단 시각: {thesis.observed_at.isoformat()}\n"
        f"- 유효 시각: {thesis.valid_until.isoformat()}\n"
        f"- Theme ID: {thesis.theme_id}\n"
        f"- Catalyst Event ID: {thesis.catalyst_event_id}\n"
        f"- 테마(observed): {thesis.theme_name} — {thesis.theme_rationale.text}\n"
        f"- 촉매(observed): {thesis.catalyst_rationale.text}\n"
        f"- 대장주 근거(observed): {thesis.leader_rationale.text}\n"
        f"- 수급 근거({thesis.flow_rationale.observation_kind.value}): {thesis.flow_rationale.text}\n"
        f"- 수급 추론 종류: {None if thesis.flow_inference_kind is None else thesis.flow_inference_kind.value}\n"
        f"- 증거: {refs}\n"
        f"- 진입: {thesis.entry_price}\n"
        f"- 손절: {thesis.stop_price}\n"
        f"- 목표: {targets}\n"
        f"- 무효화: {thesis.invalidation_rule}\n"
        f"- 확신도: {thesis.confidence_bps / 100:.2f}%\n"
        f"- Agent 버전: {thesis.agent_version_id}\n"
        f"- Playbook: {thesis.playbook_id}\n"
        "- 주문 권한: 없음\n"
        "- 수익 보장: 없음\n"
    )


def persist_and_queue_thesis(
    thesis: UsDayTradeThesis,
    paper_store: PaperStore,
    thesis_store: UsDayThesisStore,
) -> bool:
    created = thesis_store.publish_thesis(thesis)
    card = render_thesis_card_korean(thesis)
    if thesis.decision is DayTradeDecision.WATCH:
        return created
    if thesis.decision in {DayTradeDecision.NO_TRADE, DayTradeDecision.INSUFFICIENT_EVIDENCE}:
        return thesis_store.publish_terminal_card(thesis, card) or created
    assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
    recommendation = Recommendation(
        recommendation_id=thesis.thesis_id,
        symbol=thesis.symbol,
        strategy=thesis.playbook_id,
        created_at=thesis.observed_at,
        entry=float(thesis.entry_price),
        stop=float(thesis.stop_price),
        target_1r=float(thesis.targets[0].price),
        target_2r=float(thesis.targets[1].price),
        state=RecommendationState.SETUP,
        rationale=thesis.rationale,
    )
    existing = tuple(item for item in paper_store.recommendations() if item.recommendation_id == thesis.thesis_id)
    repaired = False
    if not existing:
        paper_store.save(recommendation)
        repaired = True
    elif existing != (recommendation,):
        raise InvalidUsDayThesisStoreError
    events = paper_store.events(thesis.thesis_id)
    if not events:
        paper_store.set_state(
            thesis.thesis_id,
            RecommendationState.SETUP,
            thesis.observed_at,
            None,
            "추천 생성",
        )
        repaired = True
        events = paper_store.events(thesis.thesis_id)
    if len(events) != 1 or (
        events[0].occurred_at != thesis.observed_at
        or events[0].state is not RecommendationState.SETUP
        or events[0].price is not None
        or events[0].note != "추천 생성"
    ):
        raise InvalidUsDayThesisStoreError
    rationale_payload = {
        name: {
            "text": rationale.text,
            "observation_kind": rationale.observation_kind.value,
            "evidence_refs": [item.model_dump(mode="json") for item in rationale.evidence_refs],
        }
        for name, rationale in (
            ("theme", thesis.theme_rationale),
            ("catalyst", thesis.catalyst_rationale),
            ("leader", thesis.leader_rationale),
            ("flow", thesis.flow_rationale),
        )
        if rationale is not None
    }
    payload = json.dumps(
        {
            "thesis_id": thesis.thesis_id,
            "recommendation_id": thesis.thesis_id,
            "situation_id": thesis.situation_id,
            "decision": thesis.decision.value,
            "symbol": thesis.symbol,
            "theme": thesis.theme_name,
            "theme_id": thesis.theme_id,
            "catalyst_event_id": thesis.catalyst_event_id,
            "flow_inference_kind": None if thesis.flow_inference_kind is None else thesis.flow_inference_kind.value,
            "entry": str(thesis.entry_price),
            "stop": str(thesis.stop_price),
            "targets": [str(item.price) for item in thesis.targets],
            "invalidation": thesis.invalidation_rule,
            "confidence_bps": thesis.confidence_bps,
            "agent_version_id": thesis.agent_version_id,
            "playbook_id": thesis.playbook_id,
            "observed_at": thesis.observed_at.isoformat(),
            "valid_until": thesis.valid_until.isoformat(),
            "created_at": thesis.observed_at.isoformat(),
            "evidence_refs": [item.model_dump(mode="json") for item in thesis.evidence_refs],
            "rationales": rationale_payload,
            "order_authority": False,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    alert = RecommendationAlert(
        recommendation_id=thesis.thesis_id,
        queued_at=thesis.observed_at,
        payload_json=payload,
        card_markdown=card,
    )
    matching_alerts = tuple(item for item in paper_store.alerts() if item.recommendation_id == thesis.thesis_id)
    if not matching_alerts:
        if not paper_store.queue_alert(alert):
            raise InvalidUsDayThesisStoreError
        repaired = True
    elif matching_alerts != (alert,):
        raise InvalidUsDayThesisStoreError
    return created or repaired


__all__ = ("persist_and_queue_thesis", "render_change_kind_korean", "render_thesis_card_korean")
