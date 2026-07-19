# Alpaca SIP dynamic symbol projection 체크포인트

## 완료 계약

- projection은 caller가 만든 tuple이 아니라 private store와 plan/epoch를 받아 verified replay를 다시 읽는다.
- connected, authenticated, exact multi-symbol ACK control 3건을 projection 전에 재검증한다.
- data frame은 strict quote, trade, correction, cancel wire message만 허용한다.
- 각 symbol은 exact plan의 instrument ID에만 귀속된다.
- event time은 receipt 수신 시각보다 늦을 수 없고 plan의 New York market date와 같아야 한다.
- immutable message ID는 raw receipt ID, frame-local index와 canonical content hash에 결합된다.
- unbound symbol, future/wrong-date event와 control-in-data는 fail-closed한다.

## 검증

- focused 6 passed, related dynamic 30 passed, full 2424 passed
- Ruff, basedpyright 0/0, compile과 no-excuse rules 통과
- fixture library QA에서 BBB quote와 AAA trade의 exact instrument binding 확인
- provider·credential·account/order 요청 0건

## 남은 경계

- original/correction/cancel chain의 active-state canonicalization
- success/failure terminal record와 restart-safe bounded attestation
- reconnect epoch와 gap recovery fixture soak
- 열린 NYSE 정규장 bounded read-only smoke
