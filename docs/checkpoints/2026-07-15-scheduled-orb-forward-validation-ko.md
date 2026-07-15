# Scheduled ORB forward-validation 체크포인트

날짜: 2026-07-15

상태: **기존 장후 연구 체인과 lane snapshot·Reviewer runner를 opt-in fail-closed 순서로 연결**

## 완성된 일일 순서

```text
run_paper_metrics.py
  → run_daily_research_record.py
  → run_adaptive_strategy_evaluation.py
  → run_orb_lane_forward_validation.py
       → intraday LaneDailySnapshot
       → independent Reviewer event
```

각 child가 종료코드 0일 때만 다음 child를 시작한다. metrics·daily record·adaptive 실패는 Paper credential 또는 broker readiness를 건드리지 않고 lane 단계를 억제한다. lane runner 실패도 `post_session_lane_forward_validation_cycles.csv`에 기록되고 watch 전체 실패 수에 포함된다.

## 설정 계약

`run_kis_paper_watch.py`에 다음 네 경로를 모두 지정했을 때만 scheduled lane 단계를 활성화한다.

- `--lane-execution-database`
- `--lane-registry`
- `--lane-review-ledger`
- `--lane-forward-output-dir`

하나라도 빠지면 watch 시작 전에 종료코드 2로 차단한다. 이 연결은 현재 exact ORB scope만 지원하므로 다른 전략과 함께 지정해도 provider·market wait 전에 차단한다. 기본 watch는 네 경로가 없으면 기존 metrics→daily record→adaptive 동작을 그대로 유지한다.

## 권한 경계

- watch는 기존 child 실행 순서와 session audit만 소유한다.
- lane child 명령에는 arm, credential, endpoint, fixture, force 또는 mutation smoke 인자가 없다.
- snapshot은 local preflight 뒤 fixed Alpaca Paper credential로 GET/WSS readiness만 수집한다.
- Reviewer는 credential, broker, HTTP, execution DB 또는 mutation 모듈을 사용하지 않는다.
- snapshot/Reviewer의 append-only DB와 Writer lease는 watch 프로세스에 합쳐지지 않는다.
- 자동 champion, promote/demote, allocation 또는 주문권한 변경은 없다.

## 수동 QA

- executable help: 네 lane path 옵션 표시, authority 옵션 부재
- partial 설정: 종료코드 2, `모두 함께` 차단
- complete non-ORB 설정: 종료코드 2, ORB-only 차단
- fake success/replay: 매 실행 네 child 순서 동일, lane watch audit 2행
- fake adaptive failure: 세 번째 child에서 종료, lane child 0회
- fake lane failure: 네 번째 child nonzero가 watch 결과로 그대로 전파
- credential 사용: 0회
- broker network: 0회
- 외부 Alpaca Paper POST/DELETE: 0건

## 검증

- scheduled watch·lane runner focused 회귀: `19 passed`
- 전체 회귀: `777 passed`
- `uv run ruff check .`: 통과
- `uv run basedpyright`: 오류 0, 경고 0
- 변경 Python 3개 `ruff format --check`: 통과
- `git diff --check`: 통과

## 현재 제한

- 실제 정규장 Paper entry→보호 OCO→대사→EOD flat smoke는 아직 없다.
- 실제 session artifact와 fixed Paper credential을 사용한 scheduled GET/WSS snapshot은 아직 실행하지 않았다.
- 적격 forward 표본, champion과 allocation eligible snapshot은 아직 0이다.
- swing은 shadow-only, market regime은 signal-only다.

이 연결은 확정수익이나 전략 승격 증거가 아니라 ORB Paper forward-validation 후보와 blocker를 매일 동일 순서로 축적하기 위한 운영 경계다.
