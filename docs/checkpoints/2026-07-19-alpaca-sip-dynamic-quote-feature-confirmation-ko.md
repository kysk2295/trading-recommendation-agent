# Alpaca SIP dynamic quote feature confirmation 체크포인트

## 완료 계약

- trade와 quote history는 하나의 shared terminal·epoch·raw receipt coverage verifier를 사용한다.
- quote state는 전체 projected payload hash·wire type·symbol/time·epoch order를 검증하고 requested `as_of`에 관측된 종목별 최신 quote만 materialize한다.
- latest quote 순서는 provider event time, receipt time, source sequence, frame-local index와 immutable event ID로 결정한다.
- 입력 snapshot은 completed-minute kernel의 `READY` 결과이며 quote history와 exact as-of, New York market date, instrument/profile binding이 같아야 한다.
- quote event와 receipt는 마지막 완료 봉 이후여야 하고 provider event age는 5초 미만이어야 한다.
- crossed quote와 bid/ask displayed size 합계 0은 confirmation 전에 fail-closed한다.
- immutable confirmation은 bid/ask, displayed size, midpoint, size-weighted microprice, order-book imbalance, spread bps, VWAP 관계, source order와 quote expiry를 deterministic ID에 고정한다.
- wide spread는 관측 feature로 보존하지만 current-entry actionability가 아니다. 25bp 한도, setup invalidation, entry slippage와 signal publication은 기존 actionability kernel이 별도로 판단한다.
- bridge는 recommendation, lifecycle promotion, Paper intent 또는 주문을 만들지 않는다.

## 검증

- focused quote/trade history, projection와 feature bridge: **31 passed**
- dynamic SIP + intraday/actionability/signal related: **148 passed**
- full suite: **2480 passed**
- Ruff, basedpyright 0 errors/0 warnings, compileall과 no-excuse rules 통과
- local library QA: single complete epoch quote의 source order `4:1`, midpoint `100.02`, microprice `100.025`, imbalance `0.5` 확인
- local library QA: two-epoch quote history는 `complete_history=false`로 public bridge에서 차단
- actionability policy 적용 0건, fixture transport 밖 network request와 account/order mutation 0건

## 남은 경계

- quote confirmation을 기존 immutable conditional signal의 별도 Alpaca SIP actionability assessment adapter에 연결
- 열린 NYSE 정규장에서 명시적 arm과 private market-data credential 아래 bounded read-only dynamic SIP quote/trade smoke
- 실제 provider reconnect 관측 시 backfill 또는 sequence continuity evidence 확인
- 충분한 forward evidence 전에는 quote confirmation을 체결 가능성, 수익성 또는 주문 권한으로 표현하지 않는다.
