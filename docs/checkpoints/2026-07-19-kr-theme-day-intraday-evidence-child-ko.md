# KR theme day 장중 evidence child 체크포인트

## 원문 receipt 권위

`KisKrMarketReceiptStore`는 KIS 당일 분봉, 현재가 상태와 호가 예상체결 raw bytes를 projection 전에 보존할 durable source다. kind, symbol과 실제 수신시각이 logical identity이며 exact retry만 no-op이다. 같은 identity의 다른 payload는 immutable conflict로 차단한다.

SQLite는 현재 사용자 소유 regular file, mode 600, single hard link와 exact schema/index/UPDATE·DELETE trigger를 read/write 때마다 검증한다. payload SHA와 receipt identity도 query-only replay에서 다시 계산한다.

## deterministic 장중 child

`run_kr_theme_day_intraday.py`는 다음 local evidence만 읽는다.

- private append-only KR Opportunity outbox의 exact Opportunity 한 건
- 같은 rank-1 symbol과 KST session의 평가시각 이전 KIS raw receipt
- global experiment ledger의 exact registered·started shadow trial
- 별도 private shadow entry store

child는 기존 kernel로 완료 분봉 projection, 첫 VWAP pullback reclaim setup, 현재 session·VI·halt·designation·limit·5초 quote gate와 spread를 순서대로 평가한다. 조건이 없으면 `no_setup` 또는 `market_blocked`로 끝나며 entry를 만들지 않는다. 조건이 모두 맞으면 ask 기준 고정 20bp adverse slippage entry를 append하고 exact retry는 `entry_replayed`가 된다.

이 단계는 KIS credential이나 network를 열지 않고, account·position·order·arm·quantity·notional을 받지 않는다. report에도 symbol, price, ID, raw payload와 path를 기록하지 않는다.

## 검증

- focused receipt/intraday/CLI: `8 passed`
- related KR market/setup/signal/entry/exit: `35 passed`
- 전체 회귀: `2726 passed`
- Ruff 전체와 changed-file format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, 신규 production no-excuse: 통과
- actual CLI help, missing Opportunity block, fixture happy와 exact replay: 통과
- provider credential/network, 국내 account/order mutation: `0`

## 다음 단계

KIS GET-only collector가 각 응답 직후 이 receipt store를 single Writer로 확정하고, durable KR day session supervisor가 pre-open trial, intraday child·exit, post-session control runner를 공식 calendar와 current KST gate 아래 연결한다. 재시작은 source store를 다시 검증한 뒤 완료된 phase를 exact replay하고 마지막 미완료 phase만 계속한다.
