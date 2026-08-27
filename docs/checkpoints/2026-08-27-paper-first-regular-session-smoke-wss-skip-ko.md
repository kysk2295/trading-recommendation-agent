# 스물다섯 번째 Alpaca Paper smoke 시도 체크포인트

작성 시각: 2026-08-27 09:46 EDT / 2026-08-27 22:46 KST
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결론

`feature/paper-account-activities`의 local/remote 최신 `e72866c`에서 단계 1을 스물다섯 번째로 시도했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

production 로컬 NYSE 캘린더는 09:43~09:44 EDT를 열린 정규장으로 판정했고 GET-only bootstrap과 preflight는 기존 account binding, open order 0, position 0으로 통과했다. 그러나 주문 WSS readiness와 한 번의 targeted GET/WSS-only mutation recovery가 모두 `PaperOrderStreamUnavailableError`로 종료됐다. WSS 선행 실패로 broker clock은 관측하지 못했고, 첫 nonzero 게이트에서 후보 감사와 모든 armed CLI를 중단했다.

## 검증과 최종 상태

- 전체 회귀 946개, Ruff, basedpyright 0 errors/warnings를 통과했다.
- CLI help 4종, invalid arm 3종의 무생성 계약과 fake broker 전 수명주기 E2E를 통과했다.
- production source의 live Alpaca endpoint literal은 0건이다.
- 코드 변경은 없었다.
- final REST preflight는 open order 0, position 0으로 종료코드 0이었다.
- 원장은 schema v9, `PRAGMA quick_check=ok`, unresolved mutation 0이었다.
- order intent, broker order event, FILL, mutation intent/event, trade update, 보호 OCO와 safety plan/action은 모두 0행이었다.
- 저장된 stream recovery 행은 실행 전후 7행으로 변하지 않았다.
- 실제 broker mutation은 POST/PATCH/DELETE 모두 0건이었다.
- broker CLI 최대 RSS는 57,999,360 bytes, 전체 검증 최대 RSS는 639,434,752 bytes였고 측정 swap은 0이었다.

## 남은 단계

실제 entry → 보호 OCO → exact cancel → exact-quantity flatten과 WSS·REST·원장 최종 대사는 아직 시작되지 않았다. 다음 열린 정규장에서 Stage 1을 한 번만 다시 시도한다. 실제 Stage 1 PASS 전에는 ORB 소형 pilot을 시작하지 않으며, 이번 결과는 수익성 증거가 아니다.
