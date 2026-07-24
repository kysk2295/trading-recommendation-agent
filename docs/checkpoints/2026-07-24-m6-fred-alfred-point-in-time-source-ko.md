# M6 FRED·ALFRED point-in-time 거시 source 체크포인트

상태: **공식 API·private credential·실제 최신/빈티지 수집·exact replay 완료**

## 완료한 제품 경계

- 최신 관측 source는 `fred/series_observations`, 시점고정 관측 source는
  `alfred/vintage_observations`로 분리했다. 두 source 모두 domain
  `global_macro`, event type `macro_observation`이다.
- endpoint는
  `https://api.stlouisfed.org/fred/series/observations`의 GET 하나로 고정했다.
  한 요청은 source mode, series, 관측 시작·종료일, limit와 ALFRED의 명시적
  `vintage_date`를 content-addressed identity에 결합한다.
- FRED API key는 현재 사용자 소유, link count 1, mode `0600`, no-follow regular
  file의 `FRED_API_KEY` 한 줄에서만 읽는다. 32자리 lowercase alphanumeric이
  아니면 provider 호출 전에 차단하며 key와 credential 경로는 request identity,
  raw receipt, snapshot, report에 넣지 않는다.
- ALFRED는 `vintage_dates=YYYY-MM-DD`를 보내고 응답 top-level 및 모든 관측의
  `realtime_start/realtime_end`가 그 날짜와 정확히 같을 때만 성공한다. 따라서
  현재 수정된 값이 과거 시점 데이터로 섞이지 않는다.
- status와 원문 bytes·SHA-256 receipt를 parser보다 먼저 mode-600 append-only
  파일로 확정한다. strict JSON parser는 요청 범위·limit·offset·정렬·count와
  중복 없는 관측일을 검증하고 FRED 공식 결측 표기 `.`만 typed `null`로 보존한다.
- snapshot과 terminal은 content-addressed private JSON이다. exact terminal
  replay는 credential file과 network를 열기 전에 저장된 receipt를 다시 투영해
  terminal과 대사한다.
- capability registry는 historical research·shadow forward만 허용한다. 결측이
  있으면 성공을 유지하되 completeness 실제값과 `degraded`를 기록한다.
- 이 adapter는 hypothesis, strategy, trial, recommendation, broker, account,
  order, champion 또는 Allocation Manager를 변경하지 않는다.

공식 계약 근거:

- [FRED series observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [FRED/ALFRED API overview](https://fred.stlouisfed.org/docs/api/fred/overview.html)
- [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html)
- [FRED API error codes](https://fred.stlouisfed.org/docs/api/fred/errors.html)
- [FRED API terms of use](https://fred.stlouisfed.org/docs/api/terms_of_use.html)

## exact SHA 실제 운영 증거

- implementation/runtime:
  `d1aae55443bc0a2be3bdfe781586418abfeabc87`
- actual bounded query:
  - series `CPIAUCSL`
  - observation range `2024-01-01..2024-03-01`
  - FRED mode: latest view, limit `10`
  - ALFRED mode: vintage `2024-04-01`, limit `10`
- first official GET:
  - FRED exit `0`, network `1`, observations `3/3 available`
  - ALFRED exit `0`, network `1`, observations `2/2 available`
  - 두 capability 모두 `complete`, completeness `10000 bps`
- point-in-time 구분:
  - 최신 FRED에는 관측 3개가 존재한다.
  - `2024-04-01` ALFRED vintage에는 당시 제공된 관측 2개만 존재한다.
  - 최신값을 과거 vintage의 누락 관측으로 보충하지 않았다.
- exact replay:
  - 존재하지 않는 credential path를 지정해 두 mode 모두 exit `0`
  - network `0`, `replayed terminal=true`
  - actual과 replay snapshot bytes가 mode별로 정확히 동일
  - 동일 output에서 재실행했을 때 artifact created `no`
- FRED snapshot semantic ID:
  `41868cdc205083962c84081cab966b710df196acd0b399f4c6ab142be5152494`
- FRED snapshot file SHA-256:
  `2d5c162d298be6201a35788bbd8d87cc0541431e69c416221a8641f8350e9a8c`
- ALFRED snapshot semantic ID:
  `8cc7ce9af566eaa7c5193fcd51d6664a570b13d50dba077cbc39ca3c9bb93d77`
- ALFRED snapshot file SHA-256:
  `faee2001d8a8298512d1ace90511cc7bafccb167ac003121416cb9bf2c7898a1`
- private evidence root:
  `outputs/macro/m6_live/2026-07-24/fred_alfred_d1aae55443bc0a2be3bdfe781586418abfeabc87/verified`
- evidence file `21`개 모두 mode `0600`
- credential value가 evidence bytes에 존재하는 파일 `0`개

## 검증

- 초기 RED: `trading_agent.fred_alfred_config` 부재로 collection error
- 타깃: `3 passed`
- 전체: `3628 passed in 235.97s`
- Ruff: `All checks passed`
- basedpyright: `0 errors, 0 warnings, 0 notes`
- 변경 Python 파일: 모두 250줄 이하
- CLI 수동 QA:
  - `--help`: exit `0`, mode·vintage option 노출
  - ALFRED vintage 누락: exit `2`, state·registry·output 생성 `0`
  - fixture happy: exit `0`, snapshot·report 생성
  - official FRED/ALFRED GET: 각각 exit `0`, network `1`
  - missing credential exact replay: 각각 exit `0`, network `0`

## 벤치마킹 source coverage의 현재 경계

| source family | 현재 상태 | 다음 조건 |
|---|---|---|
| BLS Public Data v1 | actual connected, degraded actual snapshot | release-calendar·series catalogue 확장 |
| FRED latest | actual connected, complete bounded snapshot | 필요한 거시 series와 release 시각 계약을 전략 requirement에 결합 |
| ALFRED vintage | actual connected, complete point-in-time snapshot | 여러 vintage의 revision panel과 release-calendar causality 확장 |
| Treasury·CFTC | actual connected | 기존 일별/주별 범위 유지·전략 requirement 결합 |
| arXiv metadata | actual connected, query-only | 논문 본문 claim·실험은 별도 reviewed lineage에서만 허용 |
| GDELT metadata | 미연결 | 공식 query/rate/retention 계약과 raw-first adapter 필요 |
| X·Reddit·Stocktwits | entitlement blocked | 공식 API 또는 허용 vendor 계약, 삭제·retention 정책 필요 |
| CME·ICE futures, OPRA/SIP | license/subscription blocked or partial | 계약된 entitlement 없이는 실제 feed로 표시하지 않음 |

## 다음 제품 경계

1. 실제 장중 source의 ranking/watch/candidate/retry 결손 원인을 고쳐 clean actual
   forward session을 만든다.
2. clean session을 causal CSV·READY v2 manifest로 올리고 독립 Reviewer까지 실행한다.
3. 추가 공개 source는 동일 raw-first 경계로 연결하되, licensed/social source는
   entitlement와 retention 계약 전까지 blocked를 유지한다.
