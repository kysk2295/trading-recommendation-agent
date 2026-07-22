# M5 US Swing historical replay 제품 체크포인트

날짜: `2026-07-22 KST`

## 완료한 제품 흐름

- `run_us_swing_historical_replay.py`가 2~32개의 연속 NYSE 정규장 fixture를 날짜순으로 받는다.
- 각 source는 해당 세션의 공식 close tick에서만 기존 `load_swing_daily_source`로 열며, 날짜 누락·중복·역순, 완료 일봉 계약 위반과 사전등록 이전 신호를 차단한다.
- 기존 신고가·RVOL v1 projector가 첫 날 조건부 `WATCH`를 만들고, 조건이 없는 다음 날은 `NO_RECOMMENDATION`을 만든다.
- 기존 Swing shadow engine이 다음 정규장의 trigger, 보수적 stop-first 규칙과 terminal을 append-only 원장에 기록한다.
- 기존 global experiment trial이 장전 등록·정규장 시작·장후 완료 순서를 보존하고, terminal Hermes `EXIT`와 query-only 독립 Reviewer `continue_collection`을 연결한다.
- 모든 요청 날짜의 root card가 이미 있으면 exact replay는 fixture를 다시 열지 않고 저장된 evidence만 집계한다.

## 안전 경계

- Alpaca Paper·실거래 endpoint, account, order, position import와 HTTP POST: 0건
- credential, endpoint, arm, force 옵션: 없음
- fixture와 report 외부 전송: 없음
- Reviewer의 lifecycle·order authority·allocation 변경: 모두 false
- 보고서·SQLite·Writer lock: mode `600`

## 검증

- failing-first E2E: 신규 entrypoint 부재에서 RED, 전체 causal flow에서 GREEN
- exact replay 회귀: 실제 CLI `rc=1` 재현 뒤 source 재개방 0·event 불증가 `rc=0`으로 GREEN
- Swing 집중 회귀: `68 passed`
- 전체 회귀: `3294 passed in 186.87s`
- Ruff 전체: 통과
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- Python no-excuse: 신규 production 2개 파일 위반 0
- 신규 production pure LOC: CLI 164, replay module 170 이하

## 실제 CLI QA

- `--help`: exit `0`
- 잘못된 `--fixture invalid`: exit `1`, blocked report만 생성
- committed 2-session fixture happy path: exit `0`
- 같은 명령 exact replay: exit `0`
- delivery kinds: `watch,no_recommendation,exit`
- Reviewer evidence: `1`
- 결과 집계: causal snapshot `2`, recommendation `1`, no-recommendation `1`, shadow entry `1`, terminal `1`, broker mutation `0`

## 남은 운영 증거

이 체크포인트는 historical fixture에서 M5의 최소 제품 흐름과 인과성·멱등성을 검증한다. 실제 current NYSE post-close source, 장중 `STARTED`, 최대 10거래일 재시작과 장기 forward 표본은 아직 없다. 충분한 독립 표본과 사전등록 평가 전에는 Paper 승격, champion 선언과 Allocation 입력을 허용하지 않는다.
