# KR theme day shadow exit/PnL 체크포인트

## 인과성

entry가 포함된 1분봉은 사용하지 않는다. `filled_at`이 minute boundary면 그 시각의 봉부터, 그 외에는 다음 minute부터 시작하고 모든 봉은 같은 symbol·KST date에서 공백 없이 이어져야 한다. bar observation은 `evaluated_at` 이후일 수 없다.

## 체결 규칙

1. 같은 봉에서 stop과 first target이 모두 닿으면 stop을 먼저 적용한다.
2. stop trigger, first target trigger와 15:30 close에 매도 방향 20bp adverse slippage를 적용한다.
3. stop/target이 없고 마지막 봉이 15:30에 끝날 때만 `time_exit`를 만든다.
4. 불완전 path는 `None`이며 exit store를 만들지 않는다.

artifact는 entry/trial/signal identity, trigger·exit 가격, net return, realized R, 사용한 bar evidence ID와 canonical SHA를 보존한다.

## 검증

- focused exit/entry/trial: `25 passed`
- 전체 회귀: `2679 passed`
- minimal driver: same-bar stop-first, exit `9780.400`, first append/replay `true/false`, row `1`, mode `600`, authority field `0`
- Ruff, changed-file format, basedpyright, compileall, no-excuse: 통과
- provider, credential, account/order mutation: `0`

## 다음 단계

entry와 exit store를 query-only로 완전 재생해 artifact SHA를 만든 뒤 global multi-market trial의 sequence 2 `completed` terminal에 연결한다. missing exit, duplicate signal, incomplete session, store tamper는 `censored` 또는 `failed` reason으로 명시하고 독립 Reviewer는 terminal 이후 artifact만 읽는다.
