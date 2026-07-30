# RD-Agent·TradingAgents 실제 설치 벤치마크

- 실행일: 2026-07-22 KST
- 범위: 공식 저장소 clean clone, 격리 Python 환경 설치, CLI·offline test smoke
- 금지: LLM API, 시장 데이터 API, broker, credential 접근과 현재 제품 프로세스 변경

## 대상

| 프로젝트 | 공식 commit | 격리 Python |
|---|---|---|
| Microsoft RD-Agent | `4f9ecb005881cddc08df0124a2e894c018007679` | CPython 3.11.15 |
| TauricResearch TradingAgents | `a33fd4c0f134485a43553a2c23a63cb14adbd88f` | CPython 3.12.13 |

기존 사용자 TradingAgents clone에는 수정 파일이 있어 사용하지 않았다. 두 공식 clone은 제품 저장소 밖의
별도 benchmark 디렉터리에 만들었고 현재 trading-agent 작업 트리와 프로세스를 변경하지 않았다.

## RD-Agent 관측

- editable install은 성공했다.
- 설치 규모는 288 packages, clone과 가상환경을 합쳐 약 1.2 GiB였다.
- 공식 main은 dependency lock 없이 `requirements.txt`의 넓은 범위를 해석했다.
- `rdagent --help`는 현재 설치된 `pydantic-ai-slim 2.14.1`에
  `MCPServerStreamableHTTP`가 없어 import 단계에서 실패했다.
- quant 모듈 자체 import는 성공했다.
- quant 진입점은 help 호출에서도 Qlib data environment를 준비하며 Docker daemon에 연결했고,
  이 Mac에는 Docker가 없어 `DockerException`으로 차단됐다.
- 공식 README도 현재 Linux-only라고 명시한다.

실제 quant loop는 다음 순서를 코드로 분리한다.

```text
hypothesis generator
→ factor/model hypothesis-to-experiment
→ factor/model coder
→ runner
→ experiment feedback summarizer
→ next iteration
```

### 판정

RD-Agent의 연구 loop 경계는 채택 가치가 크다. 그러나 패키지를 현재 실행 프로세스에 import하거나
전체 의존성을 제품 환경에 합치는 방식은 부적합하다. 도입 시 별도 Linux/Docker research worker로
격리하고, 제안·패치·실험 결과만 기존 immutable experiment contract로 받아야 한다.

## TradingAgents 관측

- editable core install과 dev extra install이 모두 성공했다.
- 설치 규모는 105 packages, clone과 가상환경을 합쳐 약 304 MiB였다.
- `tradingagents --help`는 exit 0이었고 cold process 기준 약 1.33초였다.
- 외부 integration을 제외한 공식 suite는 `576 passed, 1 skipped, 1 deselected`, 69 subtests passed,
  총 96.84초였다. skip은 설치하지 않은 optional Bedrock provider였다.
- graph는 market/social/news/fundamentals analyst를 순차 실행한 뒤 bull/bear debate,
  Research Manager, Trader, aggressive/neutral/conservative risk debate, Portfolio Manager로 끝난다.
- `TradingAgentsGraph` 생성 시 LLM client를 즉시 생성한다. 실제 종목 분석은 LLM provider와
  Yahoo/Alpha Vantage 등 외부 data tool 없이는 실행할 수 없다.

### 판정

설치·CLI·checkpoint 구조는 RD-Agent보다 바로 실행하기 좋다. 다만 최종 Buy/Hold/Sell은 LLM 토론과
Portfolio Manager prompt 결과이며 byte-identical replay가 보장되지 않는다. 분석 역할·리포트 UX와
checkpoint 패턴은 참고할 수 있지만 TradeSignal, lifecycle 또는 order authority로 사용할 수 없다.
선택적으로 사용할 경우 출력은 `ResearchEvidence` 또는 아이디어 후보로만 수집하고 결정론적 전략
커널과 forward trial이 독립 검증해야 한다.

## 추가 quant/data library 실측

같은 Mac mini에서 아래 후보를 제품 저장소 밖의 `/Users/goyunseo/work/library-benchmarks/`에
격리했다. 운영체제는 Apple Silicon macOS 14.7.6이며 Docker, .NET SDK, Rust toolchain은 설치되어
있지 않았다. 시장·broker·LLM API는 호출하지 않았다.

| 후보 | 공식 기준 | 실행 결과 | 판정 |
|---|---|---|---|
| Microsoft Qlib | main `d5379c520f66a39953bad76234a7019a72796fd0`, PyPI `0.9.7` | 설치·import·`qrun --help`·synthetic risk analysis 성공 | 격리 research worker 후보 |
| NautilusTrader | develop `8da3e07b52d371fc7d4eba81ada3101a19616c43`, stable `1.230.0` | 최신 stable 설치 실패, 호환 stable `1.219.0` engine·RSI smoke 성공 | 향후 execution-kernel 비교 후보 |
| ArcticDB | master `dd6edd5e48f1dc1f4974f6dba200effad85faa47`, stable `6.18.7` | 현재 OS와 호환되는 wheel이 없어 설치 전 차단 | 보류 |
| QuantConnect LEAN | master `f9104801d9c8c1d4c0b50cd8ac81ccbb3539eb8b`, CLI `1.0.227` | CLI·backtest/live help 성공, 실제 local run은 Docker 필요 | 독립 benchmark 기준선만 사용 |
| OpenBB | develop `3e071fcc2cd9f891cac6040ae60296dba76dab46`, PyPI `4.7.2` | import·local API startup·OpenAPI 조회 성공 | 격리 data gateway 후보 |
| FinNLP | main `be4dfd5c2526e88bbb1307d444086c46a4b47e3a`, PyPI `0.0.1` | PyPI package import 실패, source-tree import만 성공 | 채택 제외 |

### Qlib

- CPython 3.12.13 환경에 192 packages, 약 766 MiB가 설치됐다.
- `qlib`, `qlib.backtest`, `qlib.workflow` import와 `qrun --help`가 각각 약 1.5초에 성공했다.
- synthetic return series에 대한 `risk_analysis`가 mean, standard deviation, annualized return,
  information ratio, max drawdown을 계산했다.
- stable dependency가 2022년부터 유지보수되지 않은 `gym 0.26.2`를 불러 NumPy 2.x 호환 경고를
  출력했다.
- 공식 sample workflow는 별도 Qlib-format data 준비가 필요하고 실시간 broker execution kernel은
  제공하지 않는다.

판정: factor/model 연구와 frozen-dataset experiment runner에는 가치가 있다. 현재 추천 서비스나
Paper lifecycle에 import하지 않고, RD-Agent와 같은 Linux research worker 안에서만 평가한다.

### NautilusTrader

- 최신 stable `1.230.0`은 macOS ARM64 wheel을 제공하지만 최소 tag가 `macosx_15_0_arm64`다.
  현재 macOS 14.7.6에서는 source build로 fallback했고 Rust compiler 부재로 정확히 실패했다.
- 마지막 호환 stable `1.219.0`은 15 packages, 약 448 MiB로 설치됐다.
- offline `BacktestEngine`은 data, risk, execution engine과 cache integrity check를 초기화하고 정상
  dispose했다. synthetic price sequence로 RSI 상태 갱신도 성공했다.
- research와 live가 같은 event-driven semantics를 쓰고 OCO 등 주문 모델을 제공하는 점은 현재
  자체 Paper lifecycle과 가장 직접적으로 비교할 가치가 있다.

판정: 지금 갈아엎는 대상은 아니다. 현재 사용자 수직 루프를 완성한 뒤 frozen ORB fixture 하나를
동일 비용·체결 규칙으로 재현해 자체 engine과 parity/complexity를 비교한다. 최신 버전 검증은 Linux
worker 또는 macOS 15 이상에서 한다.

### ArcticDB

- stable `6.18.7`의 PyPI macOS ARM64 wheel도 `macosx_15_0_arm64`만 있어 현재 환경에서 dependency
  resolution 단계에 차단됐다.
- 공식 기능은 DataFrame time series의 append, versioning, snapshot과 LMDB/S3 storage다.
- 현재 README의 production/commercial 사용 문구와 Business Source License는 실제 배포 전에 별도
  라이선스 검토가 필요하다.

판정: 현재 SQLite audit ledger와 파일 기반 market-data artifact가 사용자 수직 루프를 막고 있지 않다.
성능 측정으로 병목이 확인되기 전에는 도입하지 않는다.

### QuantConnect LEAN

- CLI `1.0.227`은 36 packages, 약 185 MiB로 설치됐고 cold `lean --help`는 약 2.0초였다.
- CLI는 backtest, optimize, research와 local live deployment를 모두 Docker command로 명시한다.
- source build는 별도 .NET 10 SDK가 필요하고 현재 Mac에는 Docker와 .NET이 모두 없다.
- 광범위한 자산·data provider·broker adapter를 제공하지만 Python 제품 안에 넣는 library가 아니라
  별도 C#/container runtime이다.

판정: 전략 결과를 비교하는 독립 backtest 기준선으로만 남긴다. 현재 제품 runtime으로 채택하면
운영 표면과 전략 이중 구현 비용이 늘어나므로 보류한다.

### OpenBB

- `openbb 4.7.2`는 102 packages, 약 196 MiB로 설치됐다.
- 첫 `from openbb import obb`는 extension registry를 build했고 약 7.5초, 최대 RSS 약 313 MiB였다.
- local `openbb-api`를 `127.0.0.1`에서 시작해 `/openapi.json`을 조회했다. 총 195 paths 중 equity 64,
  news 2, derivatives 6 paths가 등록됐고, 확인 즉시 서버를 정상 종료했다.
- 이는 provider normalization/API surface이며 자체 alpha discovery, causal evaluation 또는 order
  lifecycle은 아니다. 데이터의 실시간성·라이선스·비용은 각 provider 계약에 종속된다.
- package license가 AGPL-3.0-only이므로 제품 결합 방식은 배포 전에 검토해야 한다.

판정: 직접 provider를 무작정 늘리는 대신, 실제 가설이 요구하는 데이터 한 종류에 대해 별도 read-only
gateway로 A/B 한다. 현재 broker/order 프로세스에는 import하지 않는다.

### FinNLP

- PyPI `0.0.1`은 43 dependencies, 약 173 MiB를 설치했지만 배포 artifact에는 package code가 없고
  `FinNLP-0.0.1.dist-info`만 있어 `import finnlp`가 실패했다.
- 공식 source clone에서는 namespace import가 가능했다.
- Reddit/StockTwits 수집기는 실제 streaming/WebSocket이 아니라 page pull이며, 2023년 HTML/GraphQL
  구조와 source에 고정된 bearer/OAuth 값에 의존한다. news content는 source별 고정 XPath와 broad
  exception 처리에 의존한다.
- 공식 main의 마지막 push는 2024-07-01이고 자동화 test suite 대신 notebook 중심이다.

판정: source list와 아이디어만 참고하고 코드는 재사용하지 않는다. 소셜 수집은 provider별 정식 계약,
raw immutable capture, rate-limit, provenance와 replay test를 갖춘 자체 adapter로 구현한다.

## 추가 후보 우선순위

1. **지금 도입 없음**: 새 framework 통합보다 실시간 추천→Telegram/Hermes→Paper/shadow→평가 루프를
   먼저 완성한다.
2. **첫 격리 pilot은 Qlib**: frozen dataset에서 기존 baseline과 factor experiment 생산성을 비교한다.
3. **두 번째 parity spike는 NautilusTrader**: ORB fixture 하나로 fill/OCO/EOD 결과가 자체 kernel과
   일치하는지 확인한다.
4. **OpenBB는 데이터 가설이 생길 때만**: 예를 들어 옵션 context가 champion 가설의 필수 feature로
   등록됐을 때 read-only gateway를 비교한다.
5. **LEAN은 외부 기준선**: 동일 데이터·비용 모델의 결과 교차검증에만 사용한다.
6. **ArcticDB·FinNLP는 보류/제외**: 현재 사용자 가치와 직접 연결되는 병목을 해결하지 않는다.

## 제품 적용 결정

1. 현재 제품 코어에 RD-Agent·TradingAgents package를 추가하지 않는다.
2. 사용자 수직 루프인 실시간 추천 카드→전달→Paper/shadow→일일평가를 먼저 닫는다.
3. 그 다음 Loop Engineer v2에서 RD-Agent의 `proposal→implementation→runner→feedback` 경계를
   별도 research worker 계약으로 도입한다.
4. TradingAgents식 analyst 역할은 근거 요약과 가설 후보 생성에만 사용할 수 있다.
5. 어느 LLM도 주문, 위험한도 변경, 평가식 변경 또는 lifecycle 승격 권한을 갖지 않는다.
6. 실제 도입 전에는 같은 frozen dataset·비용·시간 예산으로 자체 baseline 대비 실험 생산성,
   실패율, 재현성과 LLM 비용을 다시 측정한다.

## 결론

이전 검토는 설계 문헌 비교에 그쳤고 정식 실행 벤치마크가 아니었다. 이번 설치로 RD-Agent,
TradingAgents와 추가 quant/data 후보 여섯 개의 실행 가능성과 통합 비용을 확인했다. 지금 바로 제품
코어에 넣을 package는 없다. Qlib·RD-Agent는 격리 연구 후보, NautilusTrader·LEAN은 execution/backtest
비교 후보, OpenBB는 가설 기반 read-only data gateway 후보이며, 어느 것도 현재 Paper 실행 코어와
사용자 수직 루프를 완성하기 전의 우선순위가 아니다.
