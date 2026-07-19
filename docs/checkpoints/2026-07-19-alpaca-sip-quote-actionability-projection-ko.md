# Alpaca SIP quote actionability projector 체크포인트

## 완료 계약

- 하나의 query-only projector가 READY intraday snapshot의 `observed_at`을 as-of로 사용해 stored dynamic trade history와 quote history를 각각 materialize한다.
- 두 history는 기존 complete-history gate를 통과한 뒤 같은 snapshot의 immutable microstructure bundle로 결합된다.
- projector는 bundle을 공통 actionability policy로 평가하고 self-verifying private store append를 마지막 단계에서만 실행한다.
- exact replay는 같은 decision을 반환하고 append count 0이다.
- multi-epoch continuity gap, terminal 미관측, snapshot/plan/instrument mismatch 또는 invalid policy input은 typed block으로 닫히며 output DB를 만들지 않는다.
- projector는 provider, credential, network, account 또는 order API를 열지 않는다.

## 검증

- projector focused: **3 passed**
- dynamic SIP actionability related: **44 passed**
- full suite: **2502 passed**
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- manual library QA: first append 1, exact replay 0, record 1, complete trade/quote, `validated_waiting`, `current_quote_validated` 확인
- provider·credential·network·account/order endpoint 호출과 mutation 0건

## 남은 경계

- projector API는 구현됐지만 runtime fleet/dynamic connection owner 또는 운영 CLI가 아직 자동 호출하지 않는다.
- current conditional signal, exact READY snapshot, dynamic plan과 receipt store의 운영 binding을 durable input manifest로 고정해야 한다.
- 별도 bounded operational entrypoint는 input manifest를 재검증하고 closed/stale/incomplete 조건에서 write 0을 보장해야 한다.
- actionability publication은 Telegram delivery나 Paper order intent가 아니다.
