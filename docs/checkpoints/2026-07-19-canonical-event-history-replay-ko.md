# Canonical event history replay 체크포인트

## 완성 범위

- completed canonical Parquet dataset을 기존 DuckDB verifier로 다시 검증한다.
- 여러 dataset의 event를 `normalized_at` 기준 as-of 상태로 결정적으로 재생한다.
- original, correction, tombstone의 단일 chain과 동일 source/entity/provider identity를 강제한다.
- correction branch, missing target, 역방향 시각, 충돌 event ID와 tombstone 이후 후속 event를 차단한다.
- exact duplicate dataset/event는 idempotent하게 한 번만 반영한다.

## CLI

`run_canonical_event_history.py`는 반복 `--dataset`, timezone 포함 `--as-of`, `--output-dir`만 받는다. 결과 보고서는 dataset 수, 관측·활성·대체·삭제 event 수만 owner-only 파일로 남기며 path, event ID, raw payload를 출력하지 않는다.

## 안전 경계

- local Parquet와 in-memory DuckDB 외 network 접근 0건
- credential, account, order와 broker mutation 0건
- tombstone은 immutable 원본을 삭제하지 않고 as-of active projection에서만 제거
- generic replay는 provider별 deletion API와 retention 이행을 주장하지 않음

## 다음 단계

- 추정 entitlement를 실제 source 계약 발효 근거와 분리
- append-only capability/entitlement registry와 시점별 health assessment
- 뉴스·공시 provider correction cursor와 소셜 retention adapter 연결
