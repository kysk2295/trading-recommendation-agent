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

체크포인트 `aa8730c`와 후속 `293c28c`는 운영 결함을 닫는다.

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

실제 delivery DB에서 event `38 -> 39`, attempt `43 -> 44`, acknowledgement
`27 -> 28`을 확인했다. 같은 cycle 재실행 뒤 세 수는 변하지 않았고 KR preflight
incident는 정확히 1건이었다. 메시지 본문, platform ID와 자격증명 값은 출력하지 않았다.

15:32 KST에는 별도 one-shot finalizer
`ai.trading-agent.kr-m3-finalize-20260723`가 exact post-session terminal, independent
Reviewer와 lifecycle evidence를 실행하도록 대기한다. 이 job에도 account, order,
Paper arm 또는 국내 주문 endpoint가 없다.

## 검증

- RED: 같은 초의 259228 microsecond calendar와 source-ledger 이전 preflight failure
- focused regression: `30 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 전체 pytest: 제품/시장 코드 `3274 passed`; 별도 Grok harness `84 passed, 5 failed`.
  합계 `3358 passed, 5 failed`이며 실패 5개는 임시 repo에서 `uv run --offline`이
  pytest·Ruff·basedpyright 실행환경을 해석하지 못한 현재 host 의존 경로다.
- actual CLI replay: incident `1 -> 1`, delivery/attempt/ACK 추가 `0/0/0`
- domestic account/order mutation: `0`
- Alpaca Paper POST/DELETE: `0`

OpenDART mode-600 설정이 공급되기 전에는 complete four-source cycle과 KR 추천을 열지
않는다. 이 세션은 champion, 수익성 또는 Allocation Manager 근거가 아니다.
