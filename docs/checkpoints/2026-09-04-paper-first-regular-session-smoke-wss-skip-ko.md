# 스물아홉 번째 Alpaca Paper smoke 시도 체크포인트

작성 시각: 2026-09-04 09:51~10:25 EDT / 2026-09-04 22:51~23:25 KST
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결론

`feature/paper-account-activities`의 local/remote `47e3e8f`에서 단계 1을 스물아홉 번째로 시작했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

뉴욕 현지 시각은 정규장 시간대였고 GET-only bootstrap은 기존 account binding을 확인했다. 첫 preflight는 broker 호출 전에 독립 uv 환경의 `websockets` 누락으로 실패했다. 프로젝트 환경 의존성이 이 결함을 가리고 있었으므로 PEP 723 metadata를 TDD로 수정해 `e544740`에 push했고, 실제 preflight를 새 출력 폴더에서 재실행해 open order 0, position 0을 확인했다.

주문 WSS readiness와 한 번의 targeted GET/WSS-only mutation recovery는 모두 `PaperOrderStreamUnavailableError`로 종료됐다. WSS 선행 실패로 broker clock은 관측하지 못했고, 첫 broker readiness nonzero 게이트에서 후보 감사와 모든 armed CLI를 중단했다.

## 검증과 최종 상태

- 전체 회귀 947개, Ruff, basedpyright 0 errors/warnings를 통과했다.
- 변경 파일 format·no-excuse 검사, CLI help 5종, invalid arm 3종 무생성 계약과 fake broker 전 수명주기 E2E를 통과했다.
- production source의 live Alpaca endpoint literal은 0건이다.
- final REST preflight는 open order 0, position 0으로 종료코드 0이었다.
- 원장은 schema v9, `PRAGMA quick_check=ok`, unresolved mutation 0이었다.
- order intent, broker order event, FILL, mutation intent/event, trade update, 보호 OCO와 safety plan/action은 모두 0행이었다.
- 저장된 stream recovery 행은 7행으로 변하지 않았다.
- 실제 broker mutation은 POST/PATCH/DELETE 모두 0건이었다.
- broker CLI 최대 RSS는 101,629,952 bytes, 수정 후 전체 검증 최대 RSS는 639,057,920 bytes였고 측정 프로세스 page swap은 0이었다.
- `graphify update .`은 설치된 실행기의 Python 3.12 경로가 사라져 시작 전에 실패했으며 그래프 산출물은 갱신되지 않았다.

## 남은 단계

실제 entry → 보호 OCO → exact cancel → exact-quantity flatten과 WSS·REST·원장 최종 대사는 아직 시작되지 않았다. 다음 실제 열린 정규장에서 Stage 1을 한 번만 다시 시도한다. 실제 Stage 1 PASS 전에는 ORB 소형 pilot을 시작하지 않으며, 이번 결과는 수익성 증거가 아니다.
