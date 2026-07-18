# KR normalized research evidence 체크포인트

## 완성 범위

- 기존 KR append-only ledger의 OpenDART·LS NWS normalized catalyst를 query-only로 읽는다.
- raw receipt link, terminal successful source run, adapter version, canonical normalized payload와 최초 관측시각을 다시 검증한다.
- exact projection manifest의 deterministic keyword rules로 저장 classification 전체를 재생한다.
- 검증된 positive classification만 canonical `theme_catalyst` event와 `theme.catalyst` research claim으로 투영한다.
- DART와 LS가 같은 사전등록 theme 및 exact related-symbol 집합을 지지할 때 독립 source corroboration을 계산한다.

## 인과성과 격리

- 같은 classifier/prompt version 문자열만 일치하고 실제 rules가 다르면 fail-closed다.
- source run 누락·실패·count 불일치, receipt·payload hash 불일치, noncanonical DART/LS payload와 observation 이전 분류는 차단한다.
- claim key와 output hash는 exact classification 및 source lineage에 결합된다.
- derived artifact에는 normalized 원문, evidence quote와 raw receipt reference가 없다.
- 이 artifact는 연구 근거이며 추천, TradeSignal, lifecycle 승격 또는 주문 권한이 아니다.

## 운영 경로

```bash
uv run python run_kr_research_evidence.py \
  --database outputs/kr-theme/kr-theme.sqlite3 \
  --run-manifest outputs/kr-theme/projection-run.json \
  --artifact-root outputs/kr-theme/research-evidence \
  --output-dir outputs/kr-theme/research-report
```

database는 현재 사용자 소유 regular file, exact mode 600, symlink 없는 canonical path여야 한다. artifact root는 mode 700, content-addressed artifact와 보고서는 mode 600으로 유지된다. exact retry는 동일 artifact를 재생하며 새 row나 provider 호출을 만들지 않는다.

## 검증

- focused KR extraction/CLI: 9 passed
- related DART/LS/projection regression: 128 passed
- full repository: 2316 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 수동 CLI QA

- `--help`: exit 0
- required argument 누락: exit 2
- fixture happy path와 exact replay: exit 0, corroborated claim 1개, artifact 1개
- provider·credential·account/order endpoint, POST/DELETE와 broker mutation: 0건

## 다음 단계

- US scanner candidate의 provider-specific normalized extraction
- correction/tombstone 이후 stale extraction invalidation replay
- 안전 조건이 자연스럽게 맞는 다음 열린 NYSE 정규장의 bounded SIP GET smoke
