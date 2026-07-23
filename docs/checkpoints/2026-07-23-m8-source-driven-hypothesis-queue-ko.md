# M8 source-driven hypothesis queue 체크포인트

## 제품 결과

기존 전역 experiment ledger의 immutable `ResearchSource → ResearchHypothesisCard`를
새 저장소나 daemon 없이 query-only로 읽어, 다음 연구 작업을 deterministic queue로
투영하는 첫 M8 수직축을 추가했다.

```text
source-backed hypothesis card
→ evidence quality gate
→ latest strategy version identity
→ latest version historical trial state
→ content-addressed next-work queue
```

큐 artifact는 가설, 반증 규칙, 경제적 기제, counterfactual, source key/kind, 등록된
strategy version과 현재 version의 historical trial을 결박한다. lifecycle, allocation과
order authority는 model에서 모두 literal `false`다.

## 출처 정책

기존 논문·공식 시장 규칙·공식 provider 문서·내부 관찰에 다음 discovery 종류를
추가했다.

- `open_source_repository`: GitHub 등 공개 구현 발견
- `news_article`: 연구 아이디어나 시장 구조 변화 발견
- `social_discussion`: Reddit/X 등 공개 토론 발견

discovery 종류만 있는 가설은 source 수나 인기도와 무관하게 `evidence_review`에 남는다.
논문·공식 문서 또는 명시적 내부 관찰이 하나 이상 결박돼야 `strategy_design` 이후로
갈 수 있다. 이는 소셜 주장이나 공개 코드 수익률을 검증된 전략 성과로 오인하지 않기
위한 경계다.

기존 `research_sources` CHECK를 다시 쓰지 않았다. schema v7은 discovery source 전용
append-only table과 no-update/no-delete trigger만 추가하며 v1~v6 migration에서 기존
행을 그대로 보존한다. source ID와 key 충돌 검사는 두 source table을 하나의 전역
namespace로 취급한다.

## Queue routing

- `evidence_review`: discovery-only source를 독립 근거 검토로 보냄
- `strategy_design`: source-backed card는 있지만 strategy version이 없음
- `historical_replay`: 최신 strategy version에 historical trial이 없음
- `active_research`: 최신 version의 historical trial이 등록됐거나 실행 중
- `independent_review`: 최신 version trial이 completed artifact로 종료됨
- `recovery`: 최신 version trial이 failed/censored로 종료됨

과거 version의 completed trial은 새 version에 재사용하지 않는다. 새 version이 등록되면
그 version 자체의 historical evidence가 없으므로 다시 `historical_replay`로 간다.

## CLI와 QA

`run_source_driven_hypothesis_queue.py`는 `--database`, `--artifact-root`,
`--output-dir`만 받는다. credential, provider, broker, account, Paper와 주문 module을
import하지 않는다. report에는 queue route별 개수만 쓰고 source URL, 가설 내용, 경로와
artifact ID를 쓰지 않는다.

- CLI help: exit `0`
- missing ledger: exit `1`, artifact root 미생성
- source-backed 논문 manifest happy/replay: exit `0/0`
- replay artifact: 정확히 `1`개, 신규/재사용 `0/1`
- ledger, artifact와 report mode: `600`
- ledger/queue focused regression: `150 passed`
- full pytest: `3382 passed`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- lifecycle/allocation/order authority: `false/false/false`
- provider request와 broker/account/order mutation: `0`

현재 private 운영 outputs의 experiment ledger는 schema v6이며 source-backed hypothesis
card가 0개라 production queue item은 아직 없다. 이 단계는 GitHub/Reddit/X connector,
자동 원문 수집, LLM 전략 생성, strategy patch, PR, backtest 실행이나 lifecycle 승격을
구현하지 않는다. 다음 M8 단계는 승인된 source ingestion에서 이 큐로 card를 넣고,
`strategy_design` item을 테스트된 immutable version과 기존 historical research loop에
연결하는 것이다.
