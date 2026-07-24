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
from trading_agent.alpaca_option_term_structure import (
    publish_alpaca_option_term_structure,
)
from trading_agent.alpaca_option_term_structure_models import (
    AlpacaOptionTermStructure,
    OptionTermSlice,
    OptionTermStructureStatus,
)

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_option_iv_term_context.py"
OBSERVED_AT = dt.datetime(2026, 7, 23, 15, tzinfo=dt.UTC)


def test_ready_term_structure_publishes_front_premium_context(tmp_path: Path) -> None:
    source = _term_structure(tmp_path, near_iv="0.45", far_iv="0.40")
    output = tmp_path / "context"

    completed = _run(source, output)

    assert completed.returncode == 0, completed.stderr
    artifact = next(output.glob("option_iv_term_context_*.json"))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["state"] == "front_premium"
    assert payload["front_minus_back_iv"] == "0.05"
    assert payload["near_days_to_expiry"] == 8
    assert payload["far_days_to_expiry"] == 15
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    report = (output / "option_iv_term_context_ko.md").read_text(encoding="utf-8")
    assert "- result: ready" in report
    assert "- broker, account, or order mutation: none" in report


def test_exact_context_replay_reuses_content_addressed_artifact(tmp_path: Path) -> None:
    source = _term_structure(tmp_path, near_iv="0.40", far_iv="0.45")
    output = tmp_path / "context"
    assert _run(source, output).returncode == 0

    replay = _run(source, output)

    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=no" in replay.stdout
    assert len(tuple(output.glob("option_iv_term_context_*.json"))) == 1


def test_renamed_term_structure_is_rejected_without_output(tmp_path: Path) -> None:
    source = _term_structure(tmp_path, near_iv="0.45", far_iv="0.40")
    renamed = tmp_path / "renamed.json"
    shutil.copyfile(source, renamed)
    renamed.chmod(0o600)
    output = tmp_path / "context"

    completed = _run(renamed, output)

    assert completed.returncode != 0
    assert not output.exists()


def test_mixed_option_rights_are_rejected_without_output(tmp_path: Path) -> None:
    source = _term_structure(
        tmp_path,
        near_iv="0.45",
        far_iv="0.40",
        far_contract_type=OptionContractType.PUT,
    )
    output = tmp_path / "context"

    completed = _run(source, output)

    assert completed.returncode != 0
    assert not output.exists()


def _term_structure(
    root: Path,
    *,
    near_iv: str,
    far_iv: str,
    far_contract_type: OptionContractType = OptionContractType.CALL,
) -> Path:
    slices = (
        _slice(dt.date(2026, 7, 31), 8, near_iv, OptionContractType.CALL, "near"),
        _slice(dt.date(2026, 8, 7), 15, far_iv, far_contract_type, "far"),
    )
    structure = AlpacaOptionTermStructure(
        status=OptionTermStructureStatus.READY,
        feed=OptionFeed.INDICATIVE,
        underlying_symbol="AAPL",
        market_date=dt.date(2026, 7, 23),
        as_of=OBSERVED_AT,
        maximum_observation_skew_seconds=300,
        expiration_count=2,
        surface_count=2,
        slices=slices,
    )
    return publish_alpaca_option_term_structure(root / "term", structure)[0]


def _slice(
    expiration: dt.date,
    days: int,
    median_iv: str,
    contract_type: OptionContractType,
    token: str,
) -> OptionTermSlice:
    return OptionTermSlice(
        surface_id=_sha(f"surface-{token}"),
        surface_sha256=_sha(f"surface-file-{token}"),
        expiration_date=expiration,
        contract_type=contract_type,
        days_to_expiry=days,
        surface_observed_at=OBSERVED_AT,
        contract_count=10,
        open_interest_observation_count=10,
        open_interest_date=dt.date(2026, 7, 22),
        total_open_interest=100,
        implied_volatility_observation_count=10,
        median_implied_volatility=Decimal(median_iv),
    )


def _run(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--term-structure",
            str(source),
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
