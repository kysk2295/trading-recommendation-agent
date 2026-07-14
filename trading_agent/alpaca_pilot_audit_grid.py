from __future__ import annotations

import datetime as dt

from trading_agent.alpaca_scanner_quality_models import (
    PORTFOLIO_LIMIT,
    ScannerCandidate,
    scanner_quality_grid,
    select_scanner_grid_union,
)


def scanner_grid_union_issues(
    session_date: dt.date,
    contract: str | None,
    base_symbol_count: int | None,
    base_symbols: tuple[str, ...] | None,
    config_count: int | None,
    portfolio_limit: int | None,
    decisions: tuple[ScannerCandidate, ...],
    decision_base_symbols: frozenset[str],
    archived_symbols: frozenset[str],
) -> tuple[str, ...]:
    issues: list[str] = []
    if contract != "base_plus_scanner_grid_top_10_union":
        issues.append(f"candidate_selection_contract:{session_date}")
    if base_symbol_count != len(decision_base_symbols):
        issues.append(f"base_selected_symbol_count:{session_date}")
    if base_symbols is None or frozenset(base_symbols) != decision_base_symbols:
        issues.append(f"base_selected_symbols:{session_date}")
    if config_count != len(scanner_quality_grid()):
        issues.append(f"scanner_grid_config_count:{session_date}")
    if portfolio_limit != PORTFOLIO_LIMIT:
        issues.append(f"scanner_grid_portfolio_limit:{session_date}")
    expected = decision_base_symbols | frozenset(select_scanner_grid_union(decisions))
    if archived_symbols != expected:
        issues.append(f"scanner_grid_union:{session_date}")
    return tuple(issues)
