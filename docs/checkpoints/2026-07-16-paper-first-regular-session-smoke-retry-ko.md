# 두 번째 정규장 Alpaca Paper smoke 시도 체크포인트

실행 시각: 2026-07-16 09:42~09:48 EDT
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결과

`feature/paper-account-activities`의 remote 최신 `b0abe01`에서 단계 1을 다시 검증했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

실제 Paper bootstrap, preflight, readiness, recovery를 단일 Writer·순차 WSS로 실행했다. broker는 정규장 open이었고 exact Paper WSS 인증·구독·Pong과 current-epoch REST·원장·포트폴리오 대사가 통과했다. 시작 계좌는 open order 0, position 0이고 기존 account binding은 exact 일치했다.

오늘 watch SQLite에는 추천 1건이 있었지만 exact current ORB `setup` 후보는 0건이었다. 런북은 후보가 정확히 1건이 아니면 entry를 실행하지 않으므로 exact arm을 호출하지 않았고, 과거 후보나 임의 종목·가격·수량으로 우회하지 않았다.

따라서 entry, 보호 OCO, exact-ID cancel, exact-quantity flatten mutation은 모두 0건이다. targeted mutation recovery, 최종 state recovery와 preflight는 open order 0, position 0, unresolved mutation 0, schema v9, `PRAGMA quick_check=ok`를 확인했다. 최종 flat은 PASS지만 실제 최소수량 smoke 자체는 PASS가 아니다.

## 검증

- 전체 회귀: 946 passed
- Ruff: PASS
- basedpyright: 0 errors, 0 warnings
- CLI help 4종과 invalid arm: PASS
- 실제 Paper bootstrap·preflight·readiness·recovery: PASS
- 실제 network: Paper REST GET + exact Paper WSS only
- broker mutation: POST/PATCH/DELETE 0건
- 전체 실행 최대 RSS: 639,942,656 bytes
- broker CLI 최대 RSS: 62,914,560 bytes
- swap: 0
- 운영 보고서: mode 600

## 다음 단계

다음 열린 정규장에서 30초 이내 exact current ORB setup 후보가 정확히 1건일 때 단계 1을 다시 단발 시도한다. 실제 entry ACK·체결, 부분체결부터 exact 보호 OCO, exact-ID cancel, exact-quantity flatten과 최종 flat이 모두 확인되기 전에는 ORB 반복 pilot을 시작하지 않는다.

이 결과는 Paper 안전 실행 증거이며 수익성이나 전략 승격 근거가 아니다.
