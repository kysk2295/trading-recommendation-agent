# KR M3 live preflight 복구 체크포인트

## 실제 세션 결과

2026-07-23 KST production shadow는 계좌·주문 권한 없이 고정 runtime
`960de7e0571728653966c36dfacfae7d07b44814`로 시작했다.

- 08:55 KIS 휴장일 GET과 current-date calendar snapshot 저장은 성공했다.
- runner가 `registered_at`을 초 단위로 잘라 `08:55:00`으로 만들었지만 snapshot
  `observed_at`은 `08:55:00.259228`이었다. trial은 미래 evidence로 정확히 차단됐다.
- 첫 실패 뒤 submitted launchd job이 전체 runner를 반복해 run count 18까지 증가했다.
  해당 KR label만 내리고 성공한 snapshot과 composite를 보존했다.
- 09:00 전 microsecond 정밀도 시각으로 trial register를 복구했다. 수동 복구 명령이
  exact `09:00 STARTED` event를 약 15초 먼저 append한 추가 결함도 발견했다. 이 event는
  유효한 forward-session 시작 근거로 세지 않는다.
- 09:05 same-cycle은 OpenDART private 설정이 없어 source preflight에서 차단됐다.
  이 시도에서 KIS ranking, LS NWS, account, order provider operation은 0건이고
  Opportunity과 TradeSignal은 만들지 않았다.

이는 추천 0건이나 전략 성과 0이 아니다. 필수 source 수집 전 data-quality blocker이므로
당일 trial은 `CENSORED/no_shadow_entry_artifact`로만 평가한다.

## 닫은 결함

체크포인트 `aa8730c`, `293c28c`, `a8ee8dc`, `db72cc1`, `6805be5`는 운영 결함을
닫는다.

1. trial CLI는 명시 시각과 calendar 관측시각이 절대시각 기준 같은 초인 경우에만
   관측시각으로 올려 causal ordering을 보존한다. 다음 초 또는 더 먼 미래 evidence는
   계속 차단한다.
2. 권한 검증 뒤 source ledger 생성 전에 provider preflight가 실패하면
   `blocked_source_preflight` Hermes incident를 cycle/date/strategy identity로 한 번만
   append한다. partial source ledger가 있으면 기존 source별 incident를 우선 사용한다.
3. 같은 preflight cycle 재실행은 기존 delivery event 전체 계약을 검증한 뒤 replay하고,
   payload가 다르면 fail-closed한다.
4. first STARTED event는 미래 시각이면 차단한다. crash recovery를 위한 과거 exact
   STARTED append와 이미 검증된 exact event replay는 유지한다.
5. collector가 complete terminal source cycle을 반환한 뒤 projection이 실패한 경우에는
   source preflight incident로 잘못 분류하지 않는다.
6. generic preflight incident는 exact terminal replay를 먼저 확인한 뒤 OpenDART·LS·KIS
   credential file 구조만 검증하는 전용 typed preflight 실패에서만 만든다. collector의
   다른 예외는 이 상태로 축소하지 않는다.
7. typed preflight 실패와 partial source ledger가 함께 있으면 source-specific
   `blocked_source_incomplete`를 우선하고 generic incident를 만들지 않는다.
8. incident projection의 lease·SQLite·conflict·I/O 실패는 삼키지 않고 typed delivery
   error로 전파해 supervisor 재시작과 운영 감시가 실패를 관측하게 한다.

실제 delivery DB에서 event `38 -> 39`, attempt `43 -> 44`, acknowledgement
`27 -> 28`을 확인했다. 같은 cycle 재실행 뒤 세 수는 변하지 않았고 KR preflight
incident는 정확히 1건이었다. 메시지 본문, platform ID와 자격증명 값은 출력하지 않았다.

재검증 가능한 redacted aggregate는
`outputs/kr_theme/m3_live/2026-07-23/verification/kr_m3_preflight_delivery_attestation.json`에
mode `600`으로 저장했다. 이 artifact는 private delivery DB, typed-preflight operator report,
session event log의 상대경로와 집계 수만 포함하며 delivery ID, 메시지 본문, account ID와
credential은 포함하지 않는다.
독립 리뷰가 private 운영 경로 없이 같은 집계를 검사할 수 있도록 내용이 동일한 redacted
mirror를
`docs/checkpoints/2026-07-23-kr-m3-preflight-delivery-attestation.json`에도 보존했다.

15:32 KST에는 별도 one-shot finalizer
`ai.trading-agent.kr-m3-finalize-20260723`가 exact post-session terminal, independent
Reviewer와 lifecycle evidence를 실행하도록 대기한다. 이 job에도 account, order,
Paper arm 또는 국내 주문 endpoint가 없다.

## 검증

- RED: 같은 초의 259228 microsecond calendar와 source-ledger 이전 preflight failure
- focused regressions: 기존 preflight 묶음 `52 passed`; 리뷰 blocker 묶음 `23 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 전체 pytest: 제품/시장 코드 `3279 passed`; Grok 개발 harness를 포함한 전체
  `3368 passed`. 실행 전 SHA·명령·raw pytest 출력·종료코드는
  [검증 artifact](2026-07-23-full-pytest-95f0271.txt)에 보존했다.
- actual CLI replay: incident `1 -> 1`, delivery/attempt/ACK 추가 `0/0/0`
- fixture CLI happy/replay: exit `0/0`, projection run `1`, Opportunity `1`, report mode `600`
- domestic account/order mutation: `0`
- Alpaca Paper POST/DELETE: `0`

OpenDART mode-600 설정이 공급되기 전에는 complete four-source cycle과 KR 추천을 열지
않는다. 이 세션은 champion, 수익성 또는 Allocation Manager 근거가 아니다.
