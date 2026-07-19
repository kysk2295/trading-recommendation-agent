# KR theme day shadow signal 체크포인트

## 완료 범위

- `kr_equities/day_trading/theme_leader_vwap_reclaim` 전략 lane identity를 추가했다.
- `KrThemeDaySetup`은 exact Opportunity ID, KR symbol, producer strategy version, setup 관측/만료시각, 손절·목표, 최대 slippage와 immutable evidence를 보존한다.
- pure projector는 KR theme Opportunity의 rank-1 대장주, setup, `KrMarketConstraintSnapshot` symbol과 시각 계보를 정확히 대사한다.
- 기존 KR gate의 session, VI, 단일가, halt, designation, 가격제한, 5초 quote 판정을 재사용한다.
- gate가 막히면 exact reason과 signal `None`을 반환한다. gate가 열려도 quote spread가 setup의 최대 slippage를 넘으면 signal을 만들지 않는다.
- 모든 조건이 맞으면 현재 ask를 entry로 사용하고 setup의 stop/targets, Opportunity/setup/market evidence와 typed `QuoteValidation`을 가진 `CURRENT_QUOTE_VALIDATED` `TradeSignalEnvelope`를 만든다.
- signal ID는 Opportunity, setup, market observation과 evaluation time에 content-addressed하게 결합된다.

## 경계

- setup은 아직 실시간 분봉에서 추출하지 않는다. 이번 단계는 임의 Opportunity만으로 VWAP reclaim을 추정하지 않는다.
- LS/KIS quote·VI·status provider adapter, KRX calendar, append-only multi-market trial, shadow fill과 terminal outcome은 아직 없다.
- 이 신호는 shadow 연구 계약이며 국내 계좌·잔고·포지션·주문 권한을 만들지 않는다.
- provider, credential, network, broker mutation은 0건이다.
- fixture signal은 정확도나 수익성 증거가 아니다.

## 검증

- focused signal/gate/contract: `28 passed`
- 전체 회귀: `2633 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, changed-file format, no-excuse: 통과
- 최소 driver: eligible `signal=1`, current quote validation `1`, VI active `signal=0/reason=vi_active`, external mutation `0`

## 다음 단계

완료된 KR 1분봉과 point-in-time VWAP에서 `KrThemeDaySetup`을 만드는 deterministic extractor를 추가한다. 이후 read-only LS/KIS current market adapter와 global multi-market trial schema를 연결하고, 보수적 KR shadow fill을 별도 append-only 원장에 기록한다.
