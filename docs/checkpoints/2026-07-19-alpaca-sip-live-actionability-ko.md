# Alpaca SIP live actionability lifecycle 체크포인트

## 완료 계약

- explicit `--arm-read-only`와 현재 NYSE 정규장을 credential, manifest, plan/policy/receipt store보다 먼저 검사한다.
- mode-600 manifest의 plan은 latest durable dynamic plan과 exact match해야 하고 latest policy state는 90초 이내이며 같은 topology/date를 유지해야 한다.
- bounded reconnect owner가 control/auth/subscription ACK와 data frame을 raw-first 저장하고 complete terminal을 확정한다.
- terminal은 original READY snapshot과 같은 completed-minute여야 한다. immutable completed-bar feature를 terminal 시각으로 재관측한 뒤에만 quote/trade history와 actionability projector를 실행한다.
- base signal은 terminal 시각에도 current여야 한다. minute rollover, stale state, mismatched plan, quote/trade 불완전과 public credential은 output append 0이다.
- exact restart는 existing complete terminal을 사용해 connector 0건, actionability replay로 끝난다.

## 검증

- re-observation unit + lifecycle + CLI fixture: 12 passed
- owner/reconnect/projector related: 36 passed
- full suite: 2537 passed
- fake wire happy path: connector 1, control 3, data 1, terminal complete, actionability append 1
- restart: connector 0, actionability replay
- next-minute, quote-only, public credential: actionability append 0
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- actual provider WebSocket, account/order/position endpoint, broker mutation: 0건

## 남은 경계

- runtime supervisor는 manifest를 만들지만 live lifecycle을 아직 자동 child-dispatch하지 않는다.
- actual SIP entitlement와 quote/trade 동시 coverage는 열린 NYSE 정규장에서만 별도 bounded smoke로 확인한다.
- 한 epoch의 frame budget 안에 quote와 trade가 모두 없으면 실패 증거를 성공으로 바꾸거나 재연결 epoch를 complete single history로 축소하지 않는다.
