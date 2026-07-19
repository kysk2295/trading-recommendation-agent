# Alpaca SIP dynamic microstructure feature bundle 체크포인트

## 완료 계약

- 입력 trade와 quote confirmation은 각각 complete single-epoch history와 동일 READY completed-minute snapshot을 독립 검증한다.
- bundle은 research input identity, dynamic plan ID, connection epoch, market date, instrument/symbol, observed time, 마지막 완료 봉과 VWAP가 모두 같을 때만 생성된다.
- 서로 독립적으로 complete여도 connection epoch가 다르면 fail-closed한다.
- immutable bundle ID는 trade confirmation ID, quote confirmation ID, last-trade-vs-midpoint bps와 displayed bid/ask 내부 여부를 고정한다.
- quote 밖 trade는 관측 feature로 유지하지만 current-entry actionability, signal publication 또는 주문 근거로 승격하지 않는다.
- 이 결합은 동일 provider session의 신규 데이터 evidence 계약이며 서로 다른 strategy lane 결과의 사후 혼합이 아니다.
- bundle은 recommendation, lifecycle promotion, Paper intent 또는 주문을 만들지 않는다.

## 검증

- focused bundle: **4 passed**
- dynamic SIP + intraday typed feature related: **119 passed**
- full suite: **2484 passed**
- Ruff, basedpyright 0 errors/0 warnings, compileall과 no-excuse rules 통과
- local library QA: same plan/epoch, complete trade/quote, midpoint distance `0`, inside quote `true` 확인
- actionability policy 적용 0건, signal publication 0건, network request와 account/order mutation 0건

## 남은 경계

- 698 pure LOC의 KIS-specific actionability 모듈을 provider-neutral policy kernel과 KIS/Alpaca SIP adapter로 분리한다.
- Alpaca adapter는 SIP confirmation ID와 bid/ask venue lineage를 KIS provider 또는 단일 exchange로 위조하지 않는다.
- 기존 conditional signal을 수정하지 않고 별도 terminal assessment와 derived current-quote signal을 append-only로 만든다.
- actionability와 signal이 완성돼도 Paper order authority는 별도 arm·risk·account gate 뒤에 유지한다.
