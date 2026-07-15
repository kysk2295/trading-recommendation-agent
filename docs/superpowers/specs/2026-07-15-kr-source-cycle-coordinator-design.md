# KR Multi-Source Cycle Coordinator 설계

## 1. 범위

이 설계는 다중 시장 Research OS Milestone 3과 KR Theme Phase T0의 source coverage 확정 단계다.

```text
네 immutable KrSourceCollectionRun
-> exact source set와 cycle identity 검증
-> coverage projection
-> immutable KrCatalystCollectionCycle append
-> redacted source별 coverage CSV와 한국어 요약
```

이번 단계는 이미 원장에 terminal로 저장된 `news`, `dart`, `kis_ranking`, `volume_surge` source run만 읽는다. 공급자 수집, 자격증명, HTTP, LLM, 현재가, Opportunity, TradeSignal, shadow fill과 국내 주문은 포함하지 않는다.

## 2. 비교한 접근

### 2.1 존재하는 source run만 성공 cycle로 축약 - 기각

누락된 source가 보이지 않고 적격 관측일이 과대 계산된다. 세 source 성공을 네 source 완전 수집으로 오인할 수 있다.

### 2.2 누락 source를 synthetic 실패 run으로 만들어 cycle 확정 - 기각

실제로 실행되지 않은 adapter를 실행 실패로 위조하고 immutable cycle을 너무 일찍 닫는다. 나중에 진짜 terminal run이 도착해도 같은 cycle을 고칠 수 없다.

### 2.3 네 terminal run이 모두 존재할 때만 exact cycle 확정 - 채택

source run 네 개가 모두 존재해야 cycle을 append한다. 네 run 중 실패가 있으면 해당 `record_count`와 `failure_code`를 그대로 coverage에 보존하고 `complete=false`로 확정한다. source가 하나라도 없으면 cycle은 만들지 않고 nonzero 보고만 남긴다.

## 3. Coordinator 계약

- 입력은 `collection_cycle_id`와 KR ledger path뿐이다.
- source run 집합은 `KrCatalystSource` 네 값과 정확히 일치해야 한다.
- 각 run의 `collection_cycle_id`는 입력 ID와 같아야 한다.
- cycle `started_at`은 네 run의 최소 `started_at`, `completed_at`은 최대 `completed_at`이다.
- coverage는 source 이름 오름차순이며 각 run의 source, status, record count와 failure code를 그대로 사용한다.
- 기존 `KrThemeWriter.append_cycle()`이 observation count와 cycle 시간 범위를 다시 검증한다.
- 이미 같은 cycle이 있으면 exact replay no-op이고 내용이 다르면 conflict로 차단한다.
- source run이 하나라도 누락되면 ledger mutation은 없다.

Coordinator는 source run을 생성하거나 adapter를 호출하지 않는다. source run 수집 순서와 동시성도 결정하지 않으며 terminal evidence를 최종 cycle로 투영하는 single-writer 단계만 담당한다.

## 4. 실패 의미

| 상태 | DB 결과 | CLI 종료 |
|---|---|---|
| source run 누락 | cycle 미생성 | nonzero |
| 네 run 모두 성공 | `complete=true` cycle | 0 |
| 네 run 중 terminal 실패 | `complete=false` cycle | nonzero |
| observation/count/time 불일치 | cycle 미생성 | nonzero |
| exact 재실행 | 새 행 없음 | 기존 cycle 상태에 따름 |
| 기존 immutable cycle과 충돌 | 변경 없음 | nonzero |

부분 수집 catalyst, receipt와 failed source run은 삭제하거나 수정하지 않는다. 실패 cycle도 이후 분류·Opportunity 적격일로 사용할 수 없지만 감사와 원인 분석을 위해 보존한다.

## 5. CLI와 보고서

`run_kr_source_cycle.py`는 다음 인자를 명시적으로 받는다.

```text
--collection-cycle-id
--database
--output-dir
```

CLI는 자격증명·network 모듈을 import하지 않는다. 보고서는 source, `success`/`failed`/`missing`, record count와 안전한 failure code만 포함한다. cycle ID, DB 경로, receipt ID, checksum, 원문, 회사명·기사명·공시명과 provider 메시지는 쓰지 않는다. CSV와 한국어 요약 파일은 mode `600`으로 원자적으로 교체한다.

누락 source도 보고서에는 `missing`과 `missing_source_run`으로 표시하지만 DB에 synthetic coverage나 source run을 기록하지 않는다.

## 6. 검증

- 네 성공 run에서 exact complete cycle 생성
- terminal 실패 run의 count/failure code 보존과 nonzero 상태
- source 누락 시 cycle mutation 없음
- observation count와 time 불일치 fail-closed
- exact 재실행 no-op, 기존 cycle conflict 차단
- 기존 schema v1 DB를 Writer가 먼저 v2로 migration한 뒤 source run 조회
- CLI `--help`, 오입력, missing/failed/success fixture DB, report redaction와 mode `600`
- 전체 pytest, Ruff, basedpyright

QA는 local SQLite fixture만 사용하며 OpenDART, 뉴스, KIS, Alpaca, LLM, broker와 외부 메시지 호출은 0건이어야 한다.

## 7. 후속 범위

1. production news read-only raw-first adapter
2. KIS 국내 랭킹 read-only adapter
3. canonical volume-surge source adapter
4. 네 adapter를 한 날짜 cycle로 실행하는 scheduler/orchestrator
5. 저장형 LLM 분류와 keyword/human audit 비교

한국 계좌·잔고·포지션·주문 endpoint는 계속 범위 밖이다.
