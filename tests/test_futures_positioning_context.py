from __future__ import annotations

import datetime as dt
import hashlib
import json
import stat
from pathlib import Path

import pytest

from tests.test_futures_roll_security_master_cli import _manifest
from trading_agent.cftc_tff_artifact import publish_cftc_tff_context
from trading_agent.cftc_tff_models import CftcTffRawResponse, CftcTffRequest
from trading_agent.cftc_tff_parser import parse_cftc_tff_context
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.futures_positioning_context import (
    build_futures_positioning_context,
    load_cftc_tff_context_artifact,
    load_futures_positioning_binding,
    load_futures_roll_master_artifact,
    publish_futures_positioning_context,
)
from trading_agent.futures_positioning_context_models import (
    FuturesPositioningBinding,
    FuturesPositioningContextError,
    FuturesPositioningJoinRequest,
    LoadedCftcTffContext,
    LoadedFuturesPositioningBinding,
)
from trading_agent.futures_roll_security_master import (
    load_futures_roll_security_master,
    publish_futures_roll_security_master,
)

FIXTURE = Path(__file__).parent / "fixtures/cftc_tff/es_latest_two.json"
AS_OF = dt.datetime(2026, 7, 24, 18, 0, tzinfo=dt.UTC)


def test_join_binds_positioning_to_exact_active_contract(
    tmp_path: Path,
) -> None:
    # Given
    request = _request(tmp_path)

    # When
    context = build_futures_positioning_context(request)

    # Then
    assert context.active_instrument.value == "cme:es-202609"
    assert context.active_from <= AS_OF < context.roll_at
    assert len(context.categories) == 5
    assert context.cftc_artifact_sha256
    assert context.futures_master_artifact_sha256
    assert context.binding_artifact_sha256


def test_cftc_code_mismatch_is_rejected(tmp_path: Path) -> None:
    # Given
    request = _request(tmp_path)
    binding = request.binding.value.model_copy(
        update={"cftc_contract_market_code": "13874B"},
    )

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        _ = build_futures_positioning_context(
            request.model_copy(
                update={"binding": _loaded_binding(binding)},
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (("root_symbol", "NQ"), ("venue", "XCBT")),
)
def test_root_or_venue_mismatch_is_rejected(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    # Given
    request = _request(tmp_path)
    binding = request.binding.value.model_copy(update={field: value})

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        _ = build_futures_positioning_context(
            request.model_copy(
                update={"binding": _loaded_binding(binding)},
            ),
        )


@pytest.mark.parametrize("field", ("observed_at", "effective_from"))
def test_binding_future_time_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    # Given
    request = _request(tmp_path)
    binding = request.binding.value.model_copy(
        update={field: AS_OF + dt.timedelta(seconds=1)},
    )

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        _ = build_futures_positioning_context(
            request.model_copy(
                update={"binding": _loaded_binding(binding)},
            ),
        )


def test_cftc_observation_after_as_of_is_rejected(tmp_path: Path) -> None:
    # Given
    request = _request(tmp_path)
    cftc = request.cftc.value.model_copy(
        update={"observed_at": AS_OF + dt.timedelta(seconds=1)},
    )

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        _ = build_futures_positioning_context(
            request.model_copy(
                update={
                    "cftc": LoadedCftcTffContext(
                        value=cftc,
                        artifact_sha256=request.cftc.artifact_sha256,
                    ),
                },
            ),
        )


def test_report_older_than_maximum_age_is_rejected(
    tmp_path: Path,
) -> None:
    # Given
    request = _request(tmp_path).model_copy(
        update={
            "as_of": dt.datetime(2026, 7, 30, 18, 0, tzinfo=dt.UTC),
        },
    )

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        _ = build_futures_positioning_context(request)


def test_report_date_after_as_of_utc_date_is_rejected(
    tmp_path: Path,
) -> None:
    # Given
    request = _request(tmp_path)
    cftc = request.cftc.value.model_copy(
        update={"latest_report_date": AS_OF.date() + dt.timedelta(days=1)},
    )
    request = request.model_copy(
        update={
            "cftc": LoadedCftcTffContext(
                value=cftc,
                artifact_sha256=request.cftc.artifact_sha256,
            ),
        },
    )

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        _ = build_futures_positioning_context(request)


def test_as_of_at_roll_boundary_selects_next_contract(
    tmp_path: Path,
) -> None:
    # Given
    request = _request(tmp_path)
    roll_at = dt.datetime(2026, 9, 10, 21, 0, tzinfo=dt.UTC)
    cftc = request.cftc.value.model_copy(
        update={
            "latest_report_date": dt.date(2026, 9, 9),
            "previous_report_date": dt.date(2026, 9, 2),
            "observed_at": roll_at - dt.timedelta(hours=1),
        },
    )
    request = request.model_copy(
        update={
            "as_of": roll_at,
            "cftc": LoadedCftcTffContext(
                value=cftc,
                artifact_sha256=request.cftc.artifact_sha256,
            ),
        },
    )

    # When
    context = build_futures_positioning_context(request)

    # Then
    assert context.active_instrument.value == "cme:es-202612"
    assert context.active_from == roll_at


@pytest.mark.parametrize("artifact_kind", ("cftc", "master"))
def test_renamed_artifact_is_rejected_before_join(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    # Given
    cftc_path, _ = publish_cftc_tff_context(tmp_path, _cftc_context())
    master_path, _ = publish_futures_roll_security_master(
        tmp_path,
        load_futures_roll_security_master(_write_manifest(tmp_path)),
    )
    original = cftc_path if artifact_kind == "cftc" else master_path
    renamed = original.with_name(f"{original.stem[:-64]}{'0' * 64}.json")
    original.rename(renamed)

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        if artifact_kind == "cftc":
            _ = load_cftc_tff_context_artifact(renamed)
        else:
            _ = load_futures_roll_master_artifact(renamed)


def test_public_binding_is_rejected(tmp_path: Path) -> None:
    # Given
    binding_path = _write_binding(tmp_path)
    binding_path.chmod(0o644)

    # When/Then
    with pytest.raises(FuturesPositioningContextError):
        _ = load_futures_positioning_binding(binding_path)


def test_context_publisher_is_content_addressed_and_replay_safe(
    tmp_path: Path,
) -> None:
    # Given
    context = build_futures_positioning_context(_request(tmp_path))
    output = tmp_path / "context"

    # When
    path, created = publish_futures_positioning_context(output, context)
    artifact_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    replayed_path, replayed_created = publish_futures_positioning_context(
        output,
        context,
    )

    # Then
    assert path.name == f"futures_positioning_context_{context.context_id}.json"
    assert created is True
    assert replayed_path == path
    assert replayed_created is False
    assert hashlib.sha256(replayed_path.read_bytes()).hexdigest() == artifact_sha256
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def _cftc_context():
    request = CftcTffRequest(
        collection_id="es-tff-20260724",
        contract_market_code="13874A",
        through_date=AS_OF.date(),
    )
    response = CftcTffRawResponse(
        request_id=request.request_id,
        received_at=dt.datetime(2026, 7, 24, 6, 0, tzinfo=dt.UTC),
        status_code=200,
        content_type="application/json",
        raw_payload=FIXTURE.read_bytes(),
    )
    return parse_cftc_tff_context(request, response)


def _request(root: Path) -> FuturesPositioningJoinRequest:
    cftc_path, _ = publish_cftc_tff_context(root, _cftc_context())
    master_path, _ = publish_futures_roll_security_master(
        root,
        load_futures_roll_security_master(_write_manifest(root)),
    )
    return FuturesPositioningJoinRequest(
        cftc=load_cftc_tff_context_artifact(cftc_path),
        futures_master=load_futures_roll_master_artifact(master_path),
        binding=_loaded_binding(_binding()),
        as_of=AS_OF,
        maximum_report_age_days=14,
    )


def _binding() -> FuturesPositioningBinding:
    return FuturesPositioningBinding(
        cftc_contract_market_code="13874A",
        root_symbol="ES",
        venue="XCME",
        observed_at=dt.datetime(2026, 6, 1, 17, 0, tzinfo=dt.UTC),
        effective_from=dt.datetime(2026, 6, 1, 17, 0, tzinfo=dt.UTC),
        effective_to=None,
        source_reference="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
    )


def _loaded_binding(
    binding: FuturesPositioningBinding,
) -> LoadedFuturesPositioningBinding:
    return LoadedFuturesPositioningBinding(
        value=binding,
        artifact_sha256="a" * 64,
    )


def _write_binding(root: Path) -> Path:
    path = root / "futures-positioning-binding.json"
    path.write_text(
        canonical_experiment_ledger_json(_binding()) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _write_manifest(root: Path) -> Path:
    path = root / "futures-roll.json"
    path.write_text(
        json.dumps(_manifest(), separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path
