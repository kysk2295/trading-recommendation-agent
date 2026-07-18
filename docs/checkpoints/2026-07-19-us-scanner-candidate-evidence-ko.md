# US scanner candidate evidence 체크포인트

## 완성 범위

- mode-600 US opportunity scanner store의 latest raw Opportunity과 projection을 query-only로 읽는다.
- ready data foundation, optional security-master identity와 verified canonical candidate dataset을 다시 대사한다.
- raw receipt ID·payload SHA, dataset ID, candidate symbol·rank·score, instrument와 event 시간을 재구성한다.
- 검증된 `ranking_momentum` candidate마다 factual `scanner.candidate_selection` claim 하나를 생성한다.
- 공통 research evidence read model과 content-addressed mode-600 artifact로 투영한다.

## source 의미와 격리

- canonical event source는 `internal/us_opportunity` 하나다.
- Opportunity의 KIS ranking·NYSE halt evidence reference는 selection lineage이지 독립 normalized event가 아니다.
- 따라서 candidate selection claim은 항상 `unconfirmed`이며 수익성·추천·승격 근거가 아니다.
- output hash에는 candidate payload, source coverage, evidence reference와 research input identity를 결합한다.
- derived artifact에는 raw receipt와 source evidence reference를 복제하지 않는다.

## 운영 경로

```bash
uv run python run_us_scanner_research_evidence.py \
  --scanner-store outputs/runtime/us-opportunity-scanner.sqlite3 \
  --artifact-root outputs/runtime/us-scanner-research-evidence \
  --output-dir outputs/runtime/us-scanner-research-report
```

scanner store는 현재 사용자 소유 regular file, exact mode 600, symlink 없는 canonical path여야 한다. artifact root는 mode 700, artifact와 보고서는 mode 600이다. exact retry는 같은 artifact를 재생하고 provider·credential·account/order endpoint를 열지 않는다.

## 검증

- focused scanner evidence library/CLI: 5 passed
- related scanner/fleet regression: 25 passed
- full repository: 2321 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 수동 CLI QA

- `--help`: exit 0
- required argument 누락: exit 2
- fixture happy path와 exact replay: exit 0, unconfirmed claim 1개, artifact 1개
- provider·credential·account/order endpoint, POST/DELETE와 broker mutation: 0건

## 다음 단계

- correction/tombstone 이후 stale extraction invalidation replay
- 다음 열린 NYSE 정규장의 bounded SIP GET smoke
- production candidate의 장기 forward evidence 누적
