# KR theme day restartable session supervisor 체크포인트

## 운영 단위

`run_kr_theme_day_session.py`는 장시간 sleep하는 daemon이 아니다. 장중 `onboard`가 사전등록 trial과 fresh same-cycle Opportunity을 immutable receipt와 manifest로 결합하고, `launchd` 같은 scheduler가 `tick`을 반복 호출한다. 각 tick은 receipt의 exact replay, 현재 KST와 calendar evidence 아래 지금 필요한 child만 별도 process로 직렬 실행한 뒤 종료한다.

manifest는 다음 identity를 SHA-256으로 함께 고정한다.

- strategy/code version, Opportunity producer version, session date와 사전등록 시각
- official calendar snapshot, exact Opportunity와 KR symbol
- experiment/calendar/Opportunity/raw/entry/exit/terminal/review/audit store
- redacted report root
- fixture mode에서만 허용하는 intraday/EOD manifest all-or-none pair

onboarding receipt는 exact trial/composite registration key, Opportunity payload SHA, source cycle과 관측·onboarding 시각을 추가로 고정하며 manifest보다 먼저 fsync한다. 두 파일의 canonical bytes, 현재 사용자 소유 regular file, mode 600과 single hard link가 아니면 tick 전에 차단한다. report와 audit에는 symbol, 가격, ID, 경로, raw payload와 credential을 쓰지 않는다.

## Phase와 재시작

한 session은 다음 순서로 진행한다.

1. pre-open trial register
2. 09:00 trial start
3. 09:01~15:30 minute cycle의 KIS GET-only raw collect, shadow entry, shadow exit
4. 15:30~15:31 마지막 15:29 minute catch-up과 exit
5. 15:31 이후 terminal, 독립 Reviewer, lifecycle

phase 결과는 mode-600 append-only SQLite event chain에 content-addressed record로 남는다. 성공한 같은 phase/cycle은 다음 tick에서 실행하지 않는다. blocked event는 이후 child를 중단하며 같은 cycle 재호출이 그 phase부터 재시도한다. child가 store append 후 audit 전에 죽어도 child 자체의 exact replay가 중복 artifact를 만들지 않는다.

collector가 끝난 뒤 현재시각을 다시 읽어 entry/exit 인과성을 평가한다. tick 도중 장중 minute가 바뀌면 이전 minute 평가를 계속하지 않는다. EOD collect 성공 evidence가 없거나 register/start가 누락된 채 15:31을 넘기면 장후 성공을 꾸미지 않고 fail-closed한다.

## 검증

- focused composite/onboarding/session E2E: `32 passed`
- related KR theme/same-cycle: `128 passed`
- 전체 회귀: `2803 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, 신규 production no-excuse: 통과
- actual CLI composite/onboard/tick/verifier help와 missing input: exit `0/2`, blocked report mode `600`
- actual subprocess intraday fixture: phase 5개, raw receipt 3건, shadow entry 1건, same-minute replay child 0건
- same-cycle fixture: DART·LS·KIS ranking·volume surge에서 Opportunity, immutable onboarding receipt와 첫 supervisor tick까지 연결
- restartable no-entry fixture day: EOD catch-up, censored terminal, Reviewer와 lifecycle 완료
- provider credential/live network, 국내 account/order mutation: `0`

## 남은 운영 검증

실제 열린 KRX session에서 현재 calendar와 KIS read-only GET을 최소 한 cycle 실행해 receipt 시각, tick audit와 report를 대사해야 한다. 그 증거가 통과한 뒤에만 private manifest 경로를 읽는 최소권한 `launchd` plist와 장애 재시작 soak를 별도 체크포인트로 추가한다. 한국 증권사 account/order endpoint는 이 경로에 추가하지 않는다.
