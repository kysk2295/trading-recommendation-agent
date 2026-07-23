from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from tests.alpaca_option_skew_fixtures import (
    NY,
    SYMBOL,
    publish_spot_inputs,
    publish_surface,
)
from trading_agent.alpaca_option_chain_models import OptionContractType

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alpaca_option_skew.py"


def test_source_backed_spot_publishes_preregistered_option_skew(
    tmp_path: Path,
) -> None:
    # Given
    call_surface = publish_surface(
        tmp_path,
        OptionContractType.CALL,
        (Decimal("0.30"), Decimal("0.25"), Decimal("0.28")),
        (Decimal("0.20"), Decimal("0.50"), Decimal("0.80")),
    )
    put_surface = publish_surface(
        tmp_path,
        OptionContractType.PUT,
        (Decimal("0.40"), Decimal("0.35"), Decimal("0.30")),
        (Decimal("-0.80"), Decimal("-0.50"), Decimal("-0.20")),
    )
    runtime_store, dataset = publish_spot_inputs(tmp_path)
    output = tmp_path / "skew"

    # When
    completed = run_skew_cli(
        call_surface,
        put_surface,
        runtime_store,
        dataset,
        output,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    artifacts = tuple(output.glob("option_skew_*.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "ready"
    assert payload["underlying_symbol"] == SYMBOL
    assert payload["spot_price"] == "100.0"
    assert payload["observation_skew_seconds"] == "30.0"
    assert [(item["bucket_id"], item["median_put_minus_call_iv"]) for item in payload["strike_buckets"]] == [
        ("moneyness_9500_10000", "0.10"),
        ("moneyness_10000_10500", "0.10"),
        ("moneyness_10500_11000", "0.02"),
    ]
    assert {item["bucket_id"]: item["put_minus_call_median_iv"] for item in payload["delta_buckets"]}[
        "absolute_delta_4000_6000"
    ] == "0.10"
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    assert "- network access: 0" in (output / "alpaca_option_skew_ko.md").read_text(encoding="utf-8")


def test_spot_received_after_surface_as_of_is_rejected(
    tmp_path: Path,
) -> None:
    # Given
    call_surface = publish_surface(
        tmp_path,
        OptionContractType.CALL,
        (Decimal("0.30"), Decimal("0.25"), Decimal("0.28")),
        (Decimal("0.20"), Decimal("0.50"), Decimal("0.80")),
    )
    put_surface = publish_surface(
        tmp_path,
        OptionContractType.PUT,
        (Decimal("0.40"), Decimal("0.35"), Decimal("0.30")),
        (Decimal("-0.80"), Decimal("-0.50"), Decimal("-0.20")),
    )
    runtime_store, dataset = publish_spot_inputs(
        tmp_path,
        received_at=dt.datetime(
            2026,
            7,
            17,
            10,
            2,
            tzinfo=NY,
        ),
    )
    output = tmp_path / "skew"

    # When
    completed = run_skew_cli(
        call_surface,
        put_surface,
        runtime_store,
        dataset,
        output,
    )

    # Then
    assert completed.returncode != 0
    assert not output.exists()


def test_runtime_close_not_bound_to_canonical_payload_is_rejected(
    tmp_path: Path,
) -> None:
    # Given
    call_surface = publish_surface(
        tmp_path,
        OptionContractType.CALL,
        (Decimal("0.30"), Decimal("0.25"), Decimal("0.28")),
        (Decimal("0.20"), Decimal("0.50"), Decimal("0.80")),
    )
    put_surface = publish_surface(
        tmp_path,
        OptionContractType.PUT,
        (Decimal("0.40"), Decimal("0.35"), Decimal("0.30")),
        (Decimal("-0.80"), Decimal("-0.50"), Decimal("-0.20")),
    )
    runtime_store, dataset = publish_spot_inputs(
        tmp_path,
        runtime_close=Decimal("101"),
    )
    output = tmp_path / "skew"

    # When
    completed = run_skew_cli(
        call_surface,
        put_surface,
        runtime_store,
        dataset,
        output,
    )

    # Then
    assert completed.returncode != 0
    assert not output.exists()


def test_spot_received_before_bar_completion_is_rejected(
    tmp_path: Path,
) -> None:
    # Given
    call_surface = publish_surface(
        tmp_path,
        OptionContractType.CALL,
        (Decimal("0.30"), Decimal("0.25"), Decimal("0.28")),
        (Decimal("0.20"), Decimal("0.50"), Decimal("0.80")),
    )
    put_surface = publish_surface(
        tmp_path,
        OptionContractType.PUT,
        (Decimal("0.40"), Decimal("0.35"), Decimal("0.30")),
        (Decimal("-0.80"), Decimal("-0.50"), Decimal("-0.20")),
    )
    runtime_store, dataset = publish_spot_inputs(
        tmp_path,
        received_at=dt.datetime(
            2026,
            7,
            17,
            10,
            0,
            30,
            tzinfo=NY,
        ),
    )
    output = tmp_path / "skew"

    # When
    completed = run_skew_cli(
        call_surface,
        put_surface,
        runtime_store,
        dataset,
        output,
    )

    # Then
    assert completed.returncode != 0
    assert not output.exists()


def run_skew_cli(
    call_surface: Path,
    put_surface: Path,
    runtime_store: Path,
    dataset: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--call-surface",
            str(call_surface),
            "--put-surface",
            str(put_surface),
            "--spot-runtime-store",
            str(runtime_store),
            "--spot-dataset",
            str(dataset),
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
