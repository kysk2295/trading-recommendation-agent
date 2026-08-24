# Post-completion verification

Run after commit `b7deb4f8842bd0f6ff58bb5faabe1a31c899e6c3` on 2026-08-24.

```text
$ uv run pytest -q tests/test_hermes_delivery_e2e.py tests/test_hermes_plugin_delivery.py
........................                                                 [100%]
24 passed in 0.65s

$ uv run ruff check trading_agent/kr_day_decision_delivery.py trading_agent/kr_day_decision_delivery_identity.py trading_agent/kr_day_decision_delivery_records.py trading_agent/kr_day_decision_delivery_rendering.py tests/test_hermes_delivery_e2e.py
All checks passed!

$ uv run basedpyright trading_agent/kr_day_decision_delivery.py trading_agent/kr_day_decision_delivery_identity.py trading_agent/kr_day_decision_delivery_records.py trading_agent/kr_day_decision_delivery_rendering.py tests/test_hermes_delivery_e2e.py
0 errors, 0 warnings, 0 notes

$ uv run python -c '<open manual_projection.sqlite3 through HermesDeliveryStore>'
projection: inserted artifact has actionable->exit and truthful no-fill text

$ uv run python -c '<open manual_invalid_projection.sqlite3 through HermesDeliveryStore>'
contradiction artifact: persisted_events=0
```

Judgment: the durable projection artifact contains the truthful no-fill `ACTIONABLE -> EXIT`
thread, and the contradictory `BLOCKED + ACTIVE` artifact contains no persisted Hermes event.
