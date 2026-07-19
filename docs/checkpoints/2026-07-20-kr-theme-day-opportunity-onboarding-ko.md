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

검증 후 content-addressed onboarding receipt를 mode 600으로 먼저 fsync하고 session manifest를 쓴다. receipt 뒤 manifest write가 실패하면 exact replay가 기존 receipt를 검증하고 manifest만 복구한다. 기존 receipt·manifest의 payload, mode, owner, hard link 또는 source lineage가 달라지면 fail-closed한다.

`tick`과 `run_kr_theme_day_session_verify.py`는 session을 열기 전에 receipt에서 trial과 Opportunity identity를 복원해 동일한 onboarding을 no-write replay한다. 따라서 legacy manifest만 있거나 이후 원천이 바뀐 경우 child·provider 실행 없이 차단된다.

## E2E와 검증

- committed 2026-07-20 synthetic fixture에서 OpenDART, LS NWS, KIS ranking과 volume surge를 순서대로 수집했다.
- final same-cycle projection이 rank-1 Opportunity 하나를 만들었다.
- exact pre-open composite와 day trial이 그 Opportunity producer version을 고정했다.
- onboarding이 immutable receipt와 manifest를 만들고 실제 supervisor subprocess의 첫 intraday tick이 5개 phase, raw receipt 3건, shadow entry 1건과 audit 5건을 남겼다.
- exact replay는 receipt, manifest와 기존 append-only artifact를 늘리지 않는다.
- 관련 KR theme/same-cycle selection: `112 passed`
- 전체 pytest: `2777 passed`
- Ruff, basedpyright `0 errors, 0 warnings`, changed-file format, compileall, JSON parse, diff/no-excuse gate: 통과
- actual composite/onboard/trial/verifier help와 missing-input CLI: 기대 exit `0/2`, 권한 옵션 0건
- provider credential/live network와 국내 account/order mutation: `0`

fixture의 시계는 현재 장 전 실행환경에서 09:03 evidence를 재현하기 위한 test-only injection이다. production CLI에는 시각 override가 없으며 실제 열린 KRX session의 read-only KIS GET smoke는 수행하지 않았다.

## 다음 운영 단계

1. 열린 KRX session에서 새 collection cycle로 bounded read-only source 수집과 Opportunity 생성
2. onboarding receipt, 첫 tick raw receipt, phase audit와 query-only verifier 대사
3. 증거가 통과한 뒤 최소권한 scheduler와 restart soak 추가

이 체크포인트는 한국장 shadow forward-validation wiring이며 추천 정확도, 실현 수익 또는 국내 주문 권한의 증거가 아니다.
