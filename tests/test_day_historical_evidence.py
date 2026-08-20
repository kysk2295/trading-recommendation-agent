from __future__ import annotations

import datetime as dt
import hashlib
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.day_historical_evidence import (
    DayDiscoveryEvidenceFeedback,
    DayEvidenceWindow,
    DayHistoricalEvidencePayload,
    DayHistoricalEvidenceSeal,
    DayHistoricalPreregistration,
    DayHoldoutRevealReceipt,
    DayMarketCostEvaluator,
    DayPointInTimeDataManifest,
    DaySelectionDiagnostics,
    InvalidDayHistoricalEvidenceError,
    ValidatedMarketTimeSeriesEValueEvaluator,
)
from trading_agent.day_historical_evidence_store import (
    load_day_historical_evidence,
    publish_day_historical_evidence,
)
from trading_agent.intraday_overfit_diagnostics_models import IntradayOverfitDiagnosticsStatus
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import EvidenceKind, EvidenceUse, TerminalOutcome

NOW = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _preregistration() -> DayHistoricalPreregistration:
    return DayHistoricalPreregistration(
        preregistration_sha256=SHA_A,
        holdout_seal_id="sealed-holdout-1",
        holdout_commitment_sha256=SHA_B,
        preregistered_at=NOW - dt.timedelta(days=200),
        train=DayEvidenceWindow(start=NOW - dt.timedelta(days=190), end=NOW - dt.timedelta(days=100)),
        validation=DayEvidenceWindow(start=NOW - dt.timedelta(days=95), end=NOW - dt.timedelta(days=20)),
        sealed_holdout=DayEvidenceWindow(start=NOW - dt.timedelta(days=15), end=NOW - dt.timedelta(days=1)),
        purge=dt.timedelta(days=5),
        embargo=dt.timedelta(days=5),
        power_or_ci_gate="studentized bootstrap CI width <= 0.02 with >= 40 observations",
    )


def _data(
    market_id: MarketId = MarketId.US_EQUITIES,
    *,
    source_kind: EvidenceKind = EvidenceKind.REAL,
    evidence_use: EvidenceUse = EvidenceUse.RESEARCH,
) -> DayPointInTimeDataManifest:
    return DayPointInTimeDataManifest(
        market_id=market_id,
        data_manifest_sha256=SHA_B,
        universe_snapshot_id=f"{market_id.value}-pit-20260819",
        point_in_time_as_of=NOW - dt.timedelta(days=1),
        source_kind=source_kind,
        evidence_use=evidence_use,
        full_universe=False,
    )


def _cost(market_id: MarketId = MarketId.US_EQUITIES) -> DayMarketCostEvaluator:
    return DayMarketCostEvaluator(
        market_id=market_id,
        cost_model_id=f"{market_id.value}_cost_v1",
        slippage_model_id=f"{market_id.value}_slippage_v1",
        evaluator_sha256=SHA_C,
    )


def _diagnostics(market_id: MarketId = MarketId.US_EQUITIES) -> DaySelectionDiagnostics:
    return DaySelectionDiagnostics(
        market_id=market_id,
        input_attempt_ids=("attempt-1", "attempt-2", "attempt-3"),
        total_attempted_variants=3,
        status=IntradayOverfitDiagnosticsStatus.DIAGNOSTIC_READY,
        diagnostics_artifact_ref=f"artifact://safe/{SHA_C}",
        diagnostics_sha256=SHA_C,
        deflated_sharpe_probability=0.91,
        pbo_probability=0.12,
        cscv_partitions=4,
    )


def _seal(market_id: MarketId = MarketId.US_EQUITIES) -> DayHistoricalEvidenceSeal:
    payload = DayHistoricalEvidencePayload(
        capsule_id=SHA_A,
        hypothesis_version_id=SHA_B,
        market_id=market_id,
        code_sha256=SHA_A,
        parameter_set_sha256=SHA_C,
        preregistration=_preregistration(),
        data_manifest=_data(market_id),
        cost_evaluator=_cost(market_id),
        evaluator_sha256=SHA_C,
        attempted_variant_count=3,
        selection_diagnostics=_diagnostics(market_id),
        holdout_reveal=DayHoldoutRevealReceipt(
            reveal_id="reveal-1",
            legacy_hypothesis_id="legacy-hypothesis-1",
            market_id=market_id,
            hypothesis_version_id=SHA_B,
            code_sha256=SHA_A,
            data_manifest_sha256=SHA_B,
            parameter_set_sha256=SHA_C,
            sanitized_result_id="terminal-1",
            revealed_at=NOW,
        ),
        classification=TerminalOutcome.SUPPORTED,
        artifact_refs=(f"artifact://safe/{SHA_A}", f"artifact://safe/{SHA_C}"),
        evaluated_at=NOW,
    )
    return DayHistoricalEvidenceSeal(seal_id=payload.content_sha256, payload=payload)


def test_seal_binds_preregistered_windows_market_cost_and_all_attempts() -> None:
    seal = _seal()

    assert seal.payload.preregistration.purge == dt.timedelta(days=5)
    assert seal.payload.preregistration.embargo == dt.timedelta(days=5)
    assert seal.payload.data_manifest.market_id is MarketId.US_EQUITIES
    assert seal.payload.cost_evaluator.market_id is MarketId.US_EQUITIES
    assert seal.payload.attempted_variant_count == len(seal.payload.selection_diagnostics.input_attempt_ids)
    assert seal.payload.selection_diagnostics.deflated_sharpe_probability == 0.91
    assert seal.payload.selection_diagnostics.pbo_probability == 0.12
    assert seal.payload.classification is TerminalOutcome.SUPPORTED
    assert seal.payload.promotion_authority is False
    assert seal.payload.paper_order_authority is False


def test_us_and_kr_evidence_cannot_share_a_seal() -> None:
    us = _seal()

    with pytest.raises(ValidationError, match="market"):
        _ = DayHistoricalEvidencePayload.model_validate(
            us.payload.model_dump(mode="python") | {"market_id": MarketId.KR_EQUITIES}
        )
    assert _seal(MarketId.KR_EQUITIES).seal_id != us.seal_id


@pytest.mark.parametrize("source_kind", (EvidenceKind.SYNTHETIC, EvidenceKind.REPLAY, EvidenceKind.BACKTEST))
def test_non_real_history_is_wiring_only(source_kind: EvidenceKind) -> None:
    with pytest.raises(ValidationError, match="wiring"):
        _ = _data(source_kind=source_kind, evidence_use=EvidenceUse.RESEARCH)
    assert _data(source_kind=source_kind, evidence_use=EvidenceUse.WIRING_ONLY).evidence_use is EvidenceUse.WIRING_ONLY


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("code_sha256", SHA_B),
        ("data_manifest_sha256", SHA_C),
        ("parameter_set_sha256", SHA_A),
    ),
)
def test_attempt_diagnostics_and_reveal_lineage_fail_closed_on_reuse(
    field: str,
    replacement: str,
) -> None:
    diagnostics = _diagnostics()
    with pytest.raises(ValidationError, match="attempt"):
        _ = DaySelectionDiagnostics.model_validate(
            diagnostics.model_dump(mode="python") | {"total_attempted_variants": 4}
        )

    seal = _seal()
    with pytest.raises(ValidationError, match="lineage"):
        changed_reveal = DayHoldoutRevealReceipt.model_validate(
            seal.payload.holdout_reveal.model_dump(mode="python") | {field: replacement}
        )
        _ = DayHistoricalEvidencePayload.model_validate(
            seal.payload.model_dump(mode="python") | {"holdout_reveal": changed_reveal}
        )


def test_feedback_structurally_excludes_exact_holdout_metrics() -> None:
    feedback = DayDiscoveryEvidenceFeedback(
        classification=TerminalOutcome.INCONCLUSIVE,
        reason_codes=("INSUFFICIENT_OBSERVATIONS",),
        preregistered_summary="studentized bootstrap CI gate not yet met",
        selection_diagnostics_status=IntradayOverfitDiagnosticsStatus.COLLECTING,
        next_review_date=dt.date(2026, 8, 21),
    )

    fields = set(DayDiscoveryEvidenceFeedback.model_fields)
    assert "exact_metrics" not in fields
    assert "holdout_values" not in fields
    assert "symbol_contributions" not in fields
    assert "account_id" not in fields
    assert all(token not in feedback.model_dump_json() for token in ("0.91", "0.12"))


def test_online_e_value_or_fdr_claim_requires_separately_validated_evaluator() -> None:
    seal = _seal()
    with pytest.raises(ValidationError, match="e_value"):
        _ = DayHistoricalEvidencePayload.model_validate(
            seal.payload.model_dump(mode="python") | {"online_e_value_or_fdr_claim": True}
        )

    evaluator = ValidatedMarketTimeSeriesEValueEvaluator(
        version="market-time-series-evalue-v1",
        validation_artifact_ref=f"artifact://safe/{SHA_A}",
        validation_sha256=SHA_A,
    )
    claimed = DayHistoricalEvidencePayload.model_validate(
        seal.payload.model_dump(mode="python") | {"online_e_value_or_fdr_claim": True, "e_value_evaluator": evaluator}
    )
    assert claimed.e_value_evaluator == evaluator


def test_private_content_addressed_seal_round_trip_is_idempotent(tmp_path: Path) -> None:
    seal = _seal()
    root = tmp_path / "private-evidence"

    path, created = publish_day_historical_evidence(root, seal)
    replay_path, replay_created = publish_day_historical_evidence(root, seal)

    assert created is True
    assert replay_created is False
    assert replay_path == path
    assert path.name == f"day_historical_evidence_{seal.seal_id}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_day_historical_evidence(path) == seal


def test_store_rejects_filename_or_payload_tampering(tmp_path: Path) -> None:
    seal = _seal()
    path, _ = publish_day_historical_evidence(tmp_path / "evidence", seal)
    renamed = path.with_name("day_historical_evidence_" + SHA_B + ".json")
    renamed.write_bytes(path.read_bytes())
    renamed.chmod(0o600)

    with pytest.raises(InvalidDayHistoricalEvidenceError):
        _ = load_day_historical_evidence(renamed)
