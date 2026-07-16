# KR Volume Surge V2 파생 source 설계

## 1. 범위

이 단계는 같은 KR collection cycle에 이미 확정된 `kis_ranking` evidence만 읽어 canonical `volume_surge` catalyst와 독립 terminal source run을 만든다.

```text
terminal kis_ranking source run
-> stored canonical volume-ranking catalyst + receipt lineage
-> deterministic volume ratio derivation
-> schema v2 volume_surge catalyst
-> terminal volume_surge source run
```

KIS 재조회, credential, token, network, 현재 호가, 분봉, VI, TradeSignal, 외부 메시지, 계좌·잔고·포지션·주문은 포함하지 않는다. 결과는 종목 발굴용 시장 evidence이며 추천이나 수익성 근거가 아니다.

## 2. 채택한 접근

### 2.1 기존 schema v1 의미를 넓히기 - 기각

현재 `KrVolumeSurgePayload` v1과 KR related-symbol 계약은 숫자 6자리만 허용한다. 같은 schema version에서 `[0-9A-Z]{6}`로 허용 범위를 넓히면 이미 저장된 계약의 의미가 바뀌고 replay 감사에서 어느 규칙이 적용됐는지 구분할 수 없다.

v1 model과 raw BLOB parsing은 그대로 유지한다. 새 파생 결과는 명시적 schema v2 model만 사용한다.

### 2.2 provider `vol_inrt`를 그대로 ratio로 사용 - 기각

provider 필드명은 증가율인지 배수인지 해석 여지가 있고 기존 synthetic canonical 예시는 `accumulated_volume / average_volume`과 일치한다. provider percent를 임의 변환하지 않는다.

### 2.3 저장된 KIS row에서 pure derivation - 채택

- 모든 입력은 같은 cycle의 immutable `kis_ranking` catalyst와 receipt lineage다.
- 거래량 ranking 행을 임의 threshold로 버리지 않는다.
- `volume_ratio = accumulated_volume / average_volume`을 고정 Decimal context로 계산한다.
- 각 v2 metric은 원본 KIS catalyst ID를 직접 가진다.
- 파생 가능 시각과 upstream 수신 시각을 분리해 인과성을 보존한다.
- 결과와 terminal run은 append-only이며 exact replay는 no-op다.

## 3. KR Instrument Symbol V2

공통 local contract는 두 버전을 구분한다.

```text
v1: [0-9]{6}
v2: [0-9A-Z]{6}
```

v2는 숫자 symbol을 계속 허용하고 대문자 영숫자 KIS 단축코드를 추가한다. lowercase, 공백, 구분자, 제어문자와 6자리가 아닌 값은 거부한다. 이 milestone은 volume-surge source evidence에 v2를 적용한다. 기존 v1 keyword rule, `KrRelatedSymbol`, KR TradeSignal 계약은 조용히 넓히지 않으며 후속 명시적 schema upgrade 전까지 숫자 symbol만 소비한다.

## 4. Volume Surge Payload V2

기존 `KrVolumeSurgeSymbol`과 `KrVolumeSurgePayload`는 v1 replay용으로 유지한다. 새 model은 별도 이름과 schema version을 가진다.

```text
KrVolumeSurgeSymbolV2
  schema_version = 2
  symbol: [0-9A-Z]{6}
  trading_value_krw: finite nonnegative Decimal
  volume_ratio: finite nonnegative Decimal
  source_catalyst_id: SHA-256 ID

KrVolumeSurgePayloadV2
  schema_version = 2
  observed_at: 파생 결과가 처음 사용 가능해진 timezone-aware 시각
  source_observed_at: 모든 사용 KIS volume receipt가 도착한 timezone-aware 시각
  source_run_id: exact `<collection_cycle_id>:kis_ranking`
  symbols[]: symbol 오름차순, symbol·source catalyst 중복 없음
```

zero-row KIS volume success는 빈 `symbols` v2 payload 하나로 보존한다. 이는 source가 성공적으로 빈 결과를 관측했다는 evidence이며 관련 symbol metric이 필요한 Opportunity projection은 계속 fail-closed한다.

canonical bytes는 Pydantic JSON mode, UTF-8, sorted keys, compact separators를 사용한다. v2 parser는 schema version을 명시적으로 분기하며 v1 payload를 추측으로 v2로 승격하지 않는다.

## 5. Upstream Evidence 계약

파생기는 먼저 exact source run을 검증한다.

- source run ID: `<cycle>:kis_ranking`
- source: `kis_ranking`
- adapter: `kis-kr-ranking-v1`
- collection date: CLI 입력과 일치
- status: success일 때만 payload 파생
- `record_count`: 같은 cycle KIS observation 수와 일치
- receipt ID: run의 exact receipt 집합과 store receipt가 일치

각 KIS catalyst는 다음을 만족해야 한다.

- source가 `kis_ranking`
- same-cycle observation이 정확히 하나
- observation-receipt lineage가 정확히 하나
- lineage receipt가 upstream run에 포함
- stored payload checksum이 record 및 lineage checksum과 일치
- strict `KisKrRankingItem` parsing 성공

`ranking_kind=volume` 행만 metric으로 사용한다. 각 행은 `average_volume > 0`, non-null `accumulated_trading_value_krw`를 가져야 한다. symbol 또는 source catalyst 중복, malformed row, lineage mismatch와 0 average는 전체 volume source run을 실패시킨다. 행을 조용히 폐기하지 않는다.

`source_observed_at`은 사용된 모든 volume receipt의 최대 `received_at`이다. volume 행이 0개여도 upstream run의 volume request-key receipt가 하나 이상 있어야 한다. `observed_at`은 파생기 clock이며 `source_observed_at`과 upstream run completion보다 빠를 수 없다.

ratio 계산은 precision 28, `ROUND_HALF_EVEN`의 local Decimal context를 사용한다. process-global Decimal context에 의존하지 않는다.

## 6. Append-only 상태기계

```text
validate safe cycle/date and existing DB
-> exact terminal volume_surge run local replay
-> validate terminal kis_ranking run
-> validate all same-cycle KIS evidence and lineage
-> build canonical v2 payload
-> append derived catalyst + same-cycle observation
-> append terminal volume_surge source run
```

volume source run ID는 `<cycle>:volume_surge`, adapter version은 `kis-ranking-volume-surge-v2`다. catalyst source record ID는 `volume-surge://<cycle>/schema-v2`, publisher는 `derived_kis_ranking`이며 `published_at=None`이다. catalyst `first_observed_at`, observation, payload `observed_at`과 source run start/completion은 같은 파생 시각이다.

기존 terminal volume run이 exact ID/version/date이면 evidence, clock과 writer를 다시 열지 않고 aggregate replay 결과를 반환한다. terminal run 없이 deterministic v2 catalyst만 남은 crash restart는 같은 upstream evidence에서 payload를 다시 계산하고 exact append no-op 뒤 terminal run을 복구한다. 다른 payload나 incompatible run은 conflict로 실패한다.

upstream run이 terminal failed이면 payload를 만들지 않고 `upstream_kis_failed` volume source run을 append한다. upstream run이 아직 없으면 나중에 같은 cycle로 재시도할 수 있도록 아무 volume run도 쓰지 않고 `source_not_ready`로 종료한다. malformed local ledger와 writer conflict는 실패 run으로 덮지 않고 store error를 그대로 fail-closed한다.

## 7. CLI와 보고서

`run_kr_volume_surge_derive.py`는 다음 네 옵션만 노출한다.

- `--collection-cycle-id`
- `--collection-date`
- `--database`
- `--output-dir`

CLI는 기존 mode-600 KR DB만 읽고 쓴다. fixture, provider, URL, token, credential, account, order, force와 mode 옵션은 없다. historical cycle replay와 deterministic derivation을 허용하므로 현재 날짜 gate를 사용하지 않는다.

mode-600 한국어 보고서는 source 상태, failure code, upstream/derived aggregate count, symbol count, 신규 catalyst/observation 수와 replay 여부만 담는다. symbol, catalyst/receipt ID, checksum, raw payload, DB/output path를 출력하지 않는다. terminal failed run은 보고서를 쓴 뒤 nonzero로 종료하고 source-not-ready는 원장·보고서를 만들지 않고 nonzero로 종료한다.

## 8. Downstream Projection 호환

theme projector는 v1과 v2 payload를 schema version으로 명시적으로 분기한다. v1 numeric payload replay는 현재와 동일하다. v2 metric은 source catalyst가 같은 cycle의 KIS volume-ranking row이고 symbol이 일치하며 upstream observation이 `source_observed_at <= observed_at`을 만족할 때만 사용한다.

현재 v1 keyword classification의 related symbol은 숫자 6자리 계약을 유지한다. 따라서 alphanumeric v2 metric은 source evidence와 coverage에는 보존되지만 v1 theme rule에 자동 결합하지 않는다. 그 결합은 related-symbol/classification v2라는 별도 가설·schema milestone에서 수행한다.

## 9. 검증 기준

- instrument v1/v2 symbol contract와 lowercase/길이/control rejection
- v1 payload replay 및 v2 canonical/alphanumeric/empty-symbol contract
- exact upstream run·receipt·observation lineage validation
- 30개 또는 synthetic volume rows의 deterministic ratio/trading value projection
- average zero, missing metric, duplicate, malformed row, failed/missing upstream fail-closed
- terminal replay와 orphan catalyst restart no-op
- downstream projector의 v1 compatibility와 v2 lineage 검증
- CLI help, bad ID/date/path, source-not-ready, happy path, terminal replay, mode/redaction
- full pytest, Ruff, basedpyright, actual CLI manual QA
- provider network·credential load·외부 message·broker mutation 0건

## 10. 다음 단계

이 source가 검증되면 DART, LS NEWS, KIS ranking, volume surge adapter를 같은 날짜/cycle ID로 순서 실행하고 기존 DB-only coordinator를 마지막에 호출하는 orchestrator를 구현한다. 현재 미국장 Alpaca Paper regular-session lifecycle smoke는 별도의 시장시간 조건부 우선순위로 유지한다.
