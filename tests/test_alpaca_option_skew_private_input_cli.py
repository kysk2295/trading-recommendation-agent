from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from tests.alpaca_option_skew_fixtures import (
    publish_spot_inputs,
    publish_surface,
)
from trading_agent.alpaca_option_chain_models import OptionContractType

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alpaca_option_skew.py"


def test_non_private_runtime_store_is_rejected_without_output(
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
    runtime_store.chmod(0o644)
    output = tmp_path / "skew"

    # When
    completed = subprocess.run(
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
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode != 0
    assert not output.exists()
