# KR theme day session source attestation 체크포인트

## 문제

phase audit의 `exit_code=0`은 process가 성공했다고 보고했다는 증거이지, trial/raw/entry/exit/terminal/Reviewer/lifecycle source가 현재도 그 성공을 뒷받침한다는 증거는 아니다. audit-only row를 완료로 재사용하면 source가 없는 forged history와 child 성공 뒤 추가된 evidence를 놓칠 수 있다.

## Source projection

supervisor는 completed phase를 재사용하기 전 기존 private reader로 다음 identity를 다시 계산한다.

- register: exact daily trial registration key
- start: registration과 sequence-1 started event key
- intraday/EOD collect: 해당 cycle의 required latest receipt kind, received time과 raw payload SHA-256
- entry: trial/start/raw refs, trial-bound entry IDs와 `entry-count:0` marker
- exit: entry refs, trial-bound exit IDs와 `exit-count:0` marker
- post-session: sequence-1/2 trial event, terminal artifact, exact session review와 lifecycle event keys

정렬된 reference tuple은 SHA-256과 reference count로 축약한다. source reader가 schema/hash/mode/lineage를 거부하거나 필수 reference가 없으면 state를 만들지 않는다.

## Attestation과 복구

completed audit event마다 event/session/phase/cycle과 source digest/count를 content-addressed attestation으로 저장한다. store는 audit DB와 같은 private directory의 `*-evidence.sqlite3`이며 owner mode 600 regular single-link, exact schema/index와 UPDATE/DELETE trigger를 요구한다.

같은 phase/cycle은 completed event, exact attestation과 현재 source state가 모두 일치해야 skip한다. 다음 상태는 skip하지 않는다.

- 기존 v1 audit event만 있고 attestation이 없음
- child 성공 뒤 audit/attestation 사이에서 process가 종료됨
- 같은 cycle source reference가 추가됨
- 같은 event에 다른 source digest를 결합하려 함

앞의 세 경우 child는 기존 append-only source의 exact replay 계약으로 다시 실행되고 새 event-attestation을 만든다. evidence DB 자체의 payload/schema/trigger/mode 변조는 자동 복구하지 않고 fail-closed한다.

## 검증

- focused session manifest/audit/evidence/supervisor/CLI: `14 passed`
- related KR session children: `32 passed`
- 전체 회귀: `2754 passed`
- Ruff와 format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, 신규 production no-excuse: 통과
- actual intraday subprocess: raw 3, entry 1, phase/evidence attestation 5, exact replay child 0
- no-entry daily E2E: EOD catch-up, censored terminal, Reviewer, lifecycle와 source attestation 완료
- provider credential/network와 국내 account/order mutation: `0`

## 다음 단계

열린 KRX session에서 KIS GET-only tick 한 cycle을 실행해 raw receipt, phase event와 source attestation digest를 query-only로 대사한다. 실제 증거가 통과한 뒤 private manifest와 두 supervisor store를 함께 보존하는 최소권한 launchd 반복 실행·restart soak를 추가한다.
