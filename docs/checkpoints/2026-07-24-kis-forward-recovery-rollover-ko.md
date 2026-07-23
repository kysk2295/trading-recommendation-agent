# KIS forward 연속 서버 오류 복구·다음 세션 rollover 체크포인트

## 실제 2026-07-23 세션 사건

2026-07-23 뉴욕 정규장 forward watch를 장중 strict progress 감사로 다시 읽었다.
cycle 232까지 ranking, watch, retry, candidate input cardinality는 모두 유지됐지만
다음 원본 행 하나가 세션 전체를 차단했다.

- 시각: `2026-07-24T02:44:46.578735+09:00`
- read-only endpoint: `/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice`
- exchange/symbol: `AMS/TSLZ`
- first/final status: `500/500`
- retry cycle: retry event `4`, 복구 `3`, 최종 실패 `1`
- candidate cycle: 선택 `10`, context 완성 `9`
- watch cycle: exit `1`, `failed`

02:53 KST의 감사 시점에는 네 cycle cardinality가 모두 `239`였고, KIS retry
`698`건 중 `697`건은 복구됐지만 위 한 건은 최종 실패였다. 인접 cycle은 다시
성공했다. 실패 cycle을 삭제하거나 0수익으로 바꾸지 않았으며 이 세션은
`watch_cycle_failures:1`, `kis_read_retry_failures:1`로 계속 부적격이다.

## 원인과 수정

해당 watch는 SHA `d59d2534a2561472c894bfe2acb56bd051dfca90`의 frozen
runtime이었다. 공용 `get_with_server_retry`는 최초 요청과 두 번의 bounded retry,
즉 총 세 번의 연속 5xx 뒤 응답을 호출자에게 반환했다. 실제 실패는 이 예산을 모두
소진한 경우였고, status 분류나 HTTP 200 payload 파싱 오류가 아니었다.

새 SHA `3c476d5390b39c7db252216f2191c6d0d4b8b6fb`는
`500/502/503/504`에만 `0.25초 → 0.75초 → 2.0초`의 세 번의 bounded retry를
적용한다. 따라서 최초 요청을 포함한 최대 시도는 네 번이다.

- `500 → 500 → 500 → 200`: 네 번째 응답으로 복구
- `500`이 네 번 지속: 최종 `500`, fail-closed
- `429`: 재시도 없이 즉시 반환
- redirect, transport error, retry audit 의미: 변경 없음
- 과거 실패 원장과 strict session quality gate: 변경 없음

## TDD·검증

새 테스트는 세 번의 연속 server error 뒤 네 번째 응답이 성공하는 경계를 먼저
실패시켰다. 구현 뒤 다음을 확인했다.

- KIS HTTP/ranking 관련: `21 passed`
- 전체 pytest: `3524 passed`
- Ruff 전체: pass
- basedpyright 전체: `0 errors, 0 warnings, 0 notes`
- compileall: pass
- 수동 recover driver:
  `status=200`, attempts `4`, delays `[0.25, 0.75, 2.0]`, outcome `recovered`
- 수동 terminal driver:
  `status=500`, attempts `4`, delays `[0.25, 0.75, 2.0]`, outcome `failed`

## 2026-07-24 clean-session rollover

실패가 이미 기록된 2026-07-23 프로세스와 장후 체인은 중단하거나 재시작하지 않았다.
다음 거래일은 exact clean detached runtime
`/private/tmp/trading-agent-forward-20260724-3c476d5`에 위 SHA를 고정했다.
runtime은 clean이며 다음 계약으로 `ai.trading-agent.us-forward-20260724`를 교체
등록했다.

- wrapper 시작: `2026-07-24 17:00 KST`
- 실제 정규장 시작: `--wait-until-open`, current NY session만 허용
- watch: `390` cycles, cycle 시작 cadence `60초`
- KIS server 시도 상한: `4`
- quality gate: strict
- broker mutation: `false`
- wrapper/claim mode: `700`
- stdout, stderr, receipt mode: `600`
- atomic claim, completion receipt, 종료 후 self-remove

전용 experiment ledger
`outputs/paper_control/20260724/forward_experiment.sqlite3`에는 strategy version
`4`개와 `experimental_shadow` lifecycle `4`개만 존재한다. Paper champion,
allocation 또는 주문 권한은 추가되지 않았다.

## 예약된 장중·장후 검증

2026-07-24 03:20 KST에 launchd를 다시 읽은 결과 다음 wrapper가 모두 `runs=1`,
`state=running`인 대기 상태였다.

| 단계 | 실행 시각 | 최소 cycle | 관측 PID |
|---|---:|---:|---:|
| US full-session forward | 2026-07-24 17:00 KST부터 open 대기 | 390 목표 | 71427 |
| mid strict progress | 2026-07-25 01:45 KST | 180 | 72270 |
| late strict progress | 2026-07-25 04:15 KST | 330 | 72272 |

기존 `us-day-preflight`, `us-day-finalizer`, `intraday-dataset`,
`intraday-research`의 PID와 run count는 forward wrapper 교체 전후 동일했다.
이 macOS의 `launchctl`에는 `suspend`/`resume` subcommand가 없어 해당 호출은
실행되지 않았지만, downstream payload가 조기 실행되거나 receipt를 만든 흔적은
없었다. downstream은 같은 forward label의 self-remove를 기다린다.

mid/late wrapper는 exact SHA와 clean runtime을 다시 확인한 뒤 local-only progress
감사를 수행한다. finalizer 이후에는 strict closeout이 성공한 경우에만 causal
dataset, exact SHA/READY foundation 등록, source-backed walk-forward와 독립
Reviewer가 진행한다. 실패하면 부분 CSV나 성과 trial을 만들지 않는다.

현재 executable Paper champion은 두 개 미만이므로 Allocation Manager는 계속
비활성이고, 명시적 Paper arm 없는 POST/DELETE와 실제 자금 거래는 모두 없다.
