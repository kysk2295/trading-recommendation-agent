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
