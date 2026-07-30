# 제품 우선 마일스톤 현황

권위: `docs/superpowers/specs/2026-07-17-institutional-multi-market-quant-research-os-design.md` §17 및 현재 acceptance graph

갱신: 2026-07-30

G002 발견 기준은 `main == origin/main == 14bc732`였던 discovery baseline이다. 최종 G002 증거는 이 문서를
포함한 당시 current main commit에 결속한다.

이 문서는 connector, DB schema, fixture, PID 또는 prose log 개수를 제품 완료로 세지 않는다. 실제 시장
세션과 Paper/shadow 결과가 없으면 추천·주문을 강제로 만들지 않고 `waiting` 또는 `blocked`로 남긴다.

## 권위 acceptance dependency graph

```text
G002 authority sync (**완료**: current-main launchd cutover 및 stale startup typed preflight)
  -> G003 M1 Hermes 사용자 표면
       -> G004 M2 US Day 자연 Paper lifecycle ─┐
       -> G005 M3 KR Day 실제 shadow lifecycle ─┴-> G006 M4 재부팅 가능한 soak
                                                     -> G007 M5 Swing 다중세션 lifecycle
                                                          -> G008 M6 Loop Engineer 폐루프
                                                               -> G009 최종 acceptance
```

G002 authority sync는 완료됐다. 이제 **G003 M1 Hermes가 활성 다음 gate**이며, G003이 통과된 뒤에야
G004와 G005를 독립적으로 실제 세션 gate로 진행한다. G004와 G005가 모두 통과하기 전에는 G006으로
승격하지 않으며, 어느 gate도 코드·fixture만으로 완료 처리하지 않는다.

## 2026-07-30 현재 상태

| Goal / M | 사용자 결과 | 상태 | 현재 확인된 사실 | 통과 시 필요한 관찰 artifact 또는 기준 |
|---|---|---|---|---|
| G002 | 현재 main, launcher, Dashboard 실행 권위 동기화 | **완료** | current-main launchd cutover이 끝났고 plist/loaded job은 repository-root path를 사용하며 `.worktrees` 참조가 없다. startup preflight는 credentials/readiness/snapshot/relay 전에 `branch == main`, tracked-clean, `HEAD == local main == origin/main`을 확인한다. stale/missing-ref/dirty/non-main 입력은 typed blocker로 fail-closed하며 side effect `0`; Hermes·US·KR job은 계속 running, Paper mutation count `0` | `authority-graph.json`, stale 권위 입력의 typed blocked 결과, runtime non-interference; current clean main SHA에 결속 |
| G003 / M1 | US·KR 추천·무추천·incident·일일요약의 Hermes/Telegram 전달 | **typed waiting / 미통과** | fail-closed AC-001 aggregate builder/verifier와 controlled operational QA가 존재한다. 현재 QA는 42 events, 47 attempts, 31 ACKs, 11 dead-letter이며 execution-order/broker/mutation counts는 각각 `0`; 6개 family가 분리되고 true-store reopen fixture가 통과했다. stockagent plugin `1.4.0` 계약은 별도 `allocation_manager` 상태로 독립 챔피언 `0/2`, 직접 주문·종목 선택 권한 `false`를 노출하며, 새 clean pushed commit 뒤 installation receipt를 재생성해야 한다. US·KR 각 5개 연속 실제 세션 normalized report/manifest가 없어 실제 세션·수익성은 주장하지 않는다 | `outputs/acceptance/hermes/manifest.json`의 US·KR 각 5개 실제 세션 reconciliation, restart/provider-fault report, 중복·누락 0 |
| G004 / M2 | US Day 자연 ORB → owner arm → Alpaca Paper lifecycle | **blocked** | `2026-07-22`: `censored_no_setup`; `2026-07-24`: `blocked natural_setup_without_terminal`. 두 세션 모두 paper mutation/order/trade/safety counts `0`; 자연 entry→protective OCO→exit/EOD→대사 terminal 없음 | `outputs/acceptance/us_day/natural_paper_lifecycle.json`, `three_session_report.json`, `final_reconciliation.json`; 실제 3개 NYSE 세션 중 자연 setup 1개 완주 |
| G005 / M3 | KR Day 실제 same-cycle → 보수적 shadow lifecycle | **blocked** | 마지막 known strict chain은 `2026-07-23`: KIS `500` 1건과 공식 `15:59` bar 누락 2건으로 차단. 3개 열린 KRX 세션 lifecycle 없음 | `outputs/acceptance/kr_day/open_session_shadow_lifecycle.json`, `three_session_report.json`, `reviewer_result.json`; KIS·LS mutation 0과 실제 후보 1개 포함한 3-session gate |
| G006 / M4 | US·KR 5-session always-on soak 및 재부팅 복구 | **waiting** | 현재 실행은 date-specific one-shot이며 reboot-persistent가 아님. 연속 5거래일 top-level 결과가 없음 | `outputs/acceptance/soak/us_five_session_report.json`, `kr_five_session_report.json`, `restart_and_provider_fault_reconciliation.json`; 재부팅 1회·read-only provider 장애 후 중복·누락 0 |
| G007 / M5 | US Swing 실제 다중세션 shadow lifecycle | **blocked** | fixture/replay 경로는 있으나 실제 다중세션 entry→overnight→exit/invalidation lifecycle이 없음 | `outputs/acceptance/swing/multi_session_shadow_lifecycle.json`, `reviewer_result.json`, `hermes_outcome_receipt.json` |
| G008 / M6 | provenance-bound Loop Engineer source→decision 폐루프 | **blocked** | ledger에 hypothesis `4`, `experimental_shadow` strategy version `20`, trial `2`가 있으나 source→preregister→sandbox→historical/walk-forward→shadow→Reviewer→immutable decision의 closed loop가 없음 | `outputs/acceptance/research/challenger_source_to_decision.json`, `hermes_weekly_research_receipt.json`, `signed_generated_code_provenance.json` |
| G009 | 최종 Professional Research OS acceptance | **not ready** | G003~G008의 필수 실제 gate가 미통과 | `outputs/acceptance/governance/manifest.json`와 lifecycle replay/safety/champion independence 검증이 같은 frozen clean commit에서 모두 OK |

## 관찰된 증거와 해석

- Hermes 원장 기준은 `outputs/hermes/delivery.sqlite3`와 그 read-only acceptance receipts다. AC-001 aggregate
  builder/verifier와 controlled operational QA는 fail-closed로 동작하지만, 현재 수치는 5-session top-level
  acceptance를 증명하지 않는다. 실제 US·KR 세션 normalized report/manifest가 생기기 전에는 제품 통과나
  수익성을 주장하지 않는다.
- US Day 현재 세션 receipt는 `outputs/acceptance/us_day/sessions/2026-07-22.json` 및
  `2026-07-24.json`이다. `censored_no_setup`은 자연 setup 증거가 아니고,
  `natural_setup_without_terminal`은 성공이 아니라 blocked incident다.
- KR strict-chain blocker는 source freshness와 provider 오류를 그대로 보존한다. KIS·LS read-only 경계를
  완화하거나 누락된 공식 bar를 추정하지 않는다.
- 실험 ledger의 cardinality는 준비된 shadow 등록 수일 뿐, M6 source-to-decision 결과나 수익성을 뜻하지
  않는다. 어떤 backtest, fixture, Paper, shadow output도 확정수익으로 표현하지 않는다.

## Gate 완료 규칙

각 gate는 다음을 모두 충족해야 한다.

- 사용자에게 Telegram/Hermes 결과, 무추천, incident 또는 연구 요약이 실제로 도달하고 terminal/ACK로 대사된다.
- 현재 세션 날짜의 read-only data 또는 허용된 Alpaca Paper/shadow lifecycle을 사용한다.
- 재실행·process restart·provider 장애 뒤에도 중복 주문/카드가 없고 누락·미대사는 blocker로 기록된다.
- clean immutable commit, 실제 session manifest, artifact hash와 typed receipt가 서로 일치한다.
- fixture 및 CLI help/invalid QA는 보조 증거일 뿐 실제 시장 gate를 대신하지 않는다.

## M7/M8 safety gate (의미 불변)

- **M7 Systematic·Derivatives Agent 확장:** 상태는 **조기**다. M6 이후 lane별 사용자 수직 증거가 먼저 필요하다.
- **M8 Allocation Manager:** 상태는 **금지**다. 두 독립 executable champion이 자연스럽게 증명되기 전에는
  allocation, 종목 발굴, 주문을 하지 않는다.

## 제품 완료 구간

- **운영 제품 v1:** M1~M4 완료 후에만 선언
- **Research OS v1:** M1~M6 완료 후에만 선언
- **전문 다중 Agent 목표:** M1~M7 완료 후에만 선언
- **Allocation:** M8 gate가 자연스럽게 열린 뒤 별도 완료

## 영구 금지

- 실자금 거래와 Alpaca live endpoint
- KIS·LS의 계좌·주문 mutation
- 무단 소셜 크롤링과 데이터 재배포
- LLM의 재량 주문, 위험한도 변경 또는 독립 승격
- 성과 근거 없는 위험한도 확대
- backtest·fixture·Paper 결과를 확정수익으로 표현
