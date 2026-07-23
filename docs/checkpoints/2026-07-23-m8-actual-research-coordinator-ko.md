# M8 실제 intraday research 운영 coordinator 체크포인트

## 닫은 결손

strict session catalog, actual READY input binding, source-backed walk-forward와 독립
Reviewer가 각각 구현돼 있었지만 운영 예약은 세 CLI와 artifact glob을 shell에서
수동 연결했다. 날짜별 runner가 SHA·foundation 경로를 잘못 전달하거나 catalog
성공 뒤 loop를 누락할 수 있었다.

`run_intraday_actual_research.py`는 다음 전체 경계를 한 명령으로 실행한다.

```text
candidate forward sessions
→ strict per-session catalog + required current session
→ cumulative causal CSV + canonical receipts
→ KIS historical entitlement + exact queue cards
→ strategy별 READY foundation + v2 manifest
→ source-backed bounded walk-forward
→ query-only independent Reviewer
```

## 불변 계약

- catalog가 minimum clean session과 모든 required session date를 통과하기 전에는
  binding output이나 strategy/trial ledger mutation을 만들지 않는다.
- binding은 catalog가 반환한 exact CSV와 canonical dataset receipt만 소비한다.
- loop는 binding이 반환한 exact v2 manifest, 같은 CSV, 모든 foundation path와 같은
  source queue artifact만 소비한다.
- strategy binding, code version, 등록시각, 비용·세션·bar·bootstrap·RSS 예산은 한
  request에서 세 단계에 동일하게 전달된다.
- exact replay는 dataset/catalog/binding artifact를 교체하지 않고 trial/review
  artifact도 늘리지 않는다.
- aggregate report는 session 수, input/manifest SHA, foundation/trial 수와 Reviewer
  decision만 기록하며 source path, 종목, 가격, 계좌 또는 자격증명을 출력하지 않는다.
- 자동 lifecycle, champion, allocation, account/order mutation은 없다.

## 검증

- actual-shaped one-session fixture first run:
  - selected session `1`
  - foundation/trial/review `1/1/1`
  - Reviewer `hold`
- exact replay:
  - catalog/binding `created=false`
  - 신규 trial/review artifact `0/0`
  - experiment trial 원장 `1`
- required current session blocked:
  - dataset/binding directory 없음
  - strategy version/trial 원장 신규 `0/0`
- CLI `--help`: 전체 session/catalog/entitlement/queue/lane/ledger 옵션 노출
- malformed strategy binding: exit `2`
- CLI happy/replay: exit `0/0`
- CSV, dataset/catalog/binding/foundation/trial/review/report: 모두 mode `600`
- 관련 회귀: `32 passed`
- 전체 pytest: `3413 passed`
- 전체 Ruff, basedpyright `0 errors, 0 warnings`, compileall, diff check 통과
- provider, credential, account, broker와 order mutation: `0`

이 coordinator의 fixture `hold`는 실제 성과가 아니다. 예약된 clean forward session이
끝나면 같은 CLI를 actual mode-600 entitlement와 세 queue card에 적용해 실제 CSV
SHA, 세 READY foundation, v2 manifest와 Reviewer 결과를 발행한다.
