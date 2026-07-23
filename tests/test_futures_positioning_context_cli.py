from __future__ import annotations

import hashlib
import stat
import subprocess
import sys
from pathlib import Path

from tests.test_futures_positioning_context import (
    _binding,
    _cftc_context,
    _write_binding,
    _write_manifest,
)
from trading_agent.cftc_tff_artifact import publish_cftc_tff_context
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.futures_roll_security_master import (
    load_futures_roll_security_master,
    publish_futures_roll_security_master,
)

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_futures_positioning_context.py"


def test_private_inputs_publish_aggregate_context_and_exact_replay(
    tmp_path: Path,
) -> None:
    # Given
    cftc_path, _ = publish_cftc_tff_context(tmp_path, _cftc_context())
    master_path, _ = publish_futures_roll_security_master(
        tmp_path,
        load_futures_roll_security_master(_write_manifest(tmp_path)),
    )
    binding_path = _write_binding(tmp_path)
    output = tmp_path / "output"
    command = (
        sys.executable,
        str(SCRIPT),
        "--cftc-context",
        str(cftc_path),
        "--futures-master",
        str(master_path),
        "--binding",
        str(binding_path),
        "--as-of",
        "2026-07-24T18:00:00Z",
        "--maximum-report-age-days",
        "14",
        "--output-dir",
        str(output),
    )

    # When
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    artifacts = tuple(output.glob("futures_positioning_context_*.json"))
    assert len(artifacts) == 1
    artifact = artifacts[0]
    report_path = output / "futures_positioning_context_ko.md"
    report = report_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert "- category count: 5" in report
    assert "- active contract: present" in report
    assert "- network access: 0" in report
    assert "cme:es-202609" not in report
    assert "ESU6" not in report
    assert str(tmp_path) not in report

    # When
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    replay = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert replay.returncode == 0, replay.stderr
    assert "artifact_created=no" in replay.stdout
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == artifact_sha256


def test_mismatched_binding_fails_without_output_or_input_mutation(
    tmp_path: Path,
) -> None:
    # Given
    cftc_path, _ = publish_cftc_tff_context(tmp_path, _cftc_context())
    master_path, _ = publish_futures_roll_security_master(
        tmp_path,
        load_futures_roll_security_master(_write_manifest(tmp_path)),
    )
    binding_path = tmp_path / "mismatched-binding.json"
    binding_path.write_text(
        canonical_experiment_ledger_json(
            _binding().model_copy(update={"root_symbol": "NQ"}),
        )
        + "\n",
        encoding="utf-8",
    )
    binding_path.chmod(0o600)
    inputs = (cftc_path, master_path, binding_path)
    hashes_before = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs)
    output = tmp_path / "output"

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--cftc-context",
            str(cftc_path),
            "--futures-master",
            str(master_path),
            "--binding",
            str(binding_path),
            "--as-of",
            "2026-07-24T18:00:00Z",
            "--output-dir",
            str(output),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 2
    assert not output.exists()
    assert "13874A" not in completed.stdout
    assert "NQ" not in completed.stdout
    assert str(tmp_path) not in completed.stdout
    assert tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs) == hashes_before
