# KR theme day conservative shadow entry 체크포인트

## 계약

- exact code-coupled `theme_leader_vwap_reclaim` strategy와 같은 KST session의 started trial이 필요하다.
- signal은 long, limit, `CURRENT_QUOTE_VALIDATED`이며 entry가 current ask와 같아야 한다.
- `filled_at`은 signal과 quote validity 안에 있어야 한다.
- fill price는 `ask * 1.002`로 고정한다.
- fill은 stop보다 높고 모든 preregistered target보다 낮아야 한다.
- trial registration key, started event key, canonical signal SHA와 evidence ID를 보존한다.

## 저장과 권한

schema v1 private SQLite는 signal당 하나의 immutable entry를 보존한다. exact replay는 no-op이며 schema/trigger/payload/hash, symlink, owner, mode 600 또는 hard-link 계약이 다르면 fail-closed한다. entry에는 quantity, notional, account, broker ID와 order authority가 없다.

## 검증

- focused entry/trial: `21 passed`
- 전체 회귀: `2673 passed`
- minimal library driver: entry `1`, replay `0`, ask `10000`, fill `10020.000`, slippage `20bp`, mode `600`
- Ruff, changed-file format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, no-excuse, `git diff --check`: 통과
- external/provider/broker mutation: `0`

## 다음 단계

entry 이후에 시작하는 완료 1분봉만 사용해 stop-first same-bar ambiguity, first target과 15:30 fallback을 평가하는 immutable exit/PnL artifact를 추가한다. entry 전 구간이 섞인 봉은 사용하지 않으며, 완전한 exit artifact가 생긴 날만 trial terminal과 Reviewer 입력으로 닫는다.
