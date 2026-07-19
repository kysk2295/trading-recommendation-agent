# US dynamic plan epoch 체크포인트

## 완료 계약

- dynamic subscription plan을 매 minute policy decision에서 직접 다시 만들지 않는다.
- 첫 READY policy state는 plan epoch 하나를 만들고, 같은 뉴욕 거래일에 instrument/symbol active topology가 유지되면 exact prior plan을 재사용한다.
- active topology 또는 뉴욕 거래일이 바뀔 때만 새 plan ID와 `evaluated_at`을 append한다.
- mode-600 private SQLite store는 canonical Pydantic payload와 SHA-256을 보존하고 UPDATE/DELETE, symlink, public mode, hard-link, metadata/payload 변조를 fail-closed한다.
- runtime fleet cycle과 bounded supervisor는 signal outbox/actionability manifest가 활성화될 때 이 durable plan을 roll한 뒤 exact READY binding manifest를 만든다.
- `--dynamic-plan-store`는 optional explicit 경로다. 생략하면 policy-state 파일 옆 `<stem>.dynamic-plans.sqlite3`을 사용하고 단독 지정은 provider/state write 전에 차단한다.

## 인과 순서

1. 첫 runtime minute가 active plan을 append한다.
2. 별도 read-only stream owner가 그 plan을 읽어 quote/trade raw receipt를 누적한다.
3. 다음 runtime minute는 topology가 같으면 같은 plan을 manifest에 보존한다.
4. projector는 snapshot `observed_at` 이전의 동일 plan·complete epoch receipt만 사용한다.

manifest 생성 뒤 처음 연결한 receipt를 같은 과거 snapshot에 억지로 투영하지 않는다.

## 검증

- plan roll/store unit: 17 passed
- cycle/supervisor/dynamic-plan integration: 32 passed
- two-minute fixture: provider GET 22, manifest 2, dynamic plan row 1
- full suite: 2525 passed
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- repository-wide format check는 기존 180개 파일의 unrelated format drift를 보고했으며 이 checkpoint에서 일괄 변경하지 않았다.
- external WebSocket, account/order endpoint, broker mutation: 0건

## 다음 단계

query-only active plan reader와 bounded reconnect owner CLI를 연결한다. 현재 일요일에는 fixture transport만 사용하고, 실제 SIP WebSocket은 열린 NYSE 정규장·explicit read-only arm·mode-600 market-data credential이 동시에 맞을 때만 별도 smoke로 실행한다.
