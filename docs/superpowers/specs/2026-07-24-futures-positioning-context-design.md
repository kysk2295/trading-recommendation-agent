# Futures Positioning Context 설계

## 목적

Milestone 6의 CFTC TFF positioning context와 provider-neutral futures roll
security master를 하나의 as-of shadow context로 결합한다. 결과는 CFTC weekly
market-level 포지션이 어느 active futures contract window에서 관측 가능했는지를
재생할 수 있어야 한다.

이번 vertical은 network, credential, futures 시세, broker, account, order,
추천과 allocation을 열지 않는다. 두 기존 immutable artifact와 검토된 binding만
query-only로 읽는다.

## 접근 선택

세 접근을 비교했다.

1. 새 join artifact를 만든다. 기존 CFTC와 futures master artifact를 변경하지 않고
   exact file SHA와 semantic ID를 참조한다.
2. CFTC context에 futures identity를 추가한다. 이미 게시된 CFTC artifact schema와
   ID를 바꾸고 market-level source를 contract-level source로 오해하게 만든다.
3. full derivatives agent와 volatility context를 한 번에 만든다. price curve,
   option volatility와 strategy policy까지 섞여 단일 검증 경계를 넘는다.

1번을 채택한다. 원 source provenance와 독립 replay를 보존하면서 잘못된 symbol-text
join만 새 explicit binding에서 차단할 수 있다.

## 입력 계약

CLI는 다음 세 private mode-600 input을 받는다.

- `cftc_tff_context_<context-id>.json`
- `futures_roll_security_master_<master-id>.json`
- 검토된 `FuturesPositioningBinding` canonical JSON

각 published artifact는 파일명 ID, parsed model ID, canonical bytes와 file SHA-256을
다시 대사한다. binding은 다음을 고정한다.

```text
schema_version
cftc_contract_market_code
root_symbol
venue
observed_at
effective_from
effective_to
source_reference
```

binding의 CFTC code, root와 venue가 두 input과 정확히 같아야 한다. `as_of`는 binding
관측 이후이면서 effective interval 안이어야 한다. market name이나 provider symbol
문자열로 관계를 추론하지 않는다.

## as-of 결합

`as_of`는 aware datetime이어야 하며 다음 조건을 모두 만족한다.

- CFTC response `observed_at <= as_of`
- futures master `source_observed_at <= as_of`
- CFTC latest report date가 as-of UTC date보다 미래가 아님
- report age가 명시적 `maximum_report_age_days` 이내
- `active_from <= as_of < roll_at`인 futures contract가 정확히 하나
- active instrument와 provider alias가 binding venue/root를 보존

기본 maximum report age는 14일이고 허용 범위는 1~31일이다. 이 값도 output
identity에 포함한다. stale CFTC report, 미래 receipt, roll gap/overlap 또는 binding
mismatch는 artifact 발행 전에 fail-closed한다.

## 출력 계약

`FuturesPositioningContext`는 다음을 보존한다.

```text
schema_version
as_of
maximum_report_age_days
binding_sha256
cftc_context_id
cftc_artifact_sha256
futures_master_id
futures_master_artifact_sha256
cftc_contract_market_code
root_symbol
active instrument identity
active provider alias
active_from
roll_at
latest/previous CFTC report date
CFTC observed_at
five category positions
```

canonical payload SHA-256을 context ID로 사용하고
`futures_positioning_context_<context-id>.json`을 mode 600으로 게시한다. exact replay는
기존 artifact를 재사용한다.

aggregate report에는 root, venue, report dates, category count, active contract
존재 여부, max age, artifact 생성 여부와 mutation 0만 기록한다. instrument ID,
provider symbol, 개별 position, source path와 raw payload는 보고서에 쓰지 않는다.

## 파일 경계

- `trading_agent/futures_positioning_context_models.py`: binding, loaded input,
  join request와 output model
- `trading_agent/futures_positioning_context.py`: private input loading,
  semantic/file identity verification, as-of join과 immutable publication
- `run_futures_positioning_context.py`: Typer boundary와 aggregate report
- `tests/test_futures_positioning_context.py`: causal/binding/staleness unit tests
- `tests/test_futures_positioning_context_cli.py`: private CLI happy/replay와 bad input

각 production file은 250 pure LOC 이하를 유지한다. 기존 CFTC/futures artifact
schema나 published identity는 변경하지 않는다.

## 검증과 제한

TDD는 먼저 happy join이 active contract와 five-category positioning을 만드는지
검증한다. 이어 binding mismatch, future observation, stale report, renamed artifact,
public binding과 as-of roll boundary를 각각 독립적으로 차단한다.

CLI는 help, bad input, private fixture happy path와 exact replay를 검증한다. 실제 CFTC
artifact를 fixture futures master와 결합할 수 있지만 이를 licensed current futures
coverage로 표현하지 않는다. 실제 CME/ICE master, settlement·curve·basis, intraday
price, derivatives strategy 성과와 Paper 권한은 별도 evidence가 필요하다.
