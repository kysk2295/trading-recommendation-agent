# Alpaca SIP dynamic reconnect trade history 체크포인트

## 완료 계약

- plan별 terminal history와 각 epoch의 exact receipt ownership을 private store에서 다시 검증한다.
- failed epoch의 original 뒤 complete epoch의 correction/cancel도 진단용 active state로 재생한다.
- epoch 경계가 하나라도 있으면 provider backfill 증거가 없으므로 `continuity_unattested`와 `complete_history=false`로 닫는다.
- feature 입력은 `require_complete_alpaca_sip_dynamic_trade_history()`를 통과해야 하며 multi-epoch history는 거부한다.
- 단일 `BOUNDED_COMPLETE` epoch도 requested `as_of`에 terminal이 이미 관측됐을 때만 bounded complete-history로 승인한다.
- exact duplicate payload는 duplicate count만 증가시키고 active state에는 한 번만 반영한다.
- 같은 provider trade ID의 conflicting payload, epoch receipt-time overlap, complete 뒤 epoch와 10회 초과 history는 fail-closed한다.
- data가 없는 failed control-only epoch도 terminal history와 gap count에는 보존한다.

## 검증

- focused state/history 17 passed, reconnect-related 29 passed, full 2463 passed
- Ruff, basedpyright 0/0, compileall과 no-excuse rules 통과
- failed→complete correction/cancel, exact/conflicting duplicate, overlap, single complete의 terminal 전후 as-of, control-only failure와 post-complete epoch fixture 통과
- local library QA에서 2 epoch, gap 1, duplicate 1, active 1, complete-history false와 gate 거부를 확인했다.
- provider·credential file·account/order 요청 0건

## 남은 경계

- 열린 NYSE 정규장 bounded read-only dynamic SIP smoke
- provider가 제공하는 reconnect backfill 또는 sequence continuity evidence 계약

`complete dynamic state → feature confirmation` 연결은
`2026-07-19-alpaca-sip-dynamic-feature-confirmation-ko.md`에서 완료했다.
