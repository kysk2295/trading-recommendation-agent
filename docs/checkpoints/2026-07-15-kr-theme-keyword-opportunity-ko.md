# KR Theme Keyword·Opportunity 체크포인트

## 범위

다중 시장 Research OS Milestone 3의 두 번째 local vertical을 구현했다. 완전한 KR catalyst cycle의 뉴스·DART 원문을 deterministic keyword baseline으로 분류하고, 기존 append-only classification ledger에 보존한 뒤 저장 evidence만으로 theme 상태와 대장주 Opportunity을 재생한다.

이 단계는 synthetic/local fixture만 사용했다. 실제 뉴스·DART·KIS API, LLM, 현재 호가, KR VI·가격제한·투자경고 gate, TradeSignal, shadow fill 또는 국내 계좌·주문 경로를 호출하거나 추가하지 않았다.

## Keyword Baseline

- rule set은 classifier/prompt version, canonical theme·keyword·관련 종목을 고정한다.
- `news`·`dart`의 `application/json`만 대상이며 `title`, `body`, `summary`, `report_name`, `company_name` top-level string만 고정 순서로 읽는다.
- nested/unknown metadata를 임의 순회하지 않는다.
- 한 theme 일치는 positive, 일치 없음은 irrelevant로 append한다.
- 둘 이상 theme가 일치하면 임의 tie-break 없이 fail-closed한다.
- classified time은 catalyst 최초 관측 이후여야 하고 fixed run ID/time의 재실행은 exact no-op이다.
- classifier가 raw byte SHA-256을 다시 검증하며 parse/domain error는 raw cause를 연결하지 않는다.
- raw byte와 전체 extracted text는 repr, exception, report와 Opportunity에 포함하지 않는다.

`confidence=1`은 literal rule 실행의 결정성을 뜻하며 예측 확률, 매매 신뢰도 또는 수익 가능성을 뜻하지 않는다.

## Theme·Leader Projection

- final cycle의 네 source가 모두 success일 때만 projection한다.
- cycle의 모든 news/DART catalyst에 exact keyword kind/version/prompt/run classification이 하나씩 있어야 한다.
- 다른 classifier version/run은 보존하되 projection에 섞지 않는다.
- market metric은 같은 cycle의 checksum 검증된 canonical `volume_surge` BLOB에서만 읽는다.
- 관련 종목마다 metric 하나를 요구하고 missing/duplicate metric은 차단한다.
- theme freshness, catalyst count, publisher count, 관련 종목 수와 total trading value를 raw component로 보존한다.
- 대장주는 `trading_value_krw DESC`, 동률 `symbol ASC` 규칙으로만 정한다.
- arbitrary weighted theme strength와 LLM leader 판단은 없다.
- observation이 cycle window 밖이거나 classification/metric이 projection 시각보다 미래이면 차단한다.

## KR Opportunity

- lane: `kr_equities/opportunity_manager/theme_momentum`
- theme 하나당 OpportunitySnapshot 하나
- candidate score: 해당 symbol의 저장된 `trading_value_krw`
- rank 1: 규칙 기반 leader
- evidence: collection cycle, exact classification ID, volume-surge catalyst ID
- source coverage: final cycle 네 source의 exact record count
- Opportunity ID: projection producer version을 포함해 새 알고리즘 version을 별도 snapshot으로 보존
- output: immutable `opportunities.v1.jsonl`과 aggregate 한국어 보고서

같은 symbol이 여러 theme에 속하더라도 이 단계는 theme 결과를 사후 혼합하지 않는다. Opportunity은 종목 발굴 결과이며 현재 진입 가능 신호나 주문권한이 아니다.

## CLI QA

```bash
./run_kr_theme_ingest.py \
  --manifest examples/kr_theme_projection/ingest-manifest.json \
  --database <temporary-db> \
  --output-dir <temporary-ingest-output>

./run_kr_theme_projection.py \
  --run-manifest examples/kr_theme_projection/projection-run.json \
  --database <temporary-db> \
  --output-dir <temporary-projection-output>
```

- ingest: catalyst 3, observation 3, complete cycle 1
- first projection: classification 1, theme Opportunity 1
- exact restart: 신규 classification 0, 신규 Opportunity 0
- DB mode: `600`
- DB rows: catalyst 3, observation 3, cycle 1, classification 1
- Opportunity JSONL: 1행 유지
- report: raw title/body, source record ID, DB path, 64자리 hash 비노출
- missing run manifest: DB/output 생성 전 exit 2

## 검증

- 새 keyword/projection/manifest/CLI focused: `44 passed`
- 전체 pytest: `1086 passed`
- Ruff 전체: 통과
- basedpyright 전체: 오류 0, 경고 0
- 외부 network·LLM 호출: 0건
- broker·계좌·주문 mutation: 0건

synthetic rule, theme와 trading value는 계약·replay QA용이며 한국장 분류 정확도, 실시간 추천 또는 수익성 증거가 아니다.

## 커밋

- `da546ba feat: add KR keyword theme baseline`
- `125c626 fix: verify KR keyword catalyst payloads`
- `eb47747 feat: project stored KR themes to opportunities`
- `c90df8a fix: redact KR keyword parse causes`
- `1c5dba5 feat: publish local KR theme opportunities`
- `3088d32 fix: version KR opportunity identities`

## 다음 단계

1. production read-only news·DART·KIS domestic collector와 source-specific extractor
2. configured LLM classifier, repeated-run stability, keyword baseline comparison과 human audit sample
3. current KR quote·VI·가격제한·투자경고·거래정지·동시호가 freshness gate
4. theme Opportunity을 소비하는 KR day shadow TradeSignal과 conservative fill outcome

국내 account, balance, position, order, execution endpoint는 계속 범위 밖이다.
