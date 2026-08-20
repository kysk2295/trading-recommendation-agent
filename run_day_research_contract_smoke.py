from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.day_research_contract_smoke import (
    InvalidDayResearchContractSmokeError,
    run_day_research_contract_smoke,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the synthetic KR/US Day research contract foundation locally"
    )
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        result = run_day_research_contract_smoke(args.fixture, args.database)
        sys.stdout.write(
            json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return 0
    except (
        InvalidDayResearchContractSmokeError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ):
        sys.stdout.write('{"status":"blocked"}\n')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
