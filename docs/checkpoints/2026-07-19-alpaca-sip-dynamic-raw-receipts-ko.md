# Alpaca SIP dynamic raw receipt 체크포인트

## 완료 계약

- 별도 private SQLite 파일에 한 writer만 접근하며 connection epoch를 exact dynamic plan에 먼저 귀속한다.
- binding은 plan ID, policy identity/version, New York market date와 ordered instrument-symbol 목록을 고정한다.
- control/data payload는 의미 해석 전에 원문 bytes, UTC 수신 시각, 연속 sequence와 payload hash로 저장한다.
- receipt ID는 plan ID, epoch, sequence, 수신 시각, kind와 payload hash를 결합한다.
- 같은 sequence의 exact retry만 idempotent하며 conflict와 gap은 차단한다.
- 미등록 epoch, 다른 plan, bind 이전 시각의 frame은 저장하지 않는다.
- replay는 exact schema, mode 600, current owner, regular file, single hardlink, binding/hash/sequence를 다시 검증한다.

## 검증

- focused 10 passed, full 2412 passed
- Ruff, basedpyright 0/0, compile과 no-excuse rules 통과
- fixture library QA에서 control/data 2건 재생과 gap·unbound 차단 확인
- external WebSocket·credential·account/order 요청 0건

## 남은 경계

- 한 active Alpaca SIP connection owner가 plan을 bind하고 raw frame을 store에 전달
- 저장된 payload의 strict control validation과 symbol별 quote/trade projection
- reconnect epoch, gap recovery와 bounded terminal attestation
- 열린 NYSE 정규장 read-only bounded smoke
