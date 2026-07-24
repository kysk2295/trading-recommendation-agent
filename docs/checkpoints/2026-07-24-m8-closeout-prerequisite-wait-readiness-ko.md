# M8 closeout bounded-wait·KR source READY 체크포인트

## 제품 결과

2026-07-24 수동 source readiness에서 다음 한국장 clean source cycle의 외부
선행조건이 모두 준비됐음을 확인했다.

- result: `ready`
- OpenDART: `ready`
- LS NWS: `ready`
- KIS live credential contract: `ready`
- required terminal sources: `4`

이 결과는 credential value, 계좌 식별자 또는 주문 정보를 출력하지 않는다. 이미
09:05 KST에 실패한 7월 24일 source cycle을 소급 복구하지 않으며, 7월 27일 예약
chain부터 clean 표본 자격을 다시 평가한다.

미국장 strict closeout은 2026-07-25 05:20 KST까지 terminal receipt를 발행할 수
있는데 cumulative actual research가 05:18 KST에 파일을 한 번만 확인하도록 예약돼
있었다. clean closeout이 정상적으로 늦게 끝나도 causal dataset 전에 연구가
차단될 수 있는 실제 race였다.

`run_planned_intraday_actual_research.py`에 다음 선택적 bounded-wait 계약을 추가했다.

```text
--prerequisite-receipt + --prerequisite-report
+ --prerequisite-wait-until <timezone-aware deadline>
→ 두 파일의 atomic publication을 deadline까지 bounded poll
→ 기존 strict receipt/report/cardinality 검증
→ dataset·plan·trial mutation
```

deadline이 없으면 기존 immediate fail-closed 동작을 유지한다. deadline만 주거나
timezone이 없거나 시간이 끝날 때까지 두 파일이 모두 게시되지 않으면 연구
mutation 전에 차단한다. strict result, 300~390 동일 cycle cardinality, failed cycle
deletion `0`, relaxed gate `false`는 완화하지 않았다.

구현 commit은
`96783185689da9df3b43ffff28872ac366dd59b0`이며 `origin/main`에 push했다.

## TDD·검증

- RED: delayed closeout publication에서 request boundary가 없어 `1 failed`
- RED: deadline 도달 시 sleeper가 호출돼 `1 failed`
- RED: CLI help에 wait option이 없어 `1 failed`
- focused closeout·actual research: `21 passed`
- full pytest: `3612 passed in 228.06s`
- Ruff: pass
- basedpyright: `0 errors, 0 warnings, 0 notes`
- Python no-excuse: violation `0`
- CLI help: exit `0`, wait option 노출
- timezone 없는 bad input: exit `2`
- 0.2초 뒤 closeout을 게시한 CLI happy path: exit `0`, 실제 wait `true`,
  result `ready`, report mode `600`

## 2026-07-24 운영 예약 교체

기존 05:18 KST cumulative research는 stdout/stderr `0` bytes, receipt `0`,
claim `0`을 확인한 뒤 label만 제거했다. 현재 미국장 수집 runner, downstream audit,
Hermes는 변경·중단·재시작하지 않았다.

새 at-most-once 예약은 같은 frozen cumulative payload와 receipt를 사용하되 strict
closeout deadline 뒤에 시작한다.

- label:
  `ai.trading-agent.post-closeout-research-cumulative-v1-after-closeout-20260724`
- run: 2026-07-25 05:22 KST / 2026-07-24 16:22 EDT
- closeout deadline: 2026-07-25 05:20 KST
- 등록 직후 state: `running`, runs `1`, PID `10679`
- runner mode: `700`
- stdout/stderr mode: `600`
- receipt/claim: pending
- dry-run: strict quality gate, plan schema `3`, outcome trace schema `2`,
  broker mutation `false`

이 예약은 clean actual session, READY foundation, trial 또는 Reviewer 성공 증거가
아니다. 장후 receipt, exact causal CSV SHA, v2 READY manifest, 세 전략 trial과
독립 Reviewer 결과를 실행 뒤 다시 검증해야 한다. Paper champion과 Allocation
authority는 계속 닫혀 있다.
