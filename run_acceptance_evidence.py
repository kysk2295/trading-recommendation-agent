#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

from trading_agent.acceptance_evidence import main

if __name__ == "__main__":
    raise SystemExit(main())
