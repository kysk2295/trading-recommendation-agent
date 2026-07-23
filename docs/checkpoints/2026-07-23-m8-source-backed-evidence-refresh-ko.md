# M8 source-backed 고정 전략 증거 갱신 체크포인트

## 닫은 결손

최초 source-backed walk-forward가 끝나면 queue route가 `strategy_design`에서
`independent_review`로 바뀐다. 기존 actual input binding과 strategy registration은
`strategy_design`만 허용했기 때문에 이후 clean forward session이 누적돼도 같은 고정
전략 버전에 새 data evidence를 추가할 수 없었다.

이제 다음 조건을 모두 만족할 때만 기존 strategy version으로 새 historical trial과
독립 Reviewer artifact를 추가한다.

- 실행에 전달한 queue artifact가 현재 experiment ledger에서 투영한 exact 최신
  snapshot이다.
- queue item이 정확히 같은 단일 strategy version을 가리킨다.
- route가 `historical_replay`, `independent_review`, `recovery` 중 하나다.
- 새 causal dataset의 exact `input_sha256`이 그 전략 버전의 기존 completed trial에
  사용되지 않았다.
- 기존 immutable strategy registration의 hypothesis, parameter, data/cost/portfolio
  계약과 code version을 그대로 유지한다.

## 차단 계약

- 동일 data SHA를 새 `registered_at`으로 다시 trial화하지 않는다.
- 이전 queue snapshot으로 새 data trial을 만들지 않는다.
- 같은 card 아래 새 strategy version을 암묵적으로 만들지 않는다.
- queue가 `active_research`이거나 다른·복수 strategy version을 가리키면 차단한다.
- 최초 실행과 그 exact replay는 기존 content-addressed idempotency를 유지한다.
- lifecycle, champion, allocation, broker/account/order mutation은 수행하지 않는다.

## 검증

- actual coordinator 회귀:
  - 1세션 최초 실행: strategy version/trial `1/1`
  - 최신 queue + 같은 data + 새 등록시각: 차단, trial `1`
  - 2세션 누적 + 최신 queue + 같은 고정 version: 신규 trial/review `1/1`
  - 최종 strategy version/trial `1/2`
- 수동 CLI:
  - `--help`: required date, queue, strategy binding 옵션 노출
  - malformed strategy binding: exit `2`
  - first/duplicate-data/refresh: exit `0/1/0`
  - refresh selected sessions `2`, blocked sessions `0`
  - refresh exact replay: exit `0`, 신규 trial/review `0/0`
  - publication artifact 전체 mode `600`
- 관련 테스트: `19 passed`
- Ruff와 basedpyright: `0 errors, 0 warnings`
- provider, credential, account, broker와 order mutation: `0`

이 갱신 경로는 성과를 만들어 내는 장치가 아니라 서로 다른 causal data SHA에서 같은
immutable 전략 버전을 반복 평가하는 증거 축이다. 실제 성과 판정은 예약된 clean
forward session과 동일한 coordinator/Reviewer 표면에서 별도로 축적한다.
