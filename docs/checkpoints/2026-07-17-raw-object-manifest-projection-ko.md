# Raw Object Manifest Projection 체크포인트

- 날짜: 2026-07-17
- 범위: Institutional Multi-Market Quant Research OS Milestone 3.1
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건
- 기존 SQLite schema/collector 변경: 없음

## 구현

- `RawReceipt`는 exact SHA-256과 raw bytes를 생성 시 검증하며 source, market date, received time, opaque receipt ID를 immutable contract로 보존한다.
- raw bytes와 synthetic fixture의 reversible base64는 `repr`, public Pydantic dump, emitted manifest, CLI report와 terminal output에서 제외한다.
- `project_raw_receipt_partition`은 canonical receipt 순서를 보존하고 source, market date, parent generation, receipt metadata와 payload digest만으로 content-addressed `RawObjectPartitionManifest`를 만든다.
- 동일 입력은 같은 manifest ID/content hash를 만들며, payload, receipt identity, received time, source, market date 또는 parent generation 변경은 다른 manifest가 되거나 strict validation에서 차단된다.
- projection은 exact `RawReceipt`와 exact payload wrapper만 받고 mutable lookalike, subclass, tampered hash, mixed partition, noncanonical order, schema-version tampering을 차단한다.
- public manifest JSON은 deterministic round-trip을 지원한다. raw input export는 payload redaction 때문에 의도적으로 receipt 재구성에 사용하지 않는다.
- local fixture CLI는 `fixture.` source namespace만 받고 provider, credential, broker, collector 또는 receipt-store adapter를 import하지 않는다.
- CLI output parent는 current-user-owned exact mode `0700` real directory여야 한다. macOS `renamex_np(RENAME_EXCL)`로 완성된 private staging directory를 empty target도 대체하지 않고 publish한다.
- final directory는 mode `0700`, manifest와 aggregate summary는 mode `0600`이다. exclusive publish 뒤 parent durability check가 실패하면 sanitized error를 반환하되 complete final output은 삭제하지 않는다.

## 검증

- focused raw manifest/projection/CLI: `55 passed`
- 전체 회귀: `1829 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- `git diff --check`: 통과
- 수동 CLI: `--help` exit 0, missing input exit 1, synthetic fixture happy path exit 0
- 수동 happy output: directory mode `0700`, manifest/summary mode `0600`, raw payload/base64 미포함
- provider network, credential loading, broker mutation: 모두 0건

## 다음 경계

M3.2에서만 existing US Paper trade-update와 KR source receipt ledger를 read-only adapter로 연결한다. US daily/minute archive는 아직 raw receipt가 아니므로 이 checkpoint에서 source로 가장하지 않는다. 이후 M3.3 typed Parquet canonical writer, M3.4 DuckDB deterministic replay hash conformance를 순서대로 추가한다.
