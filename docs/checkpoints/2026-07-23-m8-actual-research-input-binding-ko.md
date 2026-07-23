# M8 실제 연구 입력 결속 체크포인트

작성 시각: 2026-07-23 14:50 KST

## 닫은 결손

기존 causal dataset materializer는 exact CSV SHA와 source-session receipt를 만들었고 v2 loop는 manifest의 `input_sha256`와 READY foundation SHA를 요구했다. 그러나 actual dataset, entitlement, queue card를 대사해 두 SHA를 같은 v2 manifest에 등록하는 안전한 운영 경계가 없었다. 운영자가 fixture foundation을 복사하거나 CSV와 무관한 READY foundation을 수동 결합할 여지가 있었다.

## 구현 계약

- strict materializer가 만든 mode-600 CSV와 canonical receipt만 읽는다.
- CSV filename·SHA-256·bar 수·NYSE session date·session budget을 receipt와 다시 대사한다.
- entitlement는 데이터에서 추정하지 않고 별도 mode-600 `DataEntitlement` 계약으로 요구한다.
- `fixture` provider, US equities·`minute_bar`·`historical_research`가 아닌 계약은 차단한다.
- exact content-addressed source queue를 읽고 `strategy_design` route인 card만 사용한다.
- v2 등록 시각이 CLI의 실제 UTC 관측 시각보다 미래이면 evidence read와 publication 전에 차단한다.
- 최대 세 개의 서로 다른 VWAP/HOD/Gap-and-Go binding을 전략별 US day-trading lane foundation으로 만든다.
- capability는 검증된 strict forward-session bounded universe, `file_batch + local_derived`, event-time, actual receipt의 earliest date와 100% materialized eligible rows만 선언한다.
- 각 foundation이 실제 `READY`인지 publication 전에 data gate로 평가한다.
- exact CSV SHA, queue snapshot/card, foundation SHA를 v2 manifest에 등록한다.
- dataset receipt SHA, entitlement raw-contract SHA, queue/card, foundation SHA, manifest SHA를 별도 content-addressed 결속 receipt에 남긴다.
- 모든 산출물은 mode 600이며 exact replay는 기존 파일을 교체하지 않는다.
- provider, credential, account, broker, order endpoint를 import하거나 호출하지 않는다.

## 검증

- 단일 전략 happy path에서 READY foundation, exact CSV SHA가 등록된 v2 manifest와 결속 receipt를 발행했다.
- exact replay는 `created=false`였고 모든 산출물 mode는 600이었다.
- fixture entitlement는 publication 전에 차단되고 output directory도 생기지 않았다.
- 세 개의 독립 queue card를 VWAP/HOD/Gap-and-Go foundation 세 개와 하나의 v2 manifest에 결속했다.
- CLI `--help`, malformed binding exit 2, happy path exit 0과 private aggregate report를 확인했다.
- 관련 dataset·source-backed loop·foundation·data-gate 회귀를 포함한 타깃 41개와 전체 3,404개 테스트, Ruff와 basedpyright 0/0, no-excuse, compileall이 통과했다.

## 아직 남은 운영 증거

KIS `us_candidate_minute` 원천을 로컬 historical research에만 쓰는 명시 계약
`examples/data/kis-us-candidate-minute-historical-research-v1.json`을 등록했다. 실시간
사용은 허용하지 않고, 원천 재배포도 허용하지 않으며 파생 산출물만 보존 대상으로
선언한다. 이 계약은 프로젝트의 보수적인 로컬 보존·삭제 정책이며 KIS 약관에 대한
법률적 해석이나 제3자 재배포 권리를 뜻하지 않는다. 운영 복사본은 mode 600으로
`outputs/experiment_control/source_intraday/contracts/`에 게시했다.

이 코드는 actual-ready 결속 도구다. 2026-07-23 시점 보존된 실제 네 세션은 strict quality gate를 통과하지 못했으므로 actual CSV, actual READY foundation, historical trial은 여전히 0건이다. 예약된 clean forward session이 성공한 뒤 이 CLI로 실제 SHA를 발행해야 한다. 결과가 `hold` 또는 실패여도 품질 gate나 Reviewer 기준을 완화하지 않는다.
