from __future__ import annotations

import json
from pathlib import Path

from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _champion, _markets, _rationale
from trading_agent.models import Recommendation, RecommendationState
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
        "theme_id": theme.theme_id,
        "catalyst_event_id": theme.catalysts[0].event_id,
        "flow_inference_kind": None,
        "theme_name": "semiconductor_infrastructure",
        "symbol": leader.symbol,
        "entry_price": "200.05",
        "stop_price": "199.5",
        "targets": ({"label": "target_1", "price": "200.60"}, {"label": "target_2", "price": "201.15"}),
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
    result = reason_trade_thesis(response, _champion(), situation, _markets())
    paper = PaperStore(tmp_path / "paper.sqlite3")
    theses = UsDayThesisStore(tmp_path / "private")

    assert persist_and_queue_thesis(result.thesis, paper, theses) is True
    assert persist_and_queue_thesis(result.thesis, paper, theses) is False
    recommendation = paper.recommendations()[0]
    alert = paper.alerts()[0]
    payload = json.loads(alert.payload_json)
    assert recommendation.recommendation_id == result.thesis_id
    assert payload["order_authority"] is False
    assert payload["situation_id"] == result.situation_id
    assert payload["theme_id"] == result.theme_id
    assert payload["catalyst_event_id"] == result.catalyst_event_id
    assert payload["evidence_refs"]
    assert set(payload["rationales"]) == {"theme", "catalyst", "leader", "flow"}
    assert "NVDA" in alert.card_markdown
    for text in ("테마", "촉매", "대장주", "수급", "진입", "손절", "목표", "무효화", "확신도", "Agent 버전"):
        assert text in alert.card_markdown


def test_restart_repairs_thesis_before_save_and_save_before_alert(tmp_path: Path) -> None:
    situation = _project(_inputs())
    result = reason_trade_thesis(_recommend_response(), _champion(), situation, _markets())
    paper = PaperStore(tmp_path / "paper.sqlite3")
    theses = UsDayThesisStore(tmp_path / "private")
    assert theses.publish_thesis(result.thesis)
    assert persist_and_queue_thesis(result.thesis, paper, theses) is True
    assert persist_and_queue_thesis(result.thesis, paper, theses) is False

    second_paper = PaperStore(tmp_path / "paper-second.sqlite3")
    second_theses = UsDayThesisStore(tmp_path / "private-second")
    assert second_theses.publish_thesis(result.thesis)
    second_paper.save(_recommendation(result.thesis))
    assert persist_and_queue_thesis(result.thesis, second_paper, second_theses) is True
    assert len(second_paper.alerts()) == 1


def test_watch_has_no_terminal_card_but_no_trade_does(tmp_path: Path) -> None:
    situation = _project(_inputs())
    base = _recommend_response() | {
        "symbol": None,
        "entry_price": None,
        "stop_price": None,
        "targets": (),
        "reason_code": "setup_not_confirmed",
        "theme_rationale": None,
        "catalyst_rationale": None,
        "leader_rationale": None,
        "flow_rationale": None,
    }
    paper = PaperStore(tmp_path / "paper.sqlite3")
    store = UsDayThesisStore(tmp_path / "private")
    watch = reason_trade_thesis(base | {"decision": "watch"}, _champion(), situation, _markets()).thesis
    assert persist_and_queue_thesis(watch, paper, store)
    assert not (store.root / "terminal_cards" / f"{watch.thesis_id}.md").exists()
    no_trade = reason_trade_thesis(base | {"decision": "no_trade"}, _champion(), situation, _markets()).thesis
    assert persist_and_queue_thesis(no_trade, paper, store)
    assert (store.root / "terminal_cards" / f"{no_trade.thesis_id}.md").is_file()
    assert paper.recommendations() == ()


def _recommend_response() -> dict[str, object]:
    situation = _project(_inputs())
    theme = situation.themes[0]
    leader = theme.leaders[0]
    return {
        "decision": "recommend",
        "situation_id": situation_id_for(situation),
        "agent_version_id": "a" * 64,
        "playbook_id": "leader_breakout",
        "theme_id": theme.theme_id,
        "catalyst_event_id": theme.catalysts[0].event_id,
        "flow_inference_kind": None,
        "theme_name": "semiconductor_infrastructure",
        "symbol": leader.symbol,
        "entry_price": "200.05",
        "stop_price": "199.5",
        "targets": ({"label": "target_1", "price": "200.60"}, {"label": "target_2", "price": "201.15"}),
        "invalidation_rule": "현재 완료 봉 저가 이탈 시 논리가 무효화된다.",
        "confidence_bps": 7200,
        "observed_at": EVALUATED_AT,
        "valid_until": EVALUATED_AT.replace(second=25),
        "reason_code": None,
        "theme_rationale": _rationale("테마 근거다.", theme.claims[0].evidence_refs),
        "catalyst_rationale": _rationale("촉매 근거다.", theme.catalysts[0].evidence_refs),
        "leader_rationale": _rationale("대장주 근거다.", leader.evidence_refs),
        "flow_rationale": _rationale("관측 수급이다.", leader.flow.evidence_refs),
    }


def _recommendation(thesis: object) -> Recommendation:
    return Recommendation(
        thesis.thesis_id,
        thesis.symbol,
        thesis.playbook_id,
        thesis.observed_at,
        float(thesis.entry_price),
        float(thesis.stop_price),
        float(thesis.targets[0].price),
        float(thesis.targets[1].price),
        RecommendationState.SETUP,
        thesis.rationale,
    )
