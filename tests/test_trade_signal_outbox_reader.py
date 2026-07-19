from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_alpaca_sip_dynamic_quote_actionability import _base
from trading_agent.contract_outbox import append_trade_signal_publication
from trading_agent.trade_signal_outbox_reader import (
    TradeSignalOutboxReaderError,
    read_trade_signal_publications,
)


def test_reader_replays_structural_signal_outbox(tmp_path: Path) -> None:
    publication = _base(entry="100.10", stop="99.00")
    path = tmp_path / "trade-signals.v1.jsonl"
    assert append_trade_signal_publication(path, tmp_path / "cards", publication) is True

    assert read_trade_signal_publications(path) == (publication,)
    assert read_trade_signal_publications(tmp_path / "missing.jsonl") == ()


def test_reader_rejects_duplicate_or_malformed_publications(tmp_path: Path) -> None:
    publication = _base(entry="100.10", stop="99.00")
    line = publication.model_dump_json()
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(TradeSignalOutboxReaderError):
        _ = read_trade_signal_publications(duplicate)

    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(TradeSignalOutboxReaderError):
        _ = read_trade_signal_publications(malformed)
