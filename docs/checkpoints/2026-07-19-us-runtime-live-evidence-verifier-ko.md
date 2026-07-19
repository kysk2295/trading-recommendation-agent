# US runtime live evidence cross-store verifier 체크포인트

## 완료 범위

- supervisor parent와 live child 전체 history를 기존 reader로 먼저 검증한다.
- completed child마다 parent 시작시각의 exact content-addressed manifest를 선택한다.
- actionability terminal key를 원래 source manifest identity와 digest receipt에 연결한다.
- bounded-complete terminal의 plan, connection epoch와 시각을 artifact bundle과 대사한다.
- current minute snapshot identity가 source minute와 달라져도 replay artifact의 원래 identity를 보존한다.

## Fail-closed 경계

- created receipt 삭제 또는 mode `0644`
- receipt root의 unknown entry, 잘못된 lock metadata와 manifest digest 불일치
- 중복 base+scan artifact 또는 current manifest key
- child selected count와 exact manifest 수 불일치
- child new/replay 분할과 terminal 시각 분류 불일치
- raw exception, ID, symbol, 가격과 내부 경로의 report 노출

## CLI와 검증

- `run_us_runtime_live_evidence_verify.py`는 supervisor/manifest/receipt/actionability/output 경로만 받는다.
- 실제 help: exit `0`
- missing input: exit `1`, input store 생성 `0`, mode-600 blocked report
- 2분 fixture happy: completed/selected `2/2`, created/replay/artifact `1/1/1`
- 관련 runtime 회귀: `31 passed`
- 전체 회귀: `2570 passed`
- Ruff, basedpyright `0 errors, 0 warnings`, compileall, changed-file no-excuse 통과
- provider·credential·account/order/position endpoint와 broker mutation: `0`

## 남은 계약

- terminal이 선행 crash 시도에서 완료되고 artifact append만 현재 재시작에서 성공하면 v1 store만으로 append 시도를 독립 복원할 수 없다.
- 다음 마일스톤은 manifest/terminal/artifact projection attempt를 append-only identity로 보존해 이 crash window의 new/replay도 구조적으로 증명하는 것이다.
