# KR theme day trial terminal 체크포인트

## 완료 계약

- KST 15:30 전에는 terminal artifact와 sequence 2 event를 만들지 않는다.
- exact KR day trial registration과 sequence 1 `started` key를 다시 계산해 모든 entry의 binding과 대사한다.
- entry·exit store는 query-only로 전체 schema, trigger, payload hash, mode 600과 single-link를 먼저 검증한다.
- 모든 exact-trial entry가 exact exit와 1:1일 때만 `completed`다.
- entry 0건은 `no_shadow_entry_artifact`, missing exit는 `incomplete_shadow_exit_path`로 `censored`되어 성과 0에 포함되지 않는다.
- invalid source store와 registration/start/signal/가격 계보 불일치는 `failed`다.

## Artifact와 재시작

daily artifact는 trial, strategy, session, started event key, terminal kind/reason과 ordered entry·exit ID·canonical payload SHA를 content address로 보존한다. private append-only SQLite는 trial당 하나만 허용하며 exact replay는 artifact와 ledger event 모두 append 0건이다. artifact 저장 뒤 ledger writer가 중단된 경우 같은 입력의 재시작이 sequence 2 append를 복구한다.

## 검증

- focused terminal/trial/entry/exit: `20 passed`
- 전체 회귀: `2685 passed`
- minimal driver: `completed`, artifact/event first `true/true`, replay `false/false`, ledger event `2`, mode `600`
- Ruff, format, basedpyright, compileall, no-excuse: 통과
- provider, credential, account/order mutation: `0`

## 다음 단계

독립 Reviewer는 completed terminal artifact만 성과 표본으로 읽고 censored/failed 비율을 별도 품질 gate로 평가한다. 일일 source cycle의 no-signal attestation이 연결되기 전에는 entry 0건을 성공 표본으로 재분류하지 않는다. 이후 KR lane lifecycle shadow evidence와 multi-session promotion eligibility를 연결한다.
