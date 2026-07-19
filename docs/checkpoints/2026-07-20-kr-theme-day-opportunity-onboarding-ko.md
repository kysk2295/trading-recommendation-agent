# KR theme day Opportunity onboarding 체크포인트

## 닫은 결함

이전 session `init`은 운영자가 day strategy, 사전등록 시각, Opportunity과 symbol을 직접 조합할 수 있었다. 장 전에 존재하지 않은 Opportunity을 manifest에 넣거나 Opportunity Manager의 다른 version 결과를 day trial에 연결해도 session identity 자체는 만들어질 수 있었다. 이는 각 lane 결과의 결합을 신규 가설로 먼저 등록해야 한다는 전역 실험 계약과 맞지 않았다.

## 사전등록 권위

- Opportunity Manager와 Day Agent의 exact registered strategy version을 먼저 요구한다.
- 두 version과 고정 결합 규칙으로 cross-lane composite hypothesis ID를 content-address한다.
- composite는 전역 experiment ledger에 append-only로 장 전에 등록하며 두 component의 shadow 권위와 code-coupled version을 다시 검증한다.
- day trial evidence budget은 composite hypothesis ID, registration key, exact Opportunity producer version을 고정한다.
- 등록시각이 component 등록보다 빠르거나 trial 등록보다 늦은 사후 결합은 차단한다.

## 장중 onboarding

`run_kr_theme_day_session.py onboard`는 strategy/code version, pre-open 시각과 symbol을 입력받지 않는다. exact trial ID와 Opportunity ID를 기준으로 다음 원천을 query-only 재생한다.

1. trial registration과 0개 또는 정확한 started event
2. trial evidence가 가리키는 exact composite hypothesis
3. day strategy version과 official KIS open-session calendar snapshot
4. exact Opportunity producer version, 같은 KST session, 아직 유효한 관측시각
5. 하나뿐인 `kr/collection_cycle` evidence와 rank-1 symbol

최초 composite·trial append는 CLI wrapper가 아니라 실제 registration service에서 입력 등록시각과 실제 현재시각이 모두 KST 09:00 전이고 그 차이가 0~5분일 때만 허용한다. 기존 exact registration replay는 장중 재시작을 위해 이 현재시점 gate를 다시 열지 않지만, 같은 ID의 다른 payload는 ledger에서 계속 차단된다. 운영 `onboard` CLI에는 fixture onboarding 시각 override가 없다.

검증 후 content-addressed onboarding receipt를 mode 600으로 먼저 fsync하고 session manifest를 쓴다. root부터 no-follow로 연 directory descriptor를 끝까지 유지한 채 race-safe per-target lock을 획득하고 private staging을 fsync한 뒤 no-overwrite hard link로 final 이름을 게시한다. reader와 exact replay도 같은 잠금 아래 interrupted two-link alias를 복구하고 final 이름을 다시 열어 retained inode와 대사하므로 경로가 바뀌면 성공으로 인정하지 않는다. pre-link 중단의 고아 staging은 다음 잠금 보유 replay가 정리한다. receipt 뒤 manifest 게시가 실패하거나 receipt timestamp read 전에 중단돼도 exact replay가 기존 receipt를 복구·검증하고 manifest만 다시 만든다. 기존 receipt·manifest의 payload, mode, owner, 외부 hard link 또는 source lineage가 달라지면 fail-closed한다.

`tick`과 `run_kr_theme_day_session_verify.py`는 session을 열기 전에 receipt에서 trial과 Opportunity identity를 복원해 동일한 onboarding을 no-write replay한다. manifest는 Opportunity canonical SHA도 identity에 고정하고 intraday child가 outbox를 다시 읽은 직후 exact SHA를 대사하므로 replay gate 뒤 원문 교체도 entry 전에 차단된다. canonical 09:00이 아닌 기존 START event, legacy manifest 또는 이후 원천 변경도 child·provider 실행 전에 닫힌다.

## E2E와 검증

- committed 2026-07-20 synthetic fixture에서 OpenDART, LS NWS, KIS ranking과 volume surge를 순서대로 수집했다.
- final same-cycle projection이 rank-1 Opportunity 하나를 만들었다.
- exact pre-open composite와 day trial이 그 Opportunity producer version을 고정했다.
- onboarding이 immutable receipt와 manifest를 만들고 실제 supervisor subprocess의 첫 intraday tick이 5개 phase, raw receipt 3건, shadow entry 1건과 audit 5건을 남겼다.
- exact replay는 receipt, manifest와 기존 append-only artifact를 늘리지 않는다.
- 관련 KR theme/same-cycle selection: `127 passed`
- 전체 pytest: `2799 passed`
- Ruff, basedpyright `0 errors, 0 warnings`, changed-file format, compileall, JSON parse, diff/no-excuse gate: 통과
- actual composite/onboard/trial/verifier help와 missing-input CLI: 기대 exit `0/2`, 권한 옵션 0건
- provider credential/live network와 국내 account/order mutation: `0`

fixture의 시계는 현재 장 전 실행환경에서 09:03 evidence를 재현하기 위한 test-only injection이다. production CLI에는 시각 override가 없으며 실제 열린 KRX session의 read-only KIS GET smoke는 수행하지 않았다.

## 다음 운영 단계

1. 열린 KRX session에서 새 collection cycle로 bounded read-only source 수집과 Opportunity 생성
2. onboarding receipt, 첫 tick raw receipt, phase audit와 query-only verifier 대사
3. 증거가 통과한 뒤 최소권한 scheduler와 restart soak 추가

이 체크포인트는 한국장 shadow forward-validation wiring이며 추천 정확도, 실현 수익 또는 국내 주문 권한의 증거가 아니다.
