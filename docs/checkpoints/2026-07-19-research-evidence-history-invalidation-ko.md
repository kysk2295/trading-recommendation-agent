# Research evidence history invalidation 체크포인트

## 완성 범위

- 공통 research evidence read-model 입력의 canonical event chain을 먼저 검증한다.
- original→correction→tombstone 순서, active target, source/provider/entity identity와 시각 단조성을 재사용한다.
- `as_of` 시점의 active event set에 결합된 extraction만 claim 계산에 허용한다.
- correction은 successor event의 content hash·raw receipt에 결합된 새 extraction을 요구한다.
- 미래 correction은 효력 발생 전 projection과 source event count에서 제외한다.

## immutable invalidation 의미

- 과거 extraction과 artifact를 삭제하거나 덮어쓰지 않는다.
- superseded extraction을 새로운 as-of read model에 넣으면 fail-closed다.
- tombstone 뒤 active event가 없으면 과거 extraction으로 claim을 재생할 수 없다.
- 호출자는 해당 source의 complete correction history scope를 제공해야 한다.
- provider가 아직 수집되지 않은 correction이나 deletion을 kernel이 추측하지 않는다.

## 검증

- focused history/read-model/adapters: 36 passed
- canonical history/read-model regression: 25 passed
- full repository: 2325 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 수동 library QA

- superseded original extraction: `stale=blocked`
- correction-bound 새 extraction: `active_claims=1 observed_events=2`
- provider·credential·account/order endpoint와 broker mutation: 0건

## 다음 단계

- provider별 deletion cursor와 retention 이행 증거
- source별 complete-history coverage 계약
- 다음 열린 NYSE 정규장의 bounded SIP GET smoke
