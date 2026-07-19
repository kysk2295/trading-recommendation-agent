# Alpaca SIP dynamic feature confirmation 체크포인트

## 완료 계약

- 입력 snapshot은 completed-minute kernel의 `READY` 결과이며 exact research identity, instrument, profile과 마지막 완료 봉 시각을 유지한다.
- dynamic trade history는 snapshot과 같은 `as_of`, New York market date와 instrument에 결합된 terminal-observed single epoch여야 한다.
- active trade가 여러 개면 event time, receipt time, source sequence, frame-local index와 event ID 순으로 최신 항목을 선택한다.
- trade event와 receipt는 마지막 완료 봉보다 빠를 수 없고 receipt는 관측시점보다 미래이거나 2분 초과로 오래될 수 없다.
- immutable confirmation은 snapshot identity, dynamic plan/epoch, exact trade event/source order, 체결가, VWAP와 basis-point 관계를 deterministic ID에 고정한다.
- multi-epoch, incomplete history, blocked snapshot, instrument/profile mismatch와 canceled-only state는 feature 사용 전에 fail-closed한다.
- bridge는 지표를 재계산하거나 research claim, 추천, 전략 승격 또는 주문을 만들지 않는다. canonical minute dataset 재검증과 breakout/RVOL extraction은 기존 typed feature extractor가 담당한다.

## 검증

- focused state/history/bridge: **24 passed**
- dynamic SIP + intraday kernel + typed extractor related: **105 passed**
- full suite: **2470 passed**
- Ruff, basedpyright 0 errors/0 warnings, compileall과 no-excuse rules 통과
- local library QA: single epoch의 trade `103`, source order `4:1`, complete confirmation과 VWAP 관계 확인
- local library QA: two-epoch history는 `complete_history=false`로 public bridge에서 차단
- fixture transport만 사용했으며 provider network, credential file, account/order endpoint와 mutation은 0건

## 남은 경계

- 열린 NYSE 정규장에서 명시적 arm과 private market-data credential 아래 bounded read-only dynamic SIP smoke
- 실제 provider reconnect가 관측될 때 backfill 또는 sequence continuity evidence 계약 확인
- dynamic quote state와 completed-minute snapshot의 별도 causal confirmation 계약
- 충분한 forward evidence 전에는 이 confirmation을 수익성, 추천 또는 Paper 주문 근거로 표현하지 않는다.
