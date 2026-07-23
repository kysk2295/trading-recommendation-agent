# KIS US server retry 복구 체크포인트

## 실제 결손 원인

보존된 US forward session 네 개를 strict loader와 원본 audit CSV로 다시 대사했다.

- 2026-07-15와 2026-07-21은 KIS read가 첫 `500` 뒤 80ms 단일 재시도에서도
  `500`으로 끝난 cycle을 보존하고 있으며 해당 watch cycle은 실패다.
- 2026-07-16은 같은 repeated failure 외에 scanner child가 audit을 남긴 뒤 parent
  watch audit 전에 중단된 cycle이 있어 coverage가 불일치한다.
- 2026-07-22의 첫 16 watch failure는 detached runtime scanner의 `duckdb` 누락으로
  import 전에 종료한 결과다. 이 경로는 `0c7dc57`의 PEP 723 metadata와 standalone
  child preflight로 이미 닫혔다.
- 2026-07-22의 이후 실패는 성공한 ranking 여섯 요청과 별개로 종목별 분봉·일봉 GET이
  `500 → 500`으로 끝나 candidate context가 결손된 경우다.

실패 cycle, retry event 또는 품질 gate를 삭제·완화하지 않았다. 과거 세션은 계속
blocked이고 실제 historical trial은 계속 0건이다.

## 구현

공유 `get_with_server_retry`의 server-error 복구를 다음처럼 변경했다.

- 대상 status는 기존 `500/502/503/504`만 유지한다.
- 총 HTTP 시도는 최초 요청을 포함해 최대 3회다.
- 재시도 delay는 0.25초, 0.75초의 bounded backoff다.
- 중간 server error 뒤 성공하면 기존처럼 retry event 하나를 `recovered`로 남긴다.
- 세 번 모두 server error이면 최종 status와 `failed`를 보존한다.
- `429`, redirect와 비대상 status는 추가 재시도하지 않는다.
- retry 중 transport error는 감사 event를 남긴 뒤 그대로 전파한다.
- endpoint, GET-only 권한, 인증 header 처리와 quality 판단은 변경하지 않는다.

실제 과거 패턴을 재현한 `500 → 503 → 200` 테스트는 변경 전 503으로 RED였고 변경 후
세 번째 응답 200, delay `[0.25, 0.75]`, 단일 recovered event로 GREEN이다.

## 다음 실제 세션 예약

commit `d59d2534a2561472c894bfe2acb56bd051dfca90`의 clean detached runtime
`/private/tmp/trading-agent-forward-20260724-d59d253`을 만들고 2026-07-24 NYSE
세션을 one-shot launchd로 예약했다.

1. `ai.trading-agent.us-forward-20260724`: 04:00 EDT부터 premarket와 full regular watch
2. `ai.trading-agent.us-day-preflight-20260724`: 장중 current setup Paper GET/WSS preflight
3. `ai.trading-agent.alpaca-sip-smoke-20260724`: 09:35 EDT bounded read-only stream
4. `ai.trading-agent.us-day-finalizer-20260724`: 장마감 flat/reconciliation terminal
5. `ai.trading-agent.intraday-dataset-20260724`: strict causal dataset materialization

다섯 wrapper는 현재 `running`, run count 1이며 아직 terminal exit가 없다. 기존 KR
finalizer, Hermes service와 2026-07-23 US watch는 변경하거나 재시작하지 않았다.

## 검증

- RED: consecutive server error 테스트 1 failed
- KIS·opening gap·daily quality·replay 관련: `240 passed`
- 전체 pytest: 제품/시장 코드 `3394 passed`; 기존 Grok offline harness `5 failed`
- 전체 Ruff: 통과
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- Python no-excuse: 변경 파일 위반 0
- manual wire fake: status 200, request 3, delays `[0.25, 0.75]`
- 2026-07-24 runtime child preflight failure: 0
- 다섯 wrapper `zsh -n`·dry-run·bad input: `0/0/2`
- wrapper mode: 모두 `700`
- account/order mutation과 Alpaca Paper POST/DELETE: 0

예약 결과가 clean session, 성과 또는 champion 증거는 아니다. 장후 strict gate와
materializer가 통과한 actual artifact만 다음 v2 manifest 입력으로 사용한다.
