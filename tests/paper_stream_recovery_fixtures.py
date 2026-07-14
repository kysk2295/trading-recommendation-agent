from __future__ import annotations

import datetime as dt

from tests.trade_update_ledger_fixtures import FINGERPRINT
from trading_agent.paper_stream_recovery import PaperStreamRecoveryObservation


def recovery(
    *,
    epoch: str,
    started_at: dt.datetime,
    completed_at: dt.datetime,
) -> PaperStreamRecoveryObservation:
    return PaperStreamRecoveryObservation(
        account_fingerprint=FINGERPRINT,
        connection_epoch=epoch,
        started_at=started_at,
        completed_at=completed_at,
        snapshot_json='{"orders":[],"positions":[]}',
        execution_detail_complete=True,
    )
