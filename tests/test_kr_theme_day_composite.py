from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_theme_day_composite import (
    InvalidKrThemeDayCompositeError,
    KrThemeDayCompositeAuthorityRequest,
    KrThemeDayCompositeRegistrationRequest,
    kr_theme_day_composite_hypothesis_id,
    register_kr_theme_day_composite,
    require_exact_kr_theme_day_composite,
)
from trading_agent.kr_theme_research_registration import (
    kr_theme_day_strategy_version,
    kr_theme_strategy_version,
    register_kr_theme_research_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "kr_theme_projection"
KST = ZoneInfo("Asia/Seoul")
REGISTERED_AT = dt.datetime(2026, 7, 19, 8, 30, 30, tzinfo=KST)
DAY_CODE = "kr-theme-day-fixture-code-v1"
OPPORTUNITY_CODE = "kr-theme-fixture-code-v1"
DAY_VERSION = kr_theme_day_strategy_version(DAY_CODE)
OPPORTUNITY_VERSION = kr_theme_strategy_version(OPPORTUNITY_CODE)


def test_composite_registration_is_exact_append_only_replay(tmp_path: Path) -> None:
    # Given
    ledger = _component_ledger(tmp_path)
    request = _request()

    # When
    first = register_kr_theme_day_composite(ledger, request, clock=lambda: REGISTERED_AT)
    second = register_kr_theme_day_composite(
        ledger,
        request,
        clock=lambda: REGISTERED_AT + dt.timedelta(hours=1),
    )
    authority = require_exact_kr_theme_day_composite(
        ledger,
        KrThemeDayCompositeAuthorityRequest(
            day_strategy_version=DAY_VERSION,
            opportunity_strategy_version=OPPORTUNITY_VERSION,
            as_of=REGISTERED_AT,
        ),
    )

    # Then
    assert first.created is True
    assert second.created is False
    assert authority == first.authority
    assert authority.hypothesis_id == kr_theme_day_composite_hypothesis_id(
        DAY_VERSION,
        OPPORTUNITY_VERSION,
    )
    assert authority.registration_key == ledger.multi_market_hypotheses()[-1].registration_key


def test_composite_rejects_unregistered_or_mismatched_component(tmp_path: Path) -> None:
    # Given
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    # When / Then
    with pytest.raises(InvalidKrThemeDayCompositeError):
        _ = register_kr_theme_day_composite(ledger, _request(), clock=lambda: REGISTERED_AT)

    registered = _component_ledger(tmp_path / "registered")
    _ = register_kr_theme_day_composite(registered, _request(), clock=lambda: REGISTERED_AT)
    with pytest.raises(InvalidKrThemeDayCompositeError):
        _ = require_exact_kr_theme_day_composite(
            registered,
            KrThemeDayCompositeAuthorityRequest(
                day_strategy_version=DAY_VERSION,
                opportunity_strategy_version="kr-theme-keyword-projection-v1-code-deadbeefdeadbeef",
                as_of=REGISTERED_AT,
            ),
        )


def test_composite_rejects_first_append_at_or_after_open(tmp_path: Path) -> None:
    # Given
    ledger = _component_ledger(tmp_path)
    registered_at = REGISTERED_AT.replace(hour=8, minute=59)

    # When / Then
    with pytest.raises(InvalidKrThemeDayCompositeError):
        _ = register_kr_theme_day_composite(
            ledger,
            _request().model_copy(update={"registered_at": registered_at}),
            clock=lambda: registered_at.replace(hour=9, minute=0),
        )
    assert len(ledger.multi_market_hypotheses()) == 2


def _request() -> KrThemeDayCompositeRegistrationRequest:
    return KrThemeDayCompositeRegistrationRequest(
        day_strategy_version=DAY_VERSION,
        opportunity_strategy_version=OPPORTUNITY_VERSION,
        registered_at=REGISTERED_AT,
    )


def _component_ledger(tmp_path: Path) -> ExperimentLedgerStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_manifest(
        EXAMPLES / "research-registration.json",
        tmp_path / "opportunity.json",
        ledger,
        OPPORTUNITY_VERSION,
        OPPORTUNITY_CODE,
    )
    _register_manifest(
        EXAMPLES / "day-research-registration.json",
        tmp_path / "day.json",
        ledger,
        DAY_VERSION,
        DAY_CODE,
    )
    return ledger


def _register_manifest(
    source: Path,
    target: Path,
    ledger: ExperimentLedgerStore,
    strategy_version: str,
    code_version: str,
) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["strategy_version"] = strategy_version
    payload["code_version"] = code_version
    target.write_text(json.dumps(payload), encoding="utf-8")
    _ = register_kr_theme_research_manifest(target, ledger)
