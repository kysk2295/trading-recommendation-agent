# KR Theme Keyword Baseline·Opportunity Projection 설계

## 1. 범위

이 설계는 다중 시장 Research OS Milestone 3의 두 번째 로컬 세로 경로다.

```text
완전한 KR catalyst cycle
→ deterministic keyword baseline classification
→ 기존 append-only classification ledger
→ 저장된 classification + canonical volume_surge 원문 replay
→ theme freshness·dissemination·leader projection
→ kr_equities/opportunity_manager/theme_momentum OpportunitySnapshot
```

실제 뉴스·DART·KIS API, LLM, 현재 호가, KR risk gate, trade signal과 주문은 포함하지 않는다. 출력은 다음 단계의 Paper/shadow 후보 입력이며 수익성 근거가 아니다.

## 2. 채택한 접근

### 2.1 공급자 raw JSON 직접 추측 파싱 — 기각

아직 production 뉴스·DART·KIS 국내 adapter가 선택되지 않았다. 임의의 필드 이름을 넓게 순회하면 provider schema 변경과 metadata 문자열이 분류 결과를 바꿔 replay를 훼손한다.

keyword baseline은 이 milestone에서 `news`·`dart` payload의 명시적 top-level text field만 읽는다. 지원하지 않는 payload shape는 무관으로 추정하지 않고 실패한다. production adapter는 후속 단계에서 source별 extractor를 추가한다.

### 2.2 별도 unstored market-metric manifest — 기각

대장주를 외부 manifest 수치로 정하면 Opportunity을 ledger evidence만으로 재생할 수 없다. 시장 metric은 같은 catalyst cycle에 저장된 canonical internal `volume_surge` payload에서만 읽는다.

### 2.3 저장 증거 기반 pure projection — 채택

- keyword dictionary는 canonical order·명시적 version을 가진 로컬 JSON 설정이다.
- classification은 기존 `KrThemeClassification`으로 append하고 exact replay는 no-op이다.
- theme state는 저장 classification과 exact cycle의 raw evidence에서 순수 함수로 재생성한다.
- 대장주는 LLM이 아니라 관련 종목의 당일 누적 거래대금 내림차순, 동률 symbol 오름차순으로 결정한다.
- Opportunity은 theme별 하나씩 발행해 서로 다른 테마를 사후 점수로 혼합하지 않는다.

## 3. Keyword Baseline 계약

### 3.1 Rule set

각 rule은 다음을 가진다.

```text
theme_name
keywords[]
related_symbols[]: symbol, relation, rationale
```

rule과 keyword, related symbol은 canonical order·중복 없음이어야 한다. 설정은 `classifier_version`과 `prompt_version=no-prompt-v1`을 가진다. 실제 rule 파일은 Git artifact로 보존하고 분류 행의 version과 대응한다.

### 3.2 Text extraction

- 대상 source: `news`, `dart`
- 대상 content type: `application/json`
- 허용 top-level string field: `title`, `body`, `summary`, `report_name`, `company_name`
- field 순서는 위 목록으로 고정한다.
- 빈 문자열, nested object 임의 순회, control character와 지원하지 않는 shape는 거부한다.
- raw byte와 전체 text는 repr·CLI·report·outbox에 출력하지 않는다.

### 3.3 Classification

- Unicode `casefold()` 뒤 literal substring match만 사용한다.
- 한 theme의 keyword가 하나 이상 일치하면 그 theme의 positive classification을 만든다.
- 두 theme 이상이 일치하면 lexicographic tie-break를 하지 않고 ambiguous로 실패한다.
- 일치 theme가 없으면 irrelevant classification을 append한다.
- evidence quote는 일치 field의 최대 200자 또는 첫 지원 text field의 최대 200자다.
- confidence는 규칙 실행 결과의 deterministic 값이며 예측 확률이 아니다: positive/irrelevant 모두 `1`을 저장한다.
- `classification_run_id`와 `classified_at`은 run manifest에서 고정해 재시작 idempotence를 보장한다.

## 4. Canonical Volume-Surge Evidence

`KrVolumeSurgePayload` schema v1은 다음만 허용한다.

```text
observed_at
symbols[]:
  symbol
  trading_value_krw
  volume_ratio
```

symbol은 6자리, 금액과 ratio는 finite nonnegative Decimal이다. symbol은 canonical order·중복 없음이어야 한다. payload는 exact cycle의 `volume_surge` catalyst raw BLOB이어야 하고 stored SHA-256을 통과해야 한다. payload 관측시각은 catalyst 최초 관측시각 및 cycle observation과 일치해야 한다.

이 payload는 production KIS raw 응답이 아니라 후속 read-only adapter가 raw-first KIS evidence에서 만드는 명시적 내부 파생 catalyst다.

## 5. Theme State Projection

projection은 exact `collection_cycle_id`, classifier kind/version, prompt version과 run ID를 입력으로 받는다.

필수 조건:

1. cycle이 존재하고 네 source가 모두 success다.
2. cycle의 모든 `news`·`dart` catalyst에 exact cohort classification이 정확히 하나 있다.
3. classification 시각과 volume metric 시각이 `projected_at`보다 미래가 아니다.
4. positive theme의 모든 related symbol에 volume metric이 정확히 하나 있다.
5. 다른 classifier version/run의 행을 섞지 않는다.

theme별 상태:

```text
theme_name
classifier cohort
first_observed_at
latest_observed_at
projected_at
freshness_seconds
catalyst_count
publisher_count
related symbols + trading_value_krw + volume_ratio
total_trading_value_krw
leader_symbol
classification_ids
market_catalyst_ids
```

`catalyst_count`, `publisher_count`, 관련 종목 수와 total trading value를 원시 component로 보존한다. 임의 가중치의 단일 theme strength 점수는 만들지 않는다.

## 6. KR Opportunity Projection

- lane: `kr_equities/opportunity_manager/theme_momentum`
- producer version: run manifest에 고정
- snapshot: theme 하나당 하나
- candidate order: `trading_value_krw DESC`, `symbol ASC`
- candidate score: 해당 종목 `trading_value_krw`
- rank 1은 규칙 기반 leader다.
- evidence: cycle ID, exact classification IDs, volume-surge catalyst IDs만 canonical key로 참조한다.
- source coverage: final cycle의 네 source를 공통 `SourceCoverage`로 투영한다.
- validity: `projected_at` 이후 명시적 짧은 duration, fixture 기본 10분

같은 symbol이 여러 theme에 있어도 theme별 Opportunity은 별도 가설 입력으로 유지한다. 이 milestone은 theme 간 Opportunity을 하나로 합치지 않는다.

## 7. CLI와 출력

`run_kr_theme_projection.py`는 다음 explicit path만 받는다.

- `--database`: 기존 mode-600 KR ledger
- `--run-manifest`: cycle, rule path, cohort, classified/projected time, validity, producer version
- `--output-dir`: Opportunity JSONL과 aggregate 한국어 report

run manifest와 rule path는 manifest directory 아래 relative regular file만 허용한다. CLI는 모든 설정·raw·classification·projection을 먼저 검증하고, 이후 한 writer lease에서 classification만 append한다. Opportunity outbox는 기존 immutable contract outbox를 사용한다.

보고서는 cycle, classifier version, theme 수, theme 이름, leader symbol, freshness, component count만 담는다. raw title/body, evidence quote, source record ID, hash와 자격증명은 출력하지 않는다.

## 8. 실패와 재시작

- incomplete cycle, missing/duplicate classification coverage, ambiguous keyword, unsupported payload, missing/duplicate volume metric, future evidence와 outbox conflict는 fail-closed다.
- 분류 전 검증 실패는 ledger와 outbox를 만들지 않는다.
- DB classification append 뒤 outbox 쓰기가 실패하면 재실행이 exact classification no-op 후 outbox를 복구한다.
- 같은 Opportunity ID와 같은 payload는 no-op, 다른 payload는 conflict다.

## 9. 검증

- contract tests: rule canonicality, extractor strictness, ambiguity, irrelevant, raw repr redaction
- projection tests: exact cycle/cohort coverage, no version mixing, freshness, leader tie-break, missing metric, causal time
- CLI E2E: ingest fixture → projection → classification ledger + Opportunity JSONL + Korean report
- restart E2E: classification/outbox duplicate 없음
- incomplete cycle와 invalid path는 writer/openbox 생성 전 차단
- 전체 pytest, Ruff, basedpyright, CLI help·bad input·happy path
- 외부 network·LLM·broker mutation 0건 확인

## 10. 후속 범위

- production read-only news·DART·KIS domestic adapter와 source-specific extractor
- configured LLM classification adapter와 keyword stability comparison
- human audit sample
- KR quote/VI/price-limit/warning/halt gates
- day shadow TradeSignal과 outcome evaluation

국내 계좌·잔고·포지션·주문 endpoint는 계속 추가하지 않는다.
