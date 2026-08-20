from __future__ import annotations

import json

from trading_agent.models import Recommendation, RecommendationAlert, RecommendationState
from trading_agent.store import PaperStore
from trading_agent.us_day_thesis_models import DayTradeDecision, ThesisChangeKind, UsDayTradeThesis
from trading_agent.us_day_thesis_store import UsDayThesisStore

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
    if thesis.decision is not DayTradeDecision.RECOMMEND:
        return (
            f"## 미국 데이 트레이딩 결정: {thesis.decision.value}\n\n"
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
        f"- 테마: {thesis.theme_name} — {thesis.theme_rationale.text}\n"
        f"- 촉매: {thesis.catalyst_rationale.text}\n"
        f"- 대장주 근거: {thesis.leader_rationale.text}\n"
        f"- 수급 근거: {thesis.flow_rationale.text} ({thesis.flow_rationale.observation_kind.value})\n"
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
    if not created:
        return False
    card = render_thesis_card_korean(thesis)
    if thesis.decision is not DayTradeDecision.RECOMMEND:
        _ = thesis_store.publish_terminal_card(thesis, card)
        return True
    assert thesis.symbol is not None and thesis.entry_price is not None and thesis.stop_price is not None
    paper_store.save(
        Recommendation(
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
    )
    payload = json.dumps(
        {
            "thesis_id": thesis.thesis_id,
            "recommendation_id": thesis.thesis_id,
            "decision": thesis.decision.value,
            "symbol": thesis.symbol,
            "theme": thesis.theme_name,
            "entry": str(thesis.entry_price),
            "stop": str(thesis.stop_price),
            "targets": [str(item.price) for item in thesis.targets],
            "invalidation": thesis.invalidation_rule,
            "confidence_bps": thesis.confidence_bps,
            "agent_version_id": thesis.agent_version_id,
            "playbook_id": thesis.playbook_id,
            "observed_at": thesis.observed_at.isoformat(),
            "valid_until": thesis.valid_until.isoformat(),
            "order_authority": False,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return paper_store.queue_alert(
        RecommendationAlert(
            recommendation_id=thesis.thesis_id,
            queued_at=thesis.observed_at,
            payload_json=payload,
            card_markdown=card,
        )
    )


__all__ = ("persist_and_queue_thesis", "render_change_kind_korean", "render_thesis_card_korean")
