# 열여섯 번째 Alpaca Paper smoke 시도 체크포인트

작성 시각: 2026-08-14 22:08 EDT / 2026-08-15 11:08 KST  
판정: **SAFE SKIP / STAGE 1 BLOCKED**

## 결론

`feature/paper-account-activities`의 remote 최신 `c445bfe`에서 단계 1을 열여섯 번째로 확인했다. 사용자 지정 기준 `7b033f3`은 이 commit의 ancestor다.

실행 시각은 뉴욕 2026-08-14 22:05~22:08로 로컬 NYSE 캘린더의 정규장 09:30~16:00 뒤였다. production 캘린더 함수도 `regular_session_is_open=False`를 반환했다. 따라서 실제 주문 WSS와 broker clock을 열지 않고 모든 armed entry·OCO·cancel·flatten CLI를 생략했다.

## 실제 Paper GET-only 확인

- 기존 canonical execution ledger를 그대로 사용했다.
- bootstrap은 기존 account binding을 exact 확인하고 종료코드 0이었다.
- final preflight는 open order 0, position 0으로 종료코드 0이었다.
- 원장은 schema v9, `PRAGMA quick_check=ok`, unresolved mutation 0이었다.
- order intent, broker order event, FILL, mutation intent/event, trade update, 보호 OCO와 safety plan/action은 모두 0행이었다.
- 저장된 stream recovery 행은 실행 전후 7행으로 변하지 않았다.
- 실행된 broker 네트워크 표면은 계좌·주문·포지션 GET뿐이었다.
- POST/PATCH/DELETE는 0건이었다.

## 자원·안전 경계

- Paper credential 파일은 값 확인 없이 regular file, 현재 사용자 소유, mode `600`만 확인했다.
- 허용 REST/WSS 상수는 각각 `https://paper-api.alpaca.markets`, `wss://paper-api.alpaca.markets/stream`이었다.
- redirect는 비활성이고 mutation transport 자동 retry는 0이다.
- 실행 전 Alpaca Paper Writer, KIS watch, full-universe backtest는 없었다.
- 최대 broker CLI RSS는 59,310,080 bytes, 측정 swap은 0이었다.
- 사용자 변경이 있는 primary `main` worktree는 사용하거나 수정하지 않았다.

## 남은 단계

실제 entry → 보호 OCO → exact cancel → exact-quantity flatten과 WSS·REST·원장 최종 대사는 아직 시작되지 않았다. 다음 열린 정규장에서 Stage 1을 한 번만 다시 시도한다. Stage 1 실제 PASS 전에는 ORB 소형 pilot을 시작하지 않으며, 이번 결과는 수익성 증거가 아니다.
