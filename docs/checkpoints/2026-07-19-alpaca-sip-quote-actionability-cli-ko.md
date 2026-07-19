# Alpaca SIP quote actionability operational CLI 체크포인트

## 완료 계약

- canonical private manifest가 base conditional publication, READY intraday snapshot, dynamic subscription plan과 scan start 전체를 content identity로 고정한다.
- manifest validation은 current base, READY status, plan time causality와 exact instrument-symbol binding을 확인한다.
- manifest 파일은 mode 600/current owner/single hard link, canonical sorted JSON bytes와 immutable conflict를 강제한다.
- CLI는 `--manifest`, `--receipt-store`, `--actionability-store`, `--output-dir`만 받고 provider credential이나 endpoint 인자를 받지 않는다.
- manifest 검증 후 stored receipt projector를 실행하며 incomplete/private-file/mismatch failure는 sanitized blocked report와 actionability append 0으로 닫는다.
- success report는 terminal status, new/replay, derived 여부와 account/order mutation 0만 기록하고 symbol, price, plan/assessment ID를 출력하지 않는다.

## 검증

- manifest + CLI + projector focused: **8 passed**
- dynamic actionability related: **49 passed**
- full suite: **2507 passed**
- Ruff, changed-file format, basedpyright 0 errors/0 warnings, compileall, no-excuse rules 통과
- CLI `--help`, missing required args, non-private receipt blocked path, fixture happy path와 exact replay 수동 QA 통과
- actual process QA: first exit 0, replay exit 0, `validated_waiting`, append replay, derived yes, mutation 0
- provider·credential·network·account/order endpoint 호출과 mutation 0건

## 남은 경계

- manifest writer API와 operational CLI는 완성됐지만 runtime fleet/dynamic connection owner가 manifest를 자동 생성·dispatch하지 않는다.
- current conditional signal과 READY snapshot의 exact pairing은 다음 runtime orchestration checkpoint에서 자동화한다.
- 운영 dynamic WebSocket smoke는 열린 NYSE 정규장, explicit read-only arm과 private market-data credential이 자연스럽게 맞을 때만 별도 수행한다.
- CLI 결과는 Paper forward-validation evidence이며 Telegram delivery나 Paper order intent가 아니다.
