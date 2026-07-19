from __future__ import annotations

from pathlib import Path

from trading_agent.private_report import write_private_report


def write_runtime_fleet_cycle_report(
    output_dir: Path,
    report_name: str,
    details: tuple[str, ...],
) -> None:
    content = "\n".join(
        (
            "# US runtime fleet cycle",
            "",
            "> Scanner/profile 기반 Alpaca SIP GET-only M4.4 결과입니다.",
            "",
            *(f"- {item}" for item in details),
            "",
        )
    )
    write_private_report(output_dir / report_name, content)


__all__ = ("write_runtime_fleet_cycle_report",)
