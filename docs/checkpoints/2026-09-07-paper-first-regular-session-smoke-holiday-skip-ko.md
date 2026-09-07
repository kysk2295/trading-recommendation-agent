# 서른 번째 Alpaca Paper smoke 시도 체크포인트

작성 시각: 2026-09-07 10:09 EDT / 2026-09-07 23:09 KST
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결론

`feature/paper-account-activities`의 clean local/remote `58ef1be`에서 Stage 1을 서른 번째로 확인했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

뉴욕 현지 시각은 10:09 EDT였지만 프로젝트의 고정 NYSE 캘린더에서 2026-09-07은 Labor Day 전일 휴장으로 판정됐다. `regular_session_bounds(2026-09-07)`가 `None`이고 holiday 집합 포함 여부가 참임을 production 모듈로 직접 확인했다. 다음 캘린더 정규장은 2026-09-08 09:30~16:00 EDT다.

휴장 게이트에서 자격증명·broker REST·주문 WSS·후보 감사·armed CLI를 모두 시작하지 않았다. 따라서 POST/PATCH/DELETE뿐 아니라 broker GET/WSS도 0건이다. broker clock과 현재 계좌 flat은 이번 실행에서 재확인하지 않았으며, 2026-09-04의 마지막 REST 관측만 open order 0·position 0이었다. 이를 현재 상태로 확대 해석하지 않는다.

## 로컬 안전 증거

- 다른 Alpaca Paper CLI·KIS watch·전체시장 backtest 프로세스: 0
- 실행 원장 `PRAGMA user_version`: 9
- 실행 원장 `PRAGMA quick_check`: `ok`
- mutation intent/event: 0/0
- unresolved mutation: 0
- 기존 stream recovery: 7
- 코드 변경: 없음
- 실제 broker 네트워크: GET/WSS/POST/PATCH/DELETE 모두 0
- 최대 mutation RSS: 해당 없음
- 전체 pytest·Ruff·basedpyright: 코드 변경과 broker 실행이 없어 재실행하지 않음
- `graphify query`: 설치된 실행기의 사라진 Python 3.12 경로 때문에 시작 전 실패

## 남은 단계

Stage 1의 entry → 보호 OCO → exact cancel → exact-quantity flatten과 WSS·REST·원장 최종 대사는 아직 완료되지 않았다. 다음 실제 열린 정규장에서 Stage 1을 한 번만 재시도한다. 실제 Stage 1 PASS 전에는 ORB 소형 pilot을 시작하지 않으며, 이번 결과는 수익성 증거가 아니다.
