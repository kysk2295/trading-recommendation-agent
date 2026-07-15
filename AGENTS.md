# Trading Recommendation Agent Rules

## Product Boundary

- This project produces research results, paper recommendations, and Alpaca paper-only order execution.
- Alpaca trading calls must use the exact `https://paper-api.alpaca.markets` base URL. Reject every other trading URL before any network request.
- Never add or enable Alpaca live-trading endpoints, live credentials, or real-money order paths.
- KIS and every provider other than Alpaca Paper Trading remain read-only. Do not call their order, balance, account, or position-changing endpoints.
- LS adapters may use only explicitly reviewed market-data and news contracts. Never call `/stock/accno`, `/stock/order`, WebSocket account registration types `1/2`, or any LS trading mutation.
- Alpaca paper account/order/position reads and paper order submission, cancellation, and same-day flattening are allowed only when the paper endpoint guard and risk kernel pass.
- Never claim profitability from synthetic, replay, or backtest output.
- A recommendation requires a timestamp, entry, stop, targets, rationale, and immutable outcome history.

## Market-Time Safety

- New live recommendations may use only the latest completed bar from the current New York trading session.
- Historical bars may warm indicators but must never create backdated recommendations.
- Block recommendations when the session is closed, the feed is stale, the spread is missing, or the data date is not current.
- Same-bar stop and target collisions resolve to the stop.

## Secrets

- Never store credentials or tokens in this project.
- Read KIS credentials only from `~/.config/trading-agent/kis.env` with mode `600`.
- Read OpenDART credentials only from `~/.config/trading-agent/opendart.env` as one `OPENDART_API_KEY` setting in a current-user-owned regular file with exact mode `600`.
- Read LS credentials only from `~/.config/trading-agent/ls.env` as exactly one `LS_APP_KEY` and one `LS_APP_SECRET` setting in a current-user-owned regular file with exact mode `600`.
- Read Alpaca market-data credentials from `~/.config/trading-agent/alpaca.env` and paper-execution credentials from `~/.config/trading-agent/alpaca-paper.env`; both files must have mode `600`.
- Read cached tokens only from `~/.cache/trading-agent/` with mode `600`.
- Never print request headers, API keys, secrets, tokens, account identifiers, or raw authentication responses.
- Treat credentials pasted into chat, issues, documents, or logs as compromised. Never use them; revoke and rotate them before any provider smoke test.

## Memory And Concurrency

- Never run more than two subagents concurrently.
- Subagents must not spawn child agents.
- This project must not load `data/regend_us_stocks` or run full-universe backtests.
- Only one heavy empirical process may run at a time, with a 10 GiB RSS stop threshold.
- Close reviewers immediately after collecting their result.

## Verification

- Run targeted tests, Ruff, and basedpyright for changed Python files.
- Manually run CLI help, one bad input, and the relevant happy path.
- For paper execution changes, prove the live endpoint is rejected before HTTP and reconcile open orders/positions after any real paper smoke test.
- Keep failed recommendations and status transitions in the audit database.
