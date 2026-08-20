from __future__ import annotations

import json
from pathlib import Path

from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _champion, _market, _rationale
from trading_agent.store import PaperStore
from trading_agent.us_day_recommendation_card import persist_and_queue_thesis
from trading_agent.us_day_thesis_models import situation_id_for
from trading_agent.us_day_thesis_runtime import reason_trade_thesis
from trading_agent.us_day_thesis_store import UsDayThesisStore


def test_recommendation_is_saved_and_one_complete_korean_card_is_queued(tmp_path: Path) -> None:
    situation = _project(_inputs())
    theme = situation.themes[0]
    leader = theme.leaders[0]
    response = {
        "decision": "recommend",
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_name": "semiconductor_infrastructure",
        "symbol": leader.symbol,
        "entry_price": "100.10",
        "stop_price": "99.50",
        "targets": ({"label": "target_1", "price": "101.00"}, {"label": "target_2", "price": "102.00"}),
        "invalidation_rule": "현재 완료 봉 저가 이탈 시 논리가 무효화된다.",
        "confidence_bps": 7200,
        "observed_at": EVALUATED_AT,
        "valid_until": EVALUATED_AT.replace(second=25),
        "reason_code": None,
        "theme_rationale": _rationale("반도체 인프라 테마가 활성화됐다.", theme.claims[0].evidence_refs),
        "catalyst_rationale": _rationale("현재 세션 촉매가 확인됐다.", theme.catalysts[0].evidence_refs),
        "leader_rationale": _rationale("NVDA가 대장주다.", leader.evidence_refs),
        "flow_rationale": _rationale("관측 수급이 우세하다.", leader.flow.evidence_refs),
    }
    bar_ref = next(ref for ref in leader.evidence_refs if ref.namespace == "research/current_bar")
    result = reason_trade_thesis(response, _champion(), situation, (_market(leader.symbol, bar_ref),))
    paper = PaperStore(tmp_path / "paper.sqlite3")
    theses = UsDayThesisStore(tmp_path / "private")

    assert persist_and_queue_thesis(result.thesis, paper, theses) is True
    assert persist_and_queue_thesis(result.thesis, paper, theses) is False
    recommendation = paper.recommendations()[0]
    alert = paper.alerts()[0]
    payload = json.loads(alert.payload_json)
    assert recommendation.recommendation_id == result.thesis_id
    assert payload["order_authority"] is False
    assert "NVDA" in alert.card_markdown
    for text in ("테마", "촉매", "대장주", "수급", "진입", "손절", "목표", "무효화", "확신도", "Agent 버전"):
        assert text in alert.card_markdown
