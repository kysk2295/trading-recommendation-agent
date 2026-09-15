# 서른다섯 번째 Alpaca Paper smoke 시도 체크포인트

작성 시각: 2026-09-15 09:42~09:43 EDT / 2026-09-15 22:42~22:43 KST
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결론

`feature/paper-account-activities`의 clean local/remote `932ee7c`에서 Stage 1을 서른다섯 번째로 시도했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

production 로컬 캘린더는 열린 정규장 시간대로 판정했다. 실제 Alpaca Paper GET-only bootstrap은 기존 계좌 binding exact를 확인했고 initial/final preflight는 open order 0·position 0이었다. 주문 WSS readiness와 targeted GET/WSS-only mutation recovery는 모두 `PaperOrderStreamUnavailableError`로 fail-closed했다.

WSS 선행 실패로 broker clock과 exact current ORB 후보를 감사하지 않았으며 armed entry/OCO/cancel/flatten CLI를 호출하지 않았다. 따라서 실제 POST/PATCH/DELETE는 0건이다. 최종 원장은 schema v9·`quick_check=ok`·unresolved mutation 0이며 REST 기준 flat이지만, WSS 최종 대사가 없으므로 Stage 1 PASS로 기록하지 않는다.

## 검증·자원 증거

- 실제 bootstrap·initial/final preflight: PASS
- readiness·targeted recovery: WSS fail-closed
- production endpoint: REST/WSS Paper 고정값, live endpoint literal 0건
- 실제 broker mutation: POST/PATCH/DELETE 0건
- broker CLI 최대 RSS: 58,671,104 bytes, 각 process swap 0
- 별도 KIS ORB watch: 1개 실행 중, Alpaca CLI 및 전체시장 백테스트 중복 0개
- 코드 변경: 없음
- 전체 pytest·Ruff·basedpyright: 코드 변경이 없어 재실행하지 않음
- `graphify query`: 설치된 실행기의 사라진 Python 3.12 경로 때문에 시작 전 실패

## 남은 단계

Stage 1의 entry → 보호 OCO → exact cancel → exact-quantity flatten과 WSS·REST·원장 최종 대사는 아직 완료되지 않았다. 다음 실제 열린 정규장에서 Stage 1을 한 번만 재시도한다. 실제 Stage 1 PASS 전에는 ORB 소형 pilot을 시작하지 않으며, 이번 결과는 수익성 증거가 아니다.
