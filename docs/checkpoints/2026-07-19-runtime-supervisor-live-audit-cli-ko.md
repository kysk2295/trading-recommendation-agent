# Runtime supervisor live audit 조회 CLI 체크포인트

## 완료 범위

- `run_us_runtime_supervisor_live_audit.py`는 supervisor store만 query-only로 읽는다.
- parent/legacy parent/child 수, disabled/not-attempted/completed/blocked 수와 selected/new/replay 합계만 보고한다.
- child attempt ID는 parent history의 연속 suffix여야 하며 중간 누락이나 순서 변경은 차단한다.
- 보고서는 atomic mode 600이고 account/order mutation 0을 명시한다.

## 실패 경계

- missing store는 새 SQLite를 만들지 않고 exit 1이다.
- public mode, symlink, hard link, schema/trigger/payload hash와 parent-child binding 변조는 query 단계에서 차단한다.
- blocked report에는 path, attempt/cycle/audit ID, symbol, 가격, credential과 raw exception을 기록하지 않는다.

## 검증

- library summary: legacy parent 1 + completed child 1을 `parent/legacy/child=2/1/1`로 재생
- non-suffix child history: summary block
- actual `--help`: exit 0
- actual missing store: exit 1, store 0, mode-600 blocked report
- actual happy path: exit 0, parent/child `1/1`, completed 1, selected/new/replay `2/1/1`
- 전체 `2560 passed`
- Ruff, basedpyright `0 errors, 0 warnings`, compileall, changed-file no-excuse 통과

## 남은 경계

- 이 CLI는 supervisor aggregate 감사만 조회하며 raw receipt와 actionability envelope의 manifest별 대사는 하지 않는다.
- 실제 열린 NYSE 정규장 smoke가 생긴 뒤 별도 cross-store verifier로 세 원장을 결합한다.
- credential·provider·network·account/order API와 broker mutation은 0건이다.
