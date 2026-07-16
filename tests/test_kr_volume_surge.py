from __future__ import annotations

import copy
import datetime as dt
import json
import stat
from decimal import Decimal, localcontext
from pathlib import Path
from typing import NoReturn, cast

import pytest

from trading_agent.kis_kr_ranking import KisKrRankingItem, KisKrRankingKind
from trading_agent.kis_kr_ranking_collection import collect_kis_kr_rankings
from trading_agent.kis_kr_ranking_fixture import load_kis_kr_ranking_fixture
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_theme_store import KrThemeStore
from trading_agent.kr_volume_surge import (
    KR_VOLUME_SURGE_ADAPTER_VERSION,
    KrVolumeSurgeSourceNotReadyError,
    derive_kr_volume_surge,
)
from trading_agent.kr_volume_surge_models import (
    KrVolumeSurgePayloadV2,
    parse_kr_volume_surge_payload,
)

COLLECTION_DATE = dt.date(2026, 7, 16)
KST = dt.timezone(dt.timedelta(hours=9))
FLUCTUATION_AT = dt.datetime(2026, 7, 16, 10, 1, tzinfo=KST)
VOLUME_AT = dt.datetime(2026, 7, 16, 10, 2, tzinfo=KST)
DERIVED_AT = dt.datetime(2026, 7, 16, 10, 5, tzinfo=KST)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kis_kr_ranking"


def test_derivation_preserves_all_kis_volume_rows_and_alphanumeric_lineage(
    tmp_path: Path,
) -> None:
    cycle_id = "kr-volume-derive-happy-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(
        tmp_path,
        store,
        cycle_id=cycle_id,
        rows=(
            _volume_row("005930", rank=1, accumulated="1500", average="600", trading="100000"),
            _volume_row("1234A0", rank=2, accumulated="1000", average="400", trading="200000"),
        ),
    )

    result = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=lambda: DERIVED_AT,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.adapter_version == KR_VOLUME_SURGE_ADAPTER_VERSION
    assert result.run.source is KrCatalystSource.VOLUME_SURGE
    assert result.run.source_run_id == f"{cycle_id}:volume_surge"
    assert result.run.record_count == 1
    assert result.run.receipt_ids == ()
    assert result.run.started_at == DERIVED_AT
    assert result.run.completed_at == DERIVED_AT
    assert result.run.collection_date == COLLECTION_DATE
    assert result.symbol_count == 2
    assert result.new_catalyst_count == 1
    assert result.new_observation_count == 1
    assert result.restarted is False

    derived = tuple(
        item
        for item in store.catalysts()
        if item.record.source is KrCatalystSource.VOLUME_SURGE
    )
    assert len(derived) == 1
    payload = parse_kr_volume_surge_payload(derived[0].raw_payload)
    assert isinstance(payload, KrVolumeSurgePayloadV2)
    assert payload.observed_at == DERIVED_AT
    assert payload.source_observed_at == VOLUME_AT
    assert payload.source_run_id == f"{cycle_id}:kis_ranking"
    assert tuple(item.symbol for item in payload.symbols) == ("005930", "1234A0")
    assert tuple(item.trading_value_krw for item in payload.symbols) == (
        Decimal("100000"),
        Decimal("200000"),
    )
    assert tuple(item.volume_ratio for item in payload.symbols) == (
        Decimal("2.5"),
        Decimal("2.5"),
    )
    source_ids = _stored_volume_catalyst_ids(store, cycle_id=cycle_id)
    assert tuple(item.source_catalyst_id for item in payload.symbols) == source_ids
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{store.path}.writer.lock").stat().st_mode) == 0o600


def test_terminal_replay_skips_clock_and_append_hook(tmp_path: Path) -> None:
    cycle_id = "kr-volume-derive-replay-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(tmp_path, store, cycle_id=cycle_id, rows=(_volume_row("005930"),))
    first = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=lambda: DERIVED_AT,
    )

    second = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=_reject_dependency,
        _after_catalyst=_reject_dependency,
    )

    assert second.run == first.run
    assert second.symbol_count == 1
    assert second.new_catalyst_count == 0
    assert second.new_observation_count == 0
    assert second.restarted is True


def test_missing_upstream_writes_nothing(tmp_path: Path) -> None:
    store = KrThemeStore(tmp_path / "missing.sqlite3")

    with pytest.raises(KrVolumeSurgeSourceNotReadyError):
        _ = derive_kr_volume_surge(
            store,
            collection_cycle_id="kr-volume-missing-001",
            collection_date=COLLECTION_DATE,
            _clock=_reject_dependency,
        )

    assert not store.path.exists()


def test_failed_upstream_closes_failed_volume_source_without_payload(tmp_path: Path) -> None:
    cycle_id = "kr-volume-upstream-failed-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    kis_run = _seed_kis_run(
        tmp_path,
        store,
        cycle_id=cycle_id,
        rows=(),
        volume_status=429,
    )
    assert kis_run.status is KrCoverageStatus.FAILED

    result = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=lambda: DERIVED_AT,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "upstream_kis_failed"
    assert result.run.record_count == 0
    assert result.symbol_count == 0
    assert not any(
        item.record.source is KrCatalystSource.VOLUME_SURGE for item in store.catalysts()
    )


def test_zero_average_fails_whole_source_without_dropping_row(tmp_path: Path) -> None:
    cycle_id = "kr-volume-zero-average-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(
        tmp_path,
        store,
        cycle_id=cycle_id,
        rows=(_volume_row("005930", average="0"),),
    )

    clock_calls: list[None] = []

    def clock() -> dt.datetime:
        clock_calls.append(None)
        return DERIVED_AT

    result = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=clock,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "zero_average_volume"
    assert result.symbol_count == 0
    assert result.run.record_count == 0
    assert len(clock_calls) == 1


def test_zero_volume_rows_create_explicit_empty_snapshot(tmp_path: Path) -> None:
    cycle_id = "kr-volume-empty-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(tmp_path, store, cycle_id=cycle_id, rows=())

    result = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=lambda: DERIVED_AT,
    )

    derived = next(
        item
        for item in store.catalysts()
        if item.record.source is KrCatalystSource.VOLUME_SURGE
    )
    payload = parse_kr_volume_surge_payload(derived.raw_payload)
    assert isinstance(payload, KrVolumeSurgePayloadV2)
    assert payload.symbols == ()
    assert payload.source_observed_at == VOLUME_AT
    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.symbol_count == 0


def test_derivation_clock_before_upstream_is_terminal_failure(tmp_path: Path) -> None:
    cycle_id = "kr-volume-clock-skew-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(tmp_path, store, cycle_id=cycle_id, rows=(_volume_row("005930"),))

    result = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=lambda: FLUCTUATION_AT,
    )

    assert result.run.status is KrCoverageStatus.FAILED
    assert result.run.failure_code == "invalid_derivation_time"
    assert result.run.record_count == 0


def test_orphan_catalyst_restart_reuses_stored_derivation_time(tmp_path: Path) -> None:
    cycle_id = "kr-volume-orphan-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(tmp_path, store, cycle_id=cycle_id, rows=(_volume_row("005930"),))

    with pytest.raises(RuntimeError, match="synthetic crash"):
        _ = derive_kr_volume_surge(
            store,
            collection_cycle_id=cycle_id,
            collection_date=COLLECTION_DATE,
            _clock=lambda: DERIVED_AT,
            _after_catalyst=lambda: _raise_crash(),
        )
    assert not any(
        run.source is KrCatalystSource.VOLUME_SURGE for run in store.source_runs(cycle_id)
    )

    result = derive_kr_volume_surge(
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _clock=_reject_dependency,
    )

    assert result.run.status is KrCoverageStatus.SUCCESS
    assert result.run.started_at == DERIVED_AT
    assert result.new_catalyst_count == 0
    assert result.new_observation_count == 0
    assert result.restarted is True


def test_ratio_uses_fixed_decimal_context(tmp_path: Path) -> None:
    cycle_id = "kr-volume-decimal-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(
        tmp_path,
        store,
        cycle_id=cycle_id,
        rows=(_volume_row("005930", accumulated="1", average="3"),),
    )

    with localcontext() as context:
        context.prec = 2
        _ = derive_kr_volume_surge(
            store,
            collection_cycle_id=cycle_id,
            collection_date=COLLECTION_DATE,
            _clock=lambda: DERIVED_AT,
        )

    derived = next(
        item
        for item in store.catalysts()
        if item.record.source is KrCatalystSource.VOLUME_SURGE
    )
    payload = parse_kr_volume_surge_payload(derived.raw_payload)
    assert isinstance(payload, KrVolumeSurgePayloadV2)
    assert payload.symbols[0].volume_ratio == Decimal("0.3333333333333333333333333333")


def test_collection_date_mismatch_is_not_ready_and_writes_no_volume_run(
    tmp_path: Path,
) -> None:
    cycle_id = "kr-volume-date-mismatch-001"
    store = KrThemeStore(tmp_path / "kr.sqlite3")
    _seed_kis_run(tmp_path, store, cycle_id=cycle_id, rows=(_volume_row("005930"),))

    with pytest.raises(KrVolumeSurgeSourceNotReadyError):
        _ = derive_kr_volume_surge(
            store,
            collection_cycle_id=cycle_id,
            collection_date=COLLECTION_DATE - dt.timedelta(days=1),
            _clock=_reject_dependency,
        )

    assert not any(
        run.source is KrCatalystSource.VOLUME_SURGE for run in store.source_runs(cycle_id)
    )


def _seed_kis_run(
    directory: Path,
    store: KrThemeStore,
    *,
    cycle_id: str,
    rows: tuple[dict[str, str], ...],
    volume_status: int = 200,
):
    fixture_dir = directory / f"fixture-{cycle_id}"
    fixture_dir.mkdir()
    fluctuation_payload = (FIXTURE_DIR / "fluctuation-page-1.json").read_bytes()
    volume_document: dict[str, object] = {
        "rt_cd": "0",
        "msg_cd": "0",
        "msg1": "ok",
        "output": list(rows),
    }
    volume_payload = json.dumps(volume_document, ensure_ascii=False).encode()
    (fixture_dir / "fluctuation.json").write_bytes(fluctuation_payload)
    (fixture_dir / "volume.json").write_bytes(volume_payload)
    manifest = fixture_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "collection_date": COLLECTION_DATE.isoformat(),
                "pages": [
                    _fixture_page(
                        "fluctuation",
                        "fluctuation.json",
                        FLUCTUATION_AT,
                        200,
                    ),
                    _fixture_page("volume", "volume.json", VOLUME_AT, volume_status),
                ],
            }
        ),
        encoding="utf-8",
    )
    fetcher = load_kis_kr_ranking_fixture(manifest, collection_date=COLLECTION_DATE)
    return collect_kis_kr_rankings(
        fetcher,
        store,
        collection_cycle_id=cycle_id,
        collection_date=COLLECTION_DATE,
        _sleeper=lambda _: None,
    ).run


def _fixture_page(
    kind: str,
    payload_path: str,
    received_at: dt.datetime,
    status: int,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": kind,
        "page_no": 1,
        "attempt": 1,
        "request_tr_cont": "",
        "response_tr_cont": "",
        "received_at": received_at.isoformat(),
        "http_status": status,
        "content_type": "application/json",
        "payload_path": payload_path,
    }


def _volume_row(
    symbol: str,
    *,
    rank: int = 1,
    accumulated: str = "1500",
    average: str = "600",
    trading: str = "100000",
) -> dict[str, str]:
    document: object = json.loads((FIXTURE_DIR / "volume-page-1.json").read_bytes())
    assert isinstance(document, dict)
    output = document["output"]
    assert isinstance(output, list) and len(output) == 1
    row = cast(dict[str, str], copy.deepcopy(output[0]))
    row.update(
        {
            "mksc_shrn_iscd": symbol,
            "data_rank": str(rank),
            "hts_kor_isnm": f"Synthetic {rank}",
            "acml_vol": accumulated,
            "avrg_vol": average,
            "acml_tr_pbmn": trading,
        }
    )
    return row


def _stored_volume_catalyst_ids(
    store: KrThemeStore,
    *,
    cycle_id: str,
) -> tuple[str, ...]:
    observed_ids = {
        item.catalyst_id
        for item in store.observations()
        if item.collection_cycle_id == cycle_id
    }
    rows: list[tuple[str, str]] = []
    for stored in store.catalysts():
        if (
            stored.record.catalyst_id not in observed_ids
            or stored.record.source is not KrCatalystSource.KIS_RANKING
        ):
            continue
        item = KisKrRankingItem.model_validate_json(stored.raw_payload)
        if item.ranking_kind is KisKrRankingKind.VOLUME:
            rows.append((item.symbol, stored.record.catalyst_id))
    return tuple(catalyst_id for _, catalyst_id in sorted(rows))


def _raise_crash() -> NoReturn:
    raise RuntimeError("synthetic crash")


def _reject_dependency(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("replay opened a deferred dependency")
