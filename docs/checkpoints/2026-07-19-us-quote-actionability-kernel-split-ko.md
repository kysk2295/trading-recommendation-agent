# US quote actionability kernel 분리 체크포인트

## 완료 계약

- 기존 `trading_agent.us_quote_actionability` 공개 facade와 schema v2 KIS 모델은 그대로 유지한다.
- deterministic identity, frozen models, 정규장·freshness·spread·stop·slippage 규칙, KIS projection, policy orchestration, artifact 재검증을 별도 모듈로 분리했다.
- base current 여부 → 정규장 → provider/quote → future/stale → spread → stop → slippage → waiting/reached의 평가 순서를 바꾸지 않았다.
- quote ID, assessment ID, derived signal ID와 `quote/snapshot` evidence identity 공식도 바꾸지 않았다.
- 새 모듈은 모두 166 pure LOC 이하이며 공개 facade는 36 pure LOC다.

## 검증

- KIS actionability·publication·outbox·scanner focused regression: **66 passed**
- full suite: **2484 passed**
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- provider·credential·network·account·order endpoint 호출과 mutation 0건

## 남은 경계

- 이 체크포인트는 동작 보존 리팩터링이며 KIS schema의 `provider="kis"`와 단일 `exchange` 계약을 Alpaca SIP에 재사용하지 않는다.
- 다음 단계에서 provider-neutral quote evidence와 순수 policy input을 추가하고 KIS projection은 그 입력의 한 adapter가 된다.
- Alpaca SIP adapter는 dynamic quote confirmation과 microstructure bundle의 exact plan, epoch, instrument, bid/ask venue lineage를 보존한다.
- current-quote-validated signal도 주문 권한이 아니다. Paper mutation은 기존 explicit arm, account binding, risk, regular-session gate 뒤에만 남는다.
