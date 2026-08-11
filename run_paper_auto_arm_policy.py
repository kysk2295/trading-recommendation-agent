#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///

from __future__ import annotations

from trading_agent.paper_auto_arm_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
