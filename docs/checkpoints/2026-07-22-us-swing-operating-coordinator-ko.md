# US Swing 일일 운영 Coordinator 체크포인트

## 결과

- 완료 일봉 스캔, shadow 원장, 전향 trial, Hermes 결과, 독립 Reviewer를 하나의 재시작 가능한 `tick`으로 연결했다.
- 장전에는 전날 생성됐지만 아직 등록되지 않은 신호를 다음 정규장 개장 전에 복구 등록한다.
- 정규장 안에서만 preregistered trial의 `STARTED` event를 기록한다.
- 장후에는 해당 거래일 source cycle이 없을 때만 스캐너를 실행한 뒤 신규 신호를 등록하고, terminal trial을 완료·전달·검토한다.
- 정규장 start를 놓친 trial은 과거 시각으로 보정하지 않고 `blocked_signal_ids`로 fail-closed 처리한다.
- coordinator와 scanner에는 broker, Paper 계좌, 주문 제출 권한이 없다.

## 실행

```bash
uv run python run_us_swing_operating_session.py \
  --session-date YYYY-MM-DD \
  --universe-file /private/path/us-swing-universe.txt
```

기본 원장은 다음과 같다.

- experiment: `outputs/experiment_control/experiment_ledger.sqlite3`
- shadow: `outputs/us_swing_shadow/swing-shadow.sqlite3`
- Hermes delivery: `outputs/hermes/delivery.sqlite3`
- review: `outputs/us_swing_shadow/reviews.sqlite3`
- report: `outputs/us_swing_shadow/operating/latest/us_swing_operating_session_ko.md`

production CLI는 요청 거래일이 현재 뉴욕 거래일과 일치하고 Git worktree가 clean일 때만 현재 commit SHA를 trial 코드 버전으로 사용한다. 장후 source cycle의 root `WATCH` 또는 `NO_RECOMMENDATION`이 이미 있으면 scanner를 다시 호출하지 않는다.

## 검증

- coordinator/CLI focused E2E: `5 passed`
- Swing 회귀: `63 passed`
- 전체 테스트: `3279 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- no-excuse checker: 신규 production 2개 파일 위반 0
- 수동 CLI:
  - `--help`: 성공
  - 현재 거래일과 다른 입력: `rc=1`, broker mutation 0
  - prospective fixture 첫 실행/replay: `rc=0/0`, signal 1, trial 1, delivery 1

## 남은 실제 증거

- 이 체크포인트는 fixture와 local immutable ledger에서 운영 수직을 검증한 code-ready 상태다.
- 다음 열린 미국 정규장에서 장중 `STARTED`를 실제 시각으로 기록하고, 장후 완료 일봉으로 terminal·Hermes·Reviewer까지 이어지는 forward evidence가 필요하다.
- Swing champion 승격은 충분한 독립 표본, 고정 비용모형, preregistered 평가를 통과하기 전까지 금지한다.
- Allocation Manager는 최소 두 개의 독립 executable champion이 생길 때까지 구현·활성화하지 않는다.
