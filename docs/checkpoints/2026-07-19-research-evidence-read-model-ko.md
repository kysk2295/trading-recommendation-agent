# Research evidence read model 체크포인트

## 완성 범위

- provider extractor 결과를 active canonical event에 결합하는 immutable extraction 계약
- deterministic extractor version·output hash와 LLM model·prompt·output hash 계약
- claim key와 exact canonical entity set별 current/baseline window grouping
- 독립 source corroboration, supporting/disputing conflict, novelty·burst 판정
- content-addressed mode-600 derived artifact와 aggregate-only CLI report

## 인과성과 무결성

- event ID, source ID, content hash, raw receipt reference와 entity set이 모두 exact match해야 한다.
- extraction은 event normalization 전이나 read-model as-of 뒤일 수 없다.
- tombstone event, duplicate event/evidence와 한 claim key의 mixed kind는 차단한다.
- burst는 단순 count가 아니라 current/baseline window 길이를 정규화한 rate와 명시적 threshold로 계산한다.
- artifact loader는 canonical bytes, embedded content SHA와 content-addressed filename을 모두 재검증한다.

## 프라이버시와 권한

- input bundle은 mode 600/current owner/regular file/no-symlink만 허용한다.
- artifact root는 mode 700, immutable artifact와 report는 mode 600이다.
- artifact에는 raw receipt reference, 원문 quote, provider payload를 복제하지 않는다.
- extractor, network, credential, account/order endpoint와 broker mutation은 포함하지 않는다.
- corroborated는 독립 source agreement이며 전략 수익성, 추천 또는 lifecycle 승격을 뜻하지 않는다.

## 수동 QA

- `--help`: exit 0, provider·arm·주문 옵션 없음
- mode-644 input: exit 1, artifact 생성 없음
- 2-source fixture: exit 0, corroborated claim 1
- exact retry: 같은 content-addressed artifact replay
- root/artifact/report mode: 700/600/600
- derived artifact의 `raw_receipt_ref`: 0건

## 검증

- `pytest`: 2301 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 다음 단계

- KR DART·LS news typed extraction adapter
- US scanner candidate·SIP price/volume anomaly extraction adapter
- provider correction/tombstone 뒤 extraction invalidation과 read-model replay
