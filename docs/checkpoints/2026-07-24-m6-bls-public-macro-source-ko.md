# M6 BLS Public Data v1 거시 source 체크포인트

상태: **공식 API 연결·실제 수집·exact replay 완료, 관측 완전성 9444 bps로 degraded**

## 완료한 제품 경계

- canonical source는 `bls/public_data_v1`, domain은 `global_macro`, event type은
  `macro_observation`이다.
- endpoint는 `https://api.bls.gov/publicAPI/v1/timeseries/data/`의 POST 하나로
  고정했다. redirect, 다른 origin·path, 4 MiB 초과 응답은 차단한다.
- 한 요청은 정렬된 대문자 BLS series 1~25개와 최대 10년의 시작·종료 연도를
  content-addressed request identity에 결합한다.
- HTTP status를 포함한 원문 bytes와 SHA-256을 parser보다 먼저 mode-600
  append-only SQLite에 확정한다.
- 성공 응답은 요청한 series가 정확히 한 번씩 모두 존재할 때만 정규화한다.
  관측은 연도·period 역순으로 고정하고 값, latest 표식과 각주를 보존한다.
- BLS가 공식적으로 사용하는 각주가 있는 `-` 결측은 숫자로 보간하거나 이전 값으로
  대체하지 않는다. `null` 관측과 각주로 보존하고 실제 가용 관측 수로 completeness를
  계산한다. 각주 없는 `-`나 다른 비수치 값은 `response_structure` 실패다.
- snapshot은 content-addressed private JSON으로 발행하고 capability registry에는
  historical research·shadow forward만 허용한다. completeness SLO 미달 성공은
  `complete`로 가장하지 않고 `degraded`다.
- 이 경로에는 broker, 계좌, 주문, lifecycle, champion 또는 Allocation Manager
  mutation이 없다.

공식 계약 근거:

- [BLS Public Data API v1 signatures](https://www.bls.gov/developers/api_signature.htm)
- [BLS Public Data API limits FAQ](https://www.bls.gov/developers/api_faqs.htm)
- [BLS handling of missing data](https://www.bls.gov/bls/bls-handling-of-missing-data.htm)
- [2025 shutdown CPI missing-data FAQ](https://www.bls.gov/cpi/additional-resources/2025-federal-government-shutdown-impact-cpi-faq.htm)

## exact SHA 운영 증거

- implementation/runtime:
  `ad20351339c623522ef55b12162a0a36e451d04a`
- actual request:
  - series `CUUR0000SA0`, `LNS14000000`
  - years `2025..2026`
  - credential `none`
- first official POST:
  - exit `0`
  - network access `1`
  - raw receipt / terminal run `1 / 1`
  - series / observations `2 / 36`
  - available / footnoted missing `34 / 2`
  - observed completeness `9444 bps`
  - capability `degraded`
  - artifact created `yes`
- exact replay:
  - exit `0`
  - network access `0`
  - raw receipt / terminal run `1 / 1`
  - artifact created `no`
  - provider operation `stored receipt query-only`
- snapshot semantic ID:
  `8d7b7c661ed6ce856c104d8160ed9fd930061963457b08edb08b74e774ea0067`
- snapshot file SHA-256:
  `8795e371ef8941083b62fdb0761dd098602f8e8a11a61599c0d8ca6a18aa148b`
- private evidence root:
  `outputs/macro/m6_live/2026-07-24/bls_public_ad20351339c623522ef55b12162a0a36e451d04a`
- snapshot, report, raw store와 capability registry mode: 모두 `0600`

2025년 10월 두 결측은 collector 장애가 아니다. 실제 응답은
`REQUEST_SUCCEEDED`이고 BLS 공식 문서가 연방정부 셧다운으로 수집하지 못한 값을
Public Data API에서 dash와 각주로 표시한다고 명시한다. 따라서 source transport와
schema 수집은 성공으로 보존하되 전략 입력 완전성은 `10000`으로 올리지 않았다.

## 검증

- 초기 CLI RED: collector entrypoint 부재로 `1 failed, 1 passed`
- 실제 응답 regression RED:
  `decimal.InvalidOperation`이 terminal 저장 전에 탈출
- 결측 semantic RED:
  공식 각주가 있는 dash를 typed missing observation으로 보존하지 못함
- 타깃:
  `11 passed`
- 전체:
  `3623 passed in 231.86s`
- Ruff:
  `All checks passed`
- basedpyright:
  `0 errors, 0 warnings, 0 notes`
- no-excuse:
  `no violations in 14 file(s)`
- CLI 수동 QA:
  - `--help`: exit `0`
  - 잘못된 lowercase series: exit `2`, DB·registry·output 생성 `0`
  - fixture happy: exit `0`
  - official fresh POST: exit `0`, network `1`
  - exact replay: exit `0`, network `0`

## 벤치마킹 source coverage의 현재 경계

| source family | 현재 상태 | 다음 조건 |
|---|---|---|
| BLS Public Data v1 | actual connected, degraded actual snapshot | series catalogue·release-calendar coverage 확장 |
| Treasury·CFTC | actual connected | 기존 일별/주별 범위 유지·전략 requirement 결합 |
| FRED·ALFRED | 미연결 | 공식 API key와 credential contract 필요 |
| GDELT metadata | 미연결 | 공식 query/rate/retention 계약과 raw-first adapter 필요 |
| X·Reddit·Stocktwits | entitlement blocked | 공식 API 또는 허용 vendor 계약, 삭제·retention 정책 필요 |
| CME·ICE futures, OPRA/SIP | license/subscription blocked or partial | 계약된 entitlement 없이는 실제 feed로 표시하지 않음 |
| 논문·공식 연구 API | lineage 계약은 존재, 범용 collector 미연결 | 허용 API별 revision·citation·license receipt 필요 |

`전부 연결`은 위 미연결 source를 가짜 READY로 등록한다는 뜻이 아니다. 자격증명 없는
공개 source는 실제 수집까지 순차 연결하고, key·유료 계약·재배포 권리가 필요한
source는 정확한 blocker와 준비 계약을 먼저 등록한다. 품질 gate, 실패 receipt와
entitlement 판정은 완화하거나 삭제하지 않는다.

## 다음 제품 경계

1. credential 없이 actual 검증 가능한 research/news 공개 API를 raw-first로 추가한다.
2. FRED·ALFRED는 API key 파일의 owner/mode/origin contract를 먼저 구현하고 실제 key가
   있을 때만 bounded smoke를 연다.
3. social·licensed derivatives는 entitlement와 retention·삭제 계약이 확보되기 전까지
   `blocked`를 유지한다.
