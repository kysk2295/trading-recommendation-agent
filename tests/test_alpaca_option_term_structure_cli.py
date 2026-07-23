from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from trading_agent.alpaca_option_chain_models import (
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_contract_provider_models import (
    OptionExerciseStyle,
)
from trading_agent.alpaca_option_surface import (
    AlpacaOptionSurface,
    OptionSurfaceContract,
    OptionSurfaceStatus,
    publish_alpaca_option_surface,
)

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alpaca_option_term_structure.py"


def test_ready_surfaces_publish_ordered_term_structure(
    tmp_path: Path,
) -> None:
    # Given
    first = _publish_surface(tmp_path, dt.date(2026, 7, 31), 100, "first")
    second = _publish_surface(tmp_path, dt.date(2026, 8, 7), 200, "second")
    output = tmp_path / "term"

    # When
    initial = _run(first, second, output)

    # Then
    assert initial.returncode == 0, initial.stderr
    artifacts = tuple(output.glob("option_term_structure_*.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["underlying_symbol"] == "AAPL"
    assert payload["expiration_count"] == 2
    assert [item["days_to_expiry"] for item in payload["slices"]] == [8, 15]
    assert [item["total_open_interest"] for item in payload["slices"]] == [
        100,
        200,
    ]
    assert payload["slices"][0]["median_implied_volatility"] == "0.4"
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    report = (output / "alpaca_option_term_structure_ko.md").read_text(
        encoding="utf-8"
    )
    assert "- artifact created: yes" in report
    assert "- network access: 0" in report


def test_exact_term_structure_replay_reuses_artifact(tmp_path: Path) -> None:
    # Given
    first = _publish_surface(tmp_path, dt.date(2026, 7, 31), 100, "first")
    second = _publish_surface(tmp_path, dt.date(2026, 8, 7), 200, "second")
    output = tmp_path / "term"
    assert _run(first, second, output).returncode == 0

    # When
    replay = _run(first, second, output)

    # Then
    assert replay.returncode == 0, replay.stderr
    assert tuple(output.glob("option_term_structure_*.json"))
    assert "artifact_created=no" in replay.stdout


def test_term_slice_preserves_open_interest_observation_date(
    tmp_path: Path,
) -> None:
    # Given
    first = _publish_surface(tmp_path, dt.date(2026, 7, 31), 100, "first")
    second = _publish_surface(tmp_path, dt.date(2026, 8, 7), 200, "second")
    output = tmp_path / "term"

    # When
    completed = _run(first, second, output)

    # Then
    assert completed.returncode == 0, completed.stderr
    artifact = next(output.glob("option_term_structure_*.json"))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert {
        item["open_interest_date"] for item in payload["slices"]
    } == {"2026-07-22"}


def test_non_content_addressed_surface_name_is_rejected_without_output(
    tmp_path: Path,
) -> None:
    # Given
    first = _publish_surface(tmp_path, dt.date(2026, 7, 31), 100, "first")
    second = _publish_surface(tmp_path, dt.date(2026, 8, 7), 200, "second")
    renamed = tmp_path / "renamed-surface.json"
    shutil.copyfile(first, renamed)
    renamed.chmod(0o600)
    output = tmp_path / "term"

    # When
    completed = _run(renamed, second, output)

    # Then
    assert completed.returncode != 0
    assert not output.exists()


def test_report_exposes_aggregate_values_without_contract_details(
    tmp_path: Path,
) -> None:
    # Given
    first = _publish_surface(tmp_path, dt.date(2026, 7, 31), 100, "first")
    second = _publish_surface(tmp_path, dt.date(2026, 8, 7), 200, "second")
    output = tmp_path / "term"

    # When
    completed = _run(first, second, output)

    # Then
    assert completed.returncode == 0, completed.stderr
    report = (output / "alpaca_option_term_structure_ko.md").read_text(
        encoding="utf-8"
    )
    assert "total_oi=100, median_iv=0.4" in report
    assert "provider_symbol" not in report
    assert "instrument_id" not in report


def _publish_surface(
    root: Path,
    expiration_date: dt.date,
    open_interest: int,
    token: str,
) -> Path:
    observed_at = dt.datetime(2026, 7, 23, 15, 0, tzinfo=dt.UTC)
    contract = OptionSurfaceContract(
        instrument_id=f"alpaca:{token}",
        provider_symbol=f"AAPL{expiration_date:%y%m%d}C00200000",
        underlying_instrument_id="alpaca:aapl",
        root_symbol="AAPL",
        expiration_date=expiration_date,
        strike_price=Decimal("200"),
        contract_type=OptionContractType.CALL,
        exercise_style=OptionExerciseStyle.AMERICAN,
        multiplier=Decimal(100),
        tradable=True,
        open_interest=open_interest,
        open_interest_date=dt.date(2026, 7, 22),
        close_price=None,
        close_price_date=None,
        master_observed_at=observed_at,
        snapshot_present=True,
        latest_quote=None,
        latest_trade=None,
        implied_volatility=Decimal("0.4"),
        greeks=None,
    )
    surface = AlpacaOptionSurface(
        status=OptionSurfaceStatus.READY,
        feed=OptionFeed.INDICATIVE,
        underlying_symbol="AAPL",
        expiration_date=expiration_date,
        contract_type=OptionContractType.CALL,
        master_request_id=_sha(f"master-request-{token}"),
        master_run_id=_sha(f"master-run-{token}"),
        master_run_sha256=_sha(f"master-sha-{token}"),
        chain_request_id=_sha(f"chain-request-{token}"),
        chain_run_id=_sha(f"chain-run-{token}"),
        chain_run_sha256=_sha(f"chain-sha-{token}"),
        master_observed_at=observed_at,
        surface_observed_at=observed_at,
        master_contract_count=1,
        chain_snapshot_count=1,
        joined_contract_count=1,
        snapshot_coverage_bps=10_000,
        open_interest_count=1,
        quote_count=0,
        trade_count=0,
        implied_volatility_count=1,
        greeks_count=0,
        contracts=(contract,),
    )
    return publish_alpaca_option_surface(root / token, surface)[0]


def _run(first: Path, second: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--surface",
            str(first),
            "--surface",
            str(second),
            "--max-observation-skew-seconds",
            "300",
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
