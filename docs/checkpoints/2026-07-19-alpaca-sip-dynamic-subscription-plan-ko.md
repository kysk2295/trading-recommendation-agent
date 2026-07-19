# Alpaca SIP dynamic subscription plan 체크포인트

## 계약

- M4.2 `SubscriptionPolicyDecision`이 READY이고 desired set이 비어 있지 않을 때만 plan을 만든다.
- 모든 desired item은 exact `(quote, trade)` channel과 중복 없는 instrument-symbol binding을 가진다.
- plan ID는 policy replay identity, semantic version, evaluated time, New York market date와 ordered binding을 결합한다.
- fresh connection request는 `subscribe`와 exact `quotes`, `trades` 목록만 포함한다.
- subscription ACK는 trades, quotes, automatic corrections, cancelErrors가 같은 exact symbol 집합이어야 한다.
- provider가 list 순서를 보장하지 않으므로 ACK 순서는 무시하되 missing, extra, duplicate와 partial channel은 차단한다.
- bars, updated/daily bars, statuses와 LULD subscription은 모두 비어 있어야 한다.

## 검증

- 2-symbol policy → deterministic request와 content ID
- 같은 ACK 집합의 순서 변경 허용
- missing, extra, duplicate symbol과 quote 누락 차단
- provider 405 error와 closed-session policy 차단
- focused 22 passed, full 2402 passed
- Ruff, basedpyright 0/0, external WebSocket·credential·account/order 호출 0건

## 다음 단계

- plan-bound raw-first multi-symbol control frame·data receipt store
- 한 active connection의 subscription ownership과 symbol별 projection
- fixture reconnect/gap recovery 뒤 열린 NYSE 정규장 bounded read-only smoke
