# Alpaca SIP dynamic connection owner 체크포인트

## 완료 계약

- exact dynamic plan과 connection epoch를 provider connect 전에 raw receipt store에 bind한다.
- store별 mode 600 non-blocking owner lease를 연결 전체 수명 동안 유지한다.
- 두 번째 owner는 connector와 credential send 전에 fail-closed한다.
- canonical SIP URL과 실제 handshake final URL이 모두 exact할 때만 auth frame을 보낸다.
- connected, authenticated, dynamic subscription ACK는 raw control receipt 저장 후 strict parsing한다.
- quote/trade data payload는 설정된 `max_data_frames`만큼만 raw data receipt로 저장한다.
- invalid auth/ACK와 data timeout은 이미 받은 원문을 삭제하거나 성공 evidence로 승격하지 않는다.

## 검증

- focused owner 6 passed, related 24 passed, full 2418 passed
- Ruff, basedpyright 0/0, compile과 no-excuse rules 통과
- fixture QA에서 control 3 + data 2 receipt, exact request, lease contention을 확인
- 실제 WebSocket·credential file·account/order 요청 0건

## 남은 경계

- success/failure terminal record와 restart-safe bounded attestation
- raw data frame의 strict quote/trade/correction/cancel parser
- ordered instrument binding에 따른 symbol별 immutable projection
- reconnect epoch와 gap recovery fixture soak
- 열린 NYSE 정규장 bounded read-only smoke
