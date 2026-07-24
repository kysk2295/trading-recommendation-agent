# M8 intraday 인접 파라미터 plateau 증거 체크포인트

## 제품 결과

source-backed actual research의 exact walk-forward 결과와 동일한 causal CSV,
OOS session, evaluator, 편도 비용을 사용해 각 전략의 중심 파라미터와 한 축 인접값을
재평가하는 terminal evidence를 추가했다.

```text
completed source-backed trial
→ frozen StrategyVersionRegistration.parameter_set 대사
→ center + 6 predeclared one-axis neighbors
→ same OOS sessions / cost / bootstrap evaluator
→ collecting | plateau_ready | plateau_not_found
→ content-addressed mode-600 terminal artifact
```

인접값은 actual 결과를 본 뒤 생성하지 않는다. VWAP reclaim, HOD breakout,
Gap-and-Go마다 중심 1개와 사전에 고정된 양방향 3축, 총 7개만 사용한다. ORB는 기존
81-grid flatness 경로에 남고 source-backed v2 plateau bundle에는 포함되지 않는다.

## 판정 계약

- 중심값은 원 completed experiment의 immutable schema-v2 session outcome trace다.
- 6개 인접값만 같은 bounded walk-forward evaluator로 순차 재평가한다.
- strategy registration의 전체 parameter set이 현재 frozen template와 정확히 같아야
  한다.
- 모든 variant는 같은 session date sequence를 가져야 한다.
- 중심은 최소 20 OOS session·30 trades, 인접값은 최소 30 trades인 값이 4개 이상
  생기기 전까지 `collecting`이다.
- 성숙 뒤 중심 평균이 양수이고 적격 인접값의 75% 이상이 양수이며 최솟값도 양수일
  때만 `plateau_ready`다.
- 성숙한 인접값 하나라도 방향을 뒤집어 위 조건을 깨면 `plateau_not_found`다.
- 여러 전략 중 하나가 `plateau_not_found`이면 다른 전략이 아직 `collecting`이어도
  terminal aggregate는 실패를 숨기지 않는다.
- 결과는 lifecycle, Paper, 주문 또는 allocation 권한을 변경하지 않는다.

terminal actual audit은 plateau artifact ID와 상태를 기존 exact dataset, READY
foundation, trial/review, equal-risk comparison, DSR/PBO chain과 같은 immutable
artifact에 결속한다. empirical 재평가는 기존 heavy-process lease를 공유하므로 동시에
두 개의 무거운 연구 작업을 실행하지 않는다.

## TDD와 수동 CLI QA

- fail-first: plateau module 부재
- fail-first: walk-forward request가 predeclared variant를 받지 않음
- fail-first: terminal audit에 plateau artifact가 없음
- fail-first: mature 실패가 `collecting` aggregate에 가려짐
- unit/integration/terminal focused: `51 passed`
- 전체 pytest: `3600 passed`
- Ruff 전체: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- no-excuse audit: `0`
- CLI help/bad/happy: `0/1/0`
- happy result: `parameter plateau: collecting`
- artifact/report mode: `600`
- external mutation: `0`

fixture는 한 OOS session뿐이므로 `collecting`이며 성과나 plateau 통과 근거가 아니다.

## 실제 후속 예약

기능 commit은
`2ae1a10db664ff9d2a61ca7f75f3423ae512f760`이고 `origin/main`에 push했다.
해당 exact detached runtime은
`/private/tmp/trading-agent-parameter-plateau-2ae1a10`이다.

기존 cumulative research와 schema-v3 audit을 변경하지 않고, 그 성공 receipt 뒤에만
실행되는 별도 at-most-once terminal audit을 추가했다.

| Session | 실행 시각 (KST) | Label | 등록 PID |
|---|---:|---|---:|
| 2026-07-24 | 2026-07-25 07:05 | `ai.trading-agent.actual-research-parameter-plateau-v1-audit-20260724` | 29415 |
| 2026-07-27 | 2026-07-28 06:40 | `ai.trading-agent.actual-research-parameter-plateau-v1-audit-20260727` | 29416 |

runner/payload는 mode `700`, stdout/stderr는 mode `600`·0바이트이며 아직 receipt와
claim은 없다. 예약은 성공 증거가 아니다. upstream research 또는 기존 terminal audit
receipt가 nonzero면 plateau job도 fail-closed하고 그 결과를 receipt에 보존한다.
실제 clean session이 쌓여 20-session/30-trade 문턱을 넘기 전에는
`plateau_ready`를 주장하지 않는다.
