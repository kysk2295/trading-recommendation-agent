# Task 7 KR delivery terminal-boundary fix

`omo ulw-loop status --json` returned `ULW_LOOP_PLAN_MISSING`; this directory is the
required fallback evidence location.

## Pre-entry censor projection

- Scenario: a valid same-thesis `ARMED -> REGISTERED -> CENSORED` history with no
  active shadow position is projected through the real Hermes SQLite writer.
- Invocation: `uv run pytest -q tests/test_hermes_delivery_e2e.py -k 'preentry_censored_plan or invalidation_or_blocked_plus_bound_active'`
- Binary observable: exit `0`; `3 passed, 9 deselected`.
- Captured artifact: `manual_projection.sqlite3`, created by direct
  `project_kr_day_decision_delivery(...)` invocation.
- Direct observable: `inserted=2`, kinds `('actionable', 'exit')`, and the exit text
  contains `미체결 계획 종료` while omitting `체결가`.

## Contradictory lifecycle rejection

- Scenario: the same thesis contains `ARMED -> BLOCKED` and a bound `ACTIVE` shadow
  event.
- Invocation: direct `project_kr_day_decision_delivery(...)` call against
  `manual_invalid_projection.sqlite3`.
- Binary observable: `InvalidKrDayDecisionDeliveryError` and `persisted_events=0`.
- Captured artifact: `manual_invalid_projection.sqlite3`.

## Regression and static verification

- Scenario: complete Task 7 Hermes delivery surface, including retained valid
  `ARMED -> invalidation` and `ARMED -> ACTIVE -> terminal` histories.
- Invocation: `uv run pytest -q tests/test_hermes_delivery_e2e.py tests/test_hermes_plugin_delivery.py`
- Binary observable: exit `0`; `24 passed in 0.50s`.
- Captured artifact: this README records the direct command output.

- Scenario: changed Python files are linted and type checked.
- Invocation: `uv run ruff check trading_agent/kr_day_decision_delivery.py trading_agent/kr_day_decision_delivery_identity.py trading_agent/kr_day_decision_delivery_records.py trading_agent/kr_day_decision_delivery_rendering.py tests/test_hermes_delivery_e2e.py` and `uv run basedpyright trading_agent/kr_day_decision_delivery.py trading_agent/kr_day_decision_delivery_identity.py trading_agent/kr_day_decision_delivery_records.py trading_agent/kr_day_decision_delivery_rendering.py tests/test_hermes_delivery_e2e.py`.
- Binary observable: Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`.
- Captured artifact: this README records the direct command output.

- Scenario: whitespace and patch shape check.
- Invocation: `git diff --check`.
- Binary observable: exit `0` with no output.
- Captured artifact: this README records the direct command output.
