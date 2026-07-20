# 세 번째 정규장 Alpaca Paper smoke 시도 체크포인트

실행 시각: 2026-07-20 09:43~09:51 EDT
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결과

`feature/paper-account-activities`의 remote 최신 `c487f0a`에서 단계 1을 세 번째로 검증했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

전체 회귀와 정적 검사를 먼저 통과한 뒤 실제 Paper bootstrap과 preflight를 단일 Writer로 순차 실행했다. 두 GET-only 단계는 기존 account binding, open order 0, position 0을 확인했다. 이어진 readiness의 exact Paper 주문 WSS 연결이 `PaperOrderStreamUnavailableError`로 실패했고, 별도 GET-only recovery도 같은 이유로 실패했다.

런북은 readiness nonzero를 즉시 중단 조건으로 두므로 WSS를 반복 재시도하거나 후보·과거 주문으로 우회하지 않았다. exact current ORB 후보 감사와 armed entry를 시작하지 않았고, entry·보호 OCO·exact-ID cancel·exact-quantity flatten mutation은 모두 0건이다.

마지막 REST GET preflight는 다시 open order 0, position 0, account binding ready를 확인했다. 실행 원장은 schema v9, `PRAGMA quick_check=ok`, unresolved mutation 0이고 order/mutation/broker event 행도 0이다. 따라서 broker REST 기준 최종 flat은 맞지만 WSS 최종 대사는 미완료이므로 실제 최소수량 smoke 자체는 PASS가 아니다.

## 검증

- 전체 회귀: 946 passed
- Ruff: PASS
- basedpyright: 0 errors, 0 warnings
- CLI help 4종과 invalid arm: PASS
- fake broker entry→보호 OCO→exact cancel→exact flatten E2E: PASS
- 실제 Paper bootstrap·preflight·final preflight: PASS
- 실제 Paper readiness·recovery: `PaperOrderStreamUnavailableError`, fail-closed
- 실제 network: Paper REST GET + exact Paper WSS 시도만 사용
- broker mutation: POST/PATCH/DELETE 0건
- 전체 실행 최대 RSS: 641,204,224 bytes
- broker CLI 최대 RSS: 74,055,680 bytes
- swap: 0
- 운영 보고서: mode 600

## 다음 단계

다음 열린 정규장에서 exact Paper WSS readiness가 정상 통과할 때 단계 1만 다시 단발 시도한다. actual entry ACK·체결, 부분체결부터 exact 보호 OCO, exact-ID cancel, exact-quantity flatten과 최종 REST/WSS flat이 모두 확인되기 전에는 ORB 반복 pilot을 시작하지 않는다.

이 결과는 안전한 중단 증거이며 수익성이나 전략 승격 근거가 아니다.
