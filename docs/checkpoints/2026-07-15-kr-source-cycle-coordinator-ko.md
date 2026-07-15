# KR Multi-Source Cycle Coordinator 체크포인트

## 범위

다중 시장 Research OS Milestone 3과 KR Theme Phase T0에서 네 source coverage를 최종 collection cycle로 확정하는 DB-only coordinator를 추가했다.

```text
news terminal source run
dart terminal source run
kis_ranking terminal source run
volume_surge terminal source run
-> exact source 집합·cycle identity 검증
-> immutable KrCatalystCollectionCycle
-> mode-600 coverage CSV·한국어 aggregate 요약
```

이번 단계는 provider adapter를 실행하거나 source run을 만들지 않는다. 기존 append-only KR 원장에 저장된 terminal evidence만 읽으며 자격증명, HTTP, LLM, 현재가, Opportunity, TradeSignal, shadow fill과 국내 계좌·주문 코드를 import하지 않는다.

## 확정 계약

### 네 source exact 집합

- `dart`, `kis_ranking`, `news`, `volume_surge` 네 terminal run이 모두 있어야 final cycle을 append한다.
- source 하나라도 없으면 missing row는 보고서에만 기록하고 synthetic source run이나 cycle을 만들지 않는다.
- cycle 시작은 네 run의 최소 `started_at`, 종료는 최대 `completed_at`으로 결정한다.
- source별 status, record count와 failure code는 terminal run에서 그대로 투영한다.
- 네 run이 성공하면 `complete=true`, 하나라도 실패하면 해당 실패를 보존한 `complete=false` cycle을 append한다.

### 기존 원장 검증 재사용

Coordinator는 Writer lease를 먼저 열어 schema v1 원장을 v2로 migration한 뒤 source run을 조회한다. 새 schema나 UPDATE 경로를 추가하지 않았다.

최종 append는 기존 `KrThemeWriter.append_cycle()`을 사용하므로 다음을 다시 검증한다.

- source별 실제 observation 수와 declared record count 일치
- observation 시각이 derived cycle 범위 안에 존재
- 같은 immutable cycle의 exact replay는 no-op
- 같은 ID의 다른 payload는 conflict

부분 catalyst, receipt, observation과 failed source run은 삭제하거나 수정하지 않는다.

## CLI

`run_kr_source_cycle.py`는 다음 세 인자만 노출한다.

```text
--collection-cycle-id
--database
--output-dir
```

- provider, URL, fixture, secret, account와 order 옵션이 없다.
- complete cycle만 exit 0이다.
- source 누락과 terminal failed cycle은 aggregate 보고서를 쓴 뒤 exit 1이다.
- 잘못된 cycle ID는 DB와 보고서 생성 전에 exit 2다.
- unexpected validation/report 오류의 원문 예외는 고정된 한국어 오류로 치환한다.
- CSV에는 `source,status,record_count,failure_code`만 기록한다.
- 요약에는 source 수, missing/failed 수, catalyst 합계, final/new cycle 여부만 기록한다.
- cycle ID, DB/output 경로, source run/receipt ID, checksum, payload와 provider 메시지는 출력하지 않는다.

## 검증 결과

### 자동 검증

- baseline: `1155 passed`
- coordinator·CLI 추가 후 전체: `1170 passed in 20.84s`
- focused coordinator/store/source model: `36 passed`
- focused coordinator+CLI: `15 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`

### 실제 CLI QA

- `./run_kr_source_cycle.py --help`: exit 0, DB/cycle/output 옵션만 표시
- invalid `../escape`: exit 2, DB·보고서 0건
- 세 source run DB: exit 1, `3/4`, final cycle 0건, missing coverage 보존
- 네 success run DB 첫 실행: exit 0, final cycle 1건·complete 1건
- 같은 success cycle 재실행: exit 0, 신규 cycle 0건·전체 cycle 계속 1건
- DB, WAL/SHM, Writer lock, CSV와 Markdown 보고서: 모두 mode `600`
- 보고서 private marker 검사: cycle ID, 로컬 경로, DB명, receipt/checksum 64자리 값 노출 0건
- 실제 OpenDART/news/KIS/Alpaca/LLM/broker/외부 메시지 호출: 0건

fixture의 zero-record success run은 coordinator 상태기계와 멱등성 검증용이다. production source 가용성, 분류 정확도, 추천 품질 또는 수익성 증거가 아니다.

## 커밋

- `c66f63e feat: finalize exact KR source cycles`
- `13a34a2 feat: add KR source cycle CLI`

## 남은 순서

1. production news read-only raw-first adapter
2. KIS 국내 랭킹 read-only adapter
3. canonical volume-surge source adapter
4. 네 adapter와 coordinator를 날짜별로 실행하는 scheduler/orchestrator
5. 저장형 LLM 분류와 keyword/human audit 비교

현재 production source run은 DART만 있으므로 운영 complete cycle은 아직 만들 수 없다. DART 단독 결과는 final cycle, 새 KR Opportunity, TradeSignal 또는 주문으로 승격되지 않는다. 한국 계좌·잔고·포지션·주문 endpoint는 계속 존재하지 않는다.
