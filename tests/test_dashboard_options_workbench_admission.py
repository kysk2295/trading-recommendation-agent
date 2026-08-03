from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.test_dashboard_options_workbench_projection import TRACE_ID, _seed_option_stores
from trading_agent.alpaca_option_chain_models import OptionFeed
from trading_agent.dashboard_options_workbench_projection import project_options_workbench


def test_projection_allows_fresh_indicative_quotes_for_research_only_selection(
    tmp_path: Path,
) -> None:
    observed_at = dt.datetime(2026, 7, 23, 14, 32, tzinfo=dt.UTC)
    outputs = tmp_path / "outputs"
    _seed_option_stores(outputs, observed_at=observed_at, feed=OptionFeed.INDICATIVE)

    result = project_options_workbench(
        outputs=outputs,
        now=observed_at + dt.timedelta(minutes=1),
        derivatives_trace_id=TRACE_ID,
    )

    assert result.market.state == result.chain.state == "populated"
    assert result.market.blocker_code == result.chain.blocker_code is None
    assert result.chain.selected_expiration == "2026-07-24"
    assert result.chain.expirations == ("2026-07-24",)
    assert result.chain.total_count == result.chain.projected_count == 1
    cell = result.chain.rows[0].call
    assert cell is not None
    assert (cell.contract_id, cell.provider, cell.side) == (
        "alpaca:6e58f870-fe73-4583-81e4-b9a37892c36f",
        "alpaca",
        "call",
    )
    assert (result.chain.rows[0].strike, cell.bid, cell.ask) == ("200", "5", "5.2")
    assert cell.observed_at == observed_at - dt.timedelta(seconds=30)
    assert cell.trace_id == TRACE_ID
    assert cell.state == "indicative"
    assert cell.selectable is True
    assert all(term not in result.model_dump_json().lower() for term in ("current", "realtime", "profit"))


def test_projection_blocks_unlicensed_opra_without_rows(tmp_path: Path) -> None:
    observed_at = dt.datetime(2026, 7, 23, 14, 32, tzinfo=dt.UTC)
    outputs = tmp_path / "outputs"
    _seed_option_stores(outputs, observed_at=observed_at, feed=OptionFeed.OPRA)

    result = project_options_workbench(
        outputs=outputs,
        now=observed_at + dt.timedelta(minutes=1),
        derivatives_trace_id=TRACE_ID,
    )

    assert result.market.state == result.chain.state == "blocked"
    assert result.market.blocker_code == result.chain.blocker_code == "current_quote_not_licensed"
    assert result.chain.rows == ()
    assert result.chain.total_count == result.chain.projected_count == 0


@pytest.mark.parametrize(
    ("now_offset", "chain_mode", "state", "blocker"),
    (
        (dt.timedelta(days=1), 0o600, "stale", "derivative_surface_stale"),
        (dt.timedelta(minutes=1), 0o644, "corrupt", "derivatives_source_invalid"),
    ),
)
def test_projection_fails_closed_for_stale_or_corrupt_private_stores(
    tmp_path: Path,
    now_offset: dt.timedelta,
    chain_mode: int,
    state: str,
    blocker: str,
) -> None:
    observed_at = dt.datetime(2026, 7, 23, 14, 32, tzinfo=dt.UTC)
    outputs = tmp_path / "outputs"
    _seed_option_stores(outputs, observed_at=observed_at, feed=OptionFeed.INDICATIVE)
    (outputs / "derivatives" / "option-chain.sqlite3").chmod(chain_mode)

    result = project_options_workbench(outputs=outputs, now=observed_at + now_offset, derivatives_trace_id=TRACE_ID)

    assert (result.chain.state, result.chain.blocker_code, result.chain.rows) == (state, blocker, ())
    assert result.chain.trace_id == TRACE_ID
