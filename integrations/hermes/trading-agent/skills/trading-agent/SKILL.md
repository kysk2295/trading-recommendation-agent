---
name: trading-agent
description: Query separate multi-market trading-agent opinions and owner-visible Paper operating status.
---

# Trading Agent

Use `trading_agent_query` when the owner asks about a US ticker or six-digit Korean stock code.
Present every returned agent opinion separately. Never synthesize a blended verdict, hide a blocked opinion,
or describe missing evidence as neutral agreement.

Use `trading_agent_status` for delivery and gateway readiness. A recommendation is research or Paper
forward-validation evidence, not guaranteed profit and not an order authorization.
Automatic Telegram delivery uses the configured Hermes home channel and the dedicated launchd-managed
single-worker service. `delivery_worker_status=external_running` confirms that its process-lifetime lease is held.
Delivery remains at-least-once: a process exit between Telegram acceptance and the local acknowledgement can
produce a duplicate alert.

The arm tools are owner-session-bound controls for Alpaca Paper only. They never authorize live-money
trading, KIS or LS mutations, risk-limit expansion, or discretionary LLM order placement. Do not ask the
owner for broker URLs, API keys, account identifiers, or credential values.
