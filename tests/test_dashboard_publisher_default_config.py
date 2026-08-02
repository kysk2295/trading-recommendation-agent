from pathlib import Path

import run_dashboard_publisher


def test_dashboard_publisher_default_targets_schema_v2_runtime_config() -> None:
    # Given: the publisher's configured research runtime default
    configured_default = run_dashboard_publisher.DEFAULT_RESEARCH_AGENT_CONFIG

    # When: the default path is resolved as the CLI would receive it
    resolved_default = Path(configured_default)

    # Then: the publisher selects the canonical strict schema-v2 runtime file
    assert resolved_default.name == "research-agent-runtime-v2.json"
