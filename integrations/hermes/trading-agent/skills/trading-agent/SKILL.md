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

The arm tools are owner-session-bound controls for Alpaca Paper only. They never authorize live-money
trading, KIS or LS mutations, risk-limit expansion, or discretionary LLM order placement. Do not ask the
owner for broker URLs, API keys, account identifiers, or credential values.
