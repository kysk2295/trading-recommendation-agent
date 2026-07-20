from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kr_theme_day_onboarding import _prepared_request
from trading_agent.experiment_ledger_store import InvalidExperimentLedgerSourceError
from trading_agent.private_experiment_ledger_snapshot import (
    open_private_experiment_ledger_snapshot,
)


def test_private_ledger_snapshot_rejects_wal_drift(tmp_path: Path) -> None:
    # Given
    request = _prepared_request(tmp_path)
    wal = Path(f"{request.paths.experiment_ledger}-wal")
    assert wal.is_file()

    with open_private_experiment_ledger_snapshot(request.paths.experiment_ledger) as snapshot:
        assert snapshot.multi_market_trials()

        # When
        with wal.open("ab") as handle:
            _ = handle.write(b"drift")

        # Then
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = snapshot.multi_market_trials()


def test_private_ledger_snapshot_rejects_uri_reinterpreted_path(tmp_path: Path) -> None:
    # Given
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    request = _prepared_request(actual)
    decoy = tmp_path / "actual%2Fexperiment.sqlite3"
    decoy.write_bytes(b"not the ledger")
    decoy.chmod(0o600)
    assert decoy.stat().st_ino != request.paths.experiment_ledger.stat().st_ino

    # When / Then
    with (
        pytest.raises(InvalidExperimentLedgerSourceError),
        open_private_experiment_ledger_snapshot(decoy),
    ):
        pass
