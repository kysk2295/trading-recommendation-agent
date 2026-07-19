# US runtime live actionability dispatch 체크포인트

## 완료 범위

- fleet cycle이 방금 만든 current actionability manifest를 같은 process에서 bounded Alpaca SIP quote/trade lifecycle로 전달한다.
- supervisor는 `--arm-live-actionability`, `--live-actionability-receipt-root`, `--live-actionability-store`를 매 cycle에 전달한다.
- 세 옵션과 기존 conditional outbox/manifest/dynamic plan 계약은 all-or-none이다. 부분 설정은 policy state, credential과 provider 접근 전에 차단한다.
- manifest root 전체를 먼저 검증하고 exact cycle 시각의 manifest만 instrument 순서로 실행한다.
- 종목마다 manifest digest 기반 mode-600 receipt SQLite를 사용하고 root는 mode 700, actionability output은 기존 append-only single writer store 하나를 사용한다.

## 인과성·재시작

- 과거 manifest는 연결하지 않으며 선택 0개이면 receipt root도 만들지 않는다.
- public/malformed/digest mismatch/중복 instrument manifest는 batch 실행 전에 차단한다.
- complete terminal이 있는 exact retry는 WebSocket connector 0건, actionability append replay로 끝난다.
- raw control/data와 terminal은 projection보다 먼저 보존되고 terminal 시각이 original READY snapshot과 같은 completed-minute일 때만 feature를 재관측한다.

## 검증

- current manifest fixture: WebSocket 1회, control/auth/ACK와 quote/trade data 10 frame, terminal complete, actionability append 1
- exact retry fixture: WebSocket 0회, actionability append 0/replay 1
- supervisor one-cycle fixture: historical/current GET 21, live child selected/new `1/1`, account/order mutation 0
- partial 옵션, stale manifest와 public manifest: provider/output/receipt 생성 전 block
- 전체 `2545 passed`
- Ruff 통과
- basedpyright `0 errors, 0 warnings`
- compileall 및 changed-file no-excuse 통과

## 남은 운영 검증

- 현재 체크포인트는 fixture transport까지다. 실제 Alpaca SIP WebSocket과 account/order 호출은 0건이다.
- 열린 NYSE 정규장, private market-data credential과 SIP entitlement가 모두 자연스럽게 맞을 때만 explicit arm으로 bounded read-only smoke를 실행한다.
- 다음 계약은 live child 결과를 supervisor attempt 감사 원장에 별도 결합하는 것이다.
