# Strict closeout 기반 actual research handoff 체크포인트

## KR 장후 예약 실측

기존 `ai.trading-agent.kr-m3-finalize-20260723`는 2026-07-23 15:32:02 KST에
실행됐다. 장후 local control cycle 자체는 완료됐지만 성과 세션은 아니었다.

- terminal: `censored`
- reason: `no_shadow_entry_artifact`
- completed sessions: `0`
- censored sessions: `1`
- completed trades: `0`
- Reviewer 최소 조건: signal `0/30`, forward session `0/20`
- account/order, allocation, external mutation: `0`

launchd job은 단발 종료가 아니라 KeepAlive 상태여서 약 10초 간격으로 동일 cycle을
반복 실행하고 있었다. 21:12 KST 기준 run count는 `1931`이었다. 기존 process를
변경·중단·재시작하지 말라는 운영 제약에 따라 이 job은 건드리지 않았다.

## 발견한 actual research race

기존 dataset watcher는 US watch label 종료만 기다린다. watch가 regular-session cycle을
완료한 뒤 post-session terminal을 쓰지 못하면 strict dataset이 먼저 차단되고, 뒤에서
별도 closeout이 세션을 복구해도 dataset→foundation→walk-forward→Reviewer가 자동으로
재시도되지 않는 race가 있었다.

`run_planned_intraday_actual_research.py`에 선택적 선행조건 쌍을 추가했다.

- `--prerequisite-receipt`
- `--prerequisite-report`

두 경로는 all-or-none이다. 둘 중 하나만 주거나 파일이 없으면 plan, queue, ledger를
만들기 전에 차단한다. receipt는 owner-only private file로 읽고 다음 exact shape만
허용한다.

```text
exit_code=0
completed_at_epoch=<positive integer>
```

closeout report도 owner-only private file이어야 하며 `recovered` 또는 `replayed` 결과
하나와 다음 strict marker를 각각 정확히 하나 포함해야 한다.

- failed cycle deletion: `0`
- quality gate relaxed: `false`
- provider, credential, account, or order operation: `0`

선행조건을 주지 않은 기존 caller 계약은 유지된다. 선행조건을 준 장후 handoff만
closeout 성공 뒤 실행된다.

## 검증

TDD에서 prerequisite 옵션과 helper가 없는 6개 실패를 RED로 확인한 뒤 GREEN으로
전환했다.

- closeout·actual research 관련 집중 테스트: `23 passed`
- 새 prerequisite 경계 테스트: `7 passed`
- 전체 Ruff: pass
- 전체 basedpyright: `0 errors, 0 warnings, 0 notes`
- 전체 pytest: `3459 passed`
- 기존 `tests/test_grok_task_runner.py` 오프라인 환경 테스트: `5 failed`
- CLI help: 두 prerequisite 옵션 노출
- one-sided prerequisite: exit `1`, plan/queue/ledger 생성 `0`
- valid receipt + strict recovered report: exit `0`, result `ready`
- 수동 happy path: foundation `1`, trial `1`, report mode `600`

구현 commit은
`e68857e514a6051db29a424d6d95575666af4085`이며 `origin/main`에 push했다.

## actual one-shot 예약

exact clean runtime
`/private/tmp/trading-agent-post-closeout-handoff-20260723-e68857e`로 다음 job을
예약했다.

- label: `ai.trading-agent.post-closeout-research-20260723`
- 실행 시각: 2026-07-24 05:16 KST / 2026-07-23 16:16 EDT
- 등록 직후 state: running
- run count: `1`
- PID: `17737`
- wrapper mode: `700`
- stdout/stderr mode: `600`
- receipt: pending

job은 strict forward closeout receipt와
`post_session_closeout/exact-a945ba4/forward_post_session_closeout_ko.md`를 먼저
검증한다. 통과하면 기존 immutable run key `actual-2026-07-23`과 frozen strategy
bindings로 causal dataset, exact READY foundations와 v2 manifest, source-backed
multi-strategy walk-forward, 독립 Reviewer 전체 coordinator를 실행한다. 기존 job이
이미 성공했다면 frozen plan replay이므로 experiment/review artifact를 중복 생성하지
않는다.

기존 US watch, dataset, research, closeout과 Hermes process는 변경·중단·재시작하지
않았다. 예약은 actual clean session 성공 주장이 아니다. 실행 후 receipt, exact CSV
SHA, READY manifest, trials와 Reviewer 결과를 다시 검증해야 한다. Paper arm,
allocation authority와 품질 임계값은 변경하지 않았다.
