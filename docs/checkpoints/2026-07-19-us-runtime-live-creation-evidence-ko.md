# US runtime live creation evidence 체크포인트

## 목적

live actionability artifact의 최초 생성 manifest를 schema v2 creation row로 보존하고, supervisor child의 `new/replay`를 독립 cross-store verifier가 terminal 시각 추정 없이 대사한다.

## 구현

- projector 입력을 frozen `manifest + reobserved snapshot + receipt/output store` 요청으로 묶어 base·plan·scan 시각의 느슨한 재조립을 제거했다.
- projector는 manifest-aware atomic append를 사용해 artifact와 creation을 같은 transaction에 기록한다.
- creation builder는 artifact 평가시각이 manifest snapshot에서 허용되는 같은 completed-minute reobservation인지 검증한다. 과거 분 terminal을 이후 분 manifest에 결합하지 못한다.
- dispatcher는 connector와 terminal reuse 전에 artifact와 creation history를 모두 query-only로 재생한다.
- verifier는 creation이 있으면 exact creation manifest와 그 digest receipt를 source로 사용한다. creation manifest가 현재 parent manifest면 `new`, 더 이른 manifest면 `replay`다.
- legacy v1 artifact는 creation을 추정하거나 backfill하지 않고 기존 terminal-time fail-closed 판정을 유지한다.
- manifest/receipt filesystem inventory를 별도 모듈로 분리해 verifier 본체를 201 pure LOC로 유지했다.

## 검증

- live first projection에서 artifact 1개와 creation 1개 생성
- exact restart와 다음 minute terminal reuse에서 connector 0건, creation 1개 유지
- 이전 minute artifact를 이후 manifest에 신규 creation으로 결합하는 시도 차단
- 유효한 creation payload를 같은 minute의 다른 manifest로 바꾼 fault injection 차단
- 관련 41개와 전체 2578 tests 통과
- Ruff, changed-file format, basedpyright 0/0, compileall, no-excuse 통과
- actual projection CLI `--help` exit 0, missing input exit 1/output DB 0, first/replay exit 0/0과 artifact/creation 1/1 확인
- actual verifier CLI exit 0, `ready`, `created/replay/artifact=1/1/1` 확인

## 권한 경계

fixture connector와 local SQLite만 사용했다. 실제 provider WebSocket, Alpaca account/order/position endpoint, Paper broker POST/DELETE와 실제 자금 거래는 0건이다. 열린 NYSE 정규장과 private market-data credential·SIP entitlement가 모두 맞을 때만 별도 bounded read-only smoke를 실행한다.
