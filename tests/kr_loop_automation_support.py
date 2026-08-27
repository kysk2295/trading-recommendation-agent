from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

from tests.kr_day_close_service_support import close_fixture
from tests.test_kis_kr_session_calendar import _payload as calendar_payload
from tests.test_kis_kr_session_calendar import _row as calendar_row
from tests.test_research_agent_service_cli import _config
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_loop_automation_config import (
    KrLoopAutomationConfig,
    write_kr_loop_automation_config,
)
from trading_agent.research_agent_service_config import (
    ResearchAgentServiceConfig,
    write_research_agent_service_config,
)

KST = dt.timezone(dt.timedelta(hours=9))


def automation_config(tmp_path: Path, *, with_calendar: bool = False):
    repository = Path(__file__).resolve().parents[1]
    research = _config(tmp_path / "research", project_root=repository)
    if with_calendar:
        fixture = close_fixture(tmp_path / "calendar")
        sources = research.source_paths.model_copy(update={"kr_calendar_store": fixture.config.calendar_store})
        research = ResearchAgentServiceConfig.model_validate(
            research.model_copy(
                update={
                    "schema_version": 4,
                    "browser_gateway_config": (tmp_path / "browser.json").absolute(),
                    "kr_market_receipt_root": (tmp_path / "market-receipts").absolute(),
                    "kr_social_signal_database": (tmp_path / "social.sqlite3").absolute(),
                    "source_paths": sources,
                }
            ).model_dump(mode="python")
        )
    research_path = (tmp_path / "private" / "research.json").absolute()
    assert write_research_agent_service_config(research_path, research)
    config = KrLoopAutomationConfig(
        repository=repository,
        output_root=(tmp_path / "output").absolute(),
        research_agent_config=research_path,
        active_release=(tmp_path / "private" / "active-release.json").absolute(),
        launch_agents_directory=(tmp_path / "LaunchAgents").absolute(),
        uv_path=Path(shutil.which("uv") or "/bin/false").resolve(),
        grok_binary=Path("/usr/bin/false"),
    )
    config_path = (tmp_path / "private" / "loop-automation.json").absolute()
    assert write_kr_loop_automation_config(config_path, config)
    return config, config_path


def head(repository: Path) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    ).stdout.strip()


def main_head(repository: Path) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "refs/heads/main"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def append_current_calendar(path: Path, base_date: dt.date) -> None:
    rows = tuple(
        calendar_row((base_date + dt.timedelta(days=offset)).strftime("%Y%m%d"), "Y", "Y", "Y", "Y")
        for offset in range(3)
    )
    receipt = KisKrSessionCalendarReceipt(
        base_date=base_date,
        received_at=dt.datetime.combine(base_date, dt.time(8), tzinfo=KST),
        status_code=200,
        content_type="application/json",
        raw_payload=calendar_payload(rows=rows),
    )
    assert KisKrSessionCalendarStore(path).append(receipt, project_kis_kr_session_calendar(receipt))
