# Alpaca SIP dynamic quote actionability 체크포인트

## 완료 계약

- adapter는 검증된 `AlpacaSipDynamicFeatureBundle`만 입력으로 받고 raw quote나 KIS snapshot을 받지 않는다.
- 평가시각은 호출자 입력이 아니라 bundle quote confirmation의 exact `observed_at`으로 고정한다.
- provider-neutral quote ID와 source reference는 bundle ID에 결합하며 namespace는 `quote/alpaca-sip-dynamic-bundle`이다.
- decision이 원본 bundle을 보존하므로 exact research identity, dynamic plan, complete connection epoch, instrument/symbol, bid/ask venue와 quote/trade confirmation을 다시 검증할 수 있다.
- 기존 공통 freshness 5초, spread 25bp, stop, entry slippage 20bp와 waiting/trigger policy를 그대로 사용한다.
- derived signal은 별도 `current_quote_validated` observation이며 KIS provider 또는 `quote/snapshot` evidence를 만들지 않는다.
- artifact matcher는 같은 base, bundle과 scan cycle로 전체 decision을 deterministic 재생해 변조를 차단한다.

## 검증

- focused adapter: **6 passed**
- dynamic SIP + KIS actionability/outbox related: **84 passed**
- full suite: **2493 passed**
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- provider·credential·network·account/order endpoint 호출과 mutation 0건

## 남은 경계

- decision과 bundle의 append-only durable publication 경계는 아직 없다.
- 기존 KIS `us-quote-snapshots.v2.jsonl`에 Alpaca evidence를 기록하지 않는다.
- 다음 checkpoint는 provider-specific bundle record, assessment와 derived signal을 한 batch로 재검증한 뒤 append한다.
- current-quote-validated signal은 Paper 주문 권한이 아니며 explicit arm/account/risk/session gate를 우회하지 않는다.
