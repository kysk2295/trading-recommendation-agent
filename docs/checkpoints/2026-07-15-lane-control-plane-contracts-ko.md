# Lane control-plane 계약 체크포인트

날짜: 2026-07-15

상태: **세 lane 계약·append-only registry·일일 experiment scope 격리 완료, 실행 권한 확대 없음**

## 구현 경계

- 닫힌 `LaneId`는 `intraday_momentum`, `swing_momentum`, `market_regime` 세 개다.
- intraday는 `intraday_flat_by_close_v1`, swing은 `swing_shadow_multisession_v1`, regime은 `regime_signal_publish_v1`로 서로 다른 실행 정책과 상태기계를 갖는다. overnight boolean으로 합치지 않는다.
- intraday만 Alpaca Paper 권한과 전용 account binding을 요구한다. swing은 shadow-only, regime은 signal-only이며 둘 다 account binding을 거절한다.
- 현재 intraday pilot 위험은 notional 100 USD, 계획위험 10 USD, 최대 1포지션, 일손실 30 USD, 편도 20bp, risk fraction 1/3000으로 고정했다. 기존 smoke보다 완화하지 않았다.
- 축소 위험계약은 기존 manifest `1.0.0`을 재작성하지 않고 실제 사전등록 시각 `2026-07-15T01:00:05Z`의 intraday/swing `1.0.1`로 append한다. regime은 내용이 바뀌지 않아 `1.0.0`을 유지한다.
- execution 원장은 schema v9를 그대로 유지한다. big-bang 테이블 이동이나 lane column 전파는 하지 않았다.

## 별도 registry

lane registry schema v1은 다음 네 append-only 테이블을 가진다.

- `lane_manifests`
- `lane_account_bindings`
- `experiment_scopes`
- `lane_daily_snapshots`

각 테이블은 UPDATE/DELETE trigger로 불변이며 exact replay는 idempotent하다. lane/version, hypothesis ID, lane binding, lane/date snapshot의 내용이 바뀌면 typed conflict로 차단한다. account fingerprint와 execution-ledger fingerprint는 각 lane에서 유일해야 하며 계좌번호·키·secret은 저장하지 않는다. 독립 Reviewer용 reader는 SQLite `mode=ro`와 `query_only`만 사용하고 주문 메서드가 없다.

`LaneDailySnapshot`은 manifest/scope key, source ledger generation/hash, 데이터 품질, incident, champion version과 보수적 PnL·노출만 담는다. intraday final snapshot은 open order 0·position 0·open risk 0이어야 한다. signal-only regime의 broker 필드는 모두 0이어야 한다. Portfolio Manager는 구현하지 않았고 최소 두 lane champion 전에는 추가하지 않는다.

## Experiment scope

- 단일 lane 가설은 정확히 한 lane만 포함한다.
- cross-lane 가설은 두 개 이상의 lane과 source hypothesis, source와 다른 신규 hypothesis ID, 명시적 결합 규칙, 시장 개장 전 등록시각을 모두 요구한다.
- hypothesis ID는 registry에서 유일하므로 결과를 본 뒤 다른 lane으로 옮길 수 없다.
- `DailyResearchRecord`는 schema v2에서 scope와 scope key를 record ID·누적 필터에 포함한다. 같은 strategy/evaluator라도 scope가 다르면 적격일·거래 수를 합치지 않는다.
- 기존 schema v1 JSONL은 원본을 수정하지 않고 역사적 `intraday_momentum` 단일 scope로 읽기 projection만 한다.
- adaptive evaluator도 exact scope key가 같은 거래일만 누적하며, 기존 schema v1 개별 session record를 원본 수정 없이 같은 방식으로 projection한다.

## Bootstrap과 수동 QA

`run_lane_control_plane_bootstrap.py`는 외부 API나 자격증명 없이 세 manifest와 현재 intraday 가설 scope 네 개를 등록한다. 선택적으로 기존 current-schema execution DB의 저장된 account fingerprint와 `bound_at`을 읽어 intraday 전용 binding을 만든다. resolved path는 로컬 SHA-256 입력으로만 사용하며 보고서에 쓰지 않는다.

2026-07-23 actual Paper control-plane 준비에서 process umask에 따라 bootstrap 보고서가
mode `644`로 생성되는 결손을 관찰했다. 보고서 writer를 공용 atomic private-report
경계로 교체해 success·blocked·replay 모두 mode `600`을 강제하고 CLI 회귀
`7 passed`, Ruff, basedpyright `0 errors, 0 warnings`로 확인했다. 기존 노출 파일은
즉시 mode `600`으로 보정했으며 account fingerprint와 execution path는 계속
보고서에 기록하지 않는다.

- executable `--help`: 종료코드 0
- registry-only 초기화: manifest 3/3, scope 4/4, binding `not_requested`, 종료코드 0
- 임시 execution DB binding: 기존 manifest/scope replay 0건, binding `registered`, 종료코드 0
- 기존 `1.0.0` registry migration: intraday/swing `1.0.1` manifest 2건 append, 기존 scope replay 0건, binding `registered`, 종료코드 0
- 없는 execution DB: registry를 쓰기 전에 `blocked`, 종료코드 1
- `user_version=9`이지만 schema object가 훼손된 execution DB: traceback·경로 노출 없이 `blocked`, 종료코드 1
- 보고서 fingerprint·execution path·manifest/binding key redaction 확인

## 검증

- lane policy·contract·registry·bootstrap·daily scope 표적 회귀: `34 passed`
- 전체 회귀: `665 passed`
- `uv run ruff check .`: 통과
- `uv run basedpyright`: `0 errors, 0 warnings`
- 변경 Python 18개 `ruff format --check`: 통과

이번 체크포인트는 로컬 SQLite와 fixture/fake 경로만 사용했다. Alpaca 자격증명을 읽지 않았고 외부 Paper POST/DELETE는 0건이다. 이 결과는 아키텍처·원장 격리 검증이며 전략 수익성이나 실제 체결 품질의 증거가 아니다.

## 다음 안전 단계

1. 열린 정규장에서 기존 축소 한도로 entry·즉시 보호 OCO·WSS/REST/Account Activities 대사·EOD flat smoke를 수행한다.
2. ORB broker/shadow 일일 결과를 final intraday snapshot으로 만드는 producer를 연결한다.
3. 독립 Reviewer가 query-only registry와 실험 원장을 읽어 승격·중단 blocker를 append-only로 남긴다.
4. swing은 shadow-only, regime은 signal-only를 유지하고 최소 두 champion 전에는 Portfolio Manager를 구현하지 않는다.
