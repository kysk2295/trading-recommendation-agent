# 미국 급등주 자율 Paper Trading Research OS

미국 급등주를 장전·장중에 시점 가용 데이터로 탐색하고, 전략 연구부터 실시간 Alpaca paper 주문, 장후 평가와 다음 가설 생성까지 하나의 프로젝트에서 반복하기 위한 연구 시스템이다.

> **현재 상태:** 분봉 수집·급등주 스캐너·ORB/VWAP/HOD/Gap-and-Go 추천·인과성 감사·과거 및 forward 평가에 더해 Alpaca Paper 시장시계·계좌·주문·포지션 GET, `trade_updates` 스트림 인증·구독·Ping/Pong, 단일 Writer 원장과 fail-closed 주문 승인 게이트까지 구현되어 있다. 수신 frame은 text/binary 원문 BLOB을 먼저 확정한 뒤 분류하며, 재시작 시 미분류 receipt를 원래 순서로 복구하고 매 연결 세대에서 REST 주문 snapshot과 대사한다. 승인 게이트는 활성 스트림 안에서 원장과 브로커 상태를 대사하고 부분체결을 한 노출로 합산하며 비용 포함 수량을 내부 산정한다. 주문 제출·교체·취소·청산 API는 아직 공개하지 않으므로 현재 실행 명령은 주문을 변경하지 않는다.

실제 자금 거래는 목표가 아니다. 앞으로 추가되는 실행 코드는 `https://paper-api.alpaca.markets`에만 연결하며 Alpaca live endpoint, 실계좌 키와 실제 주문 경로는 프로젝트에서 차단한다.

## 최종 목표

```text
과거 연구와 실패 원장
→ 장전 급등주 스캐너
→ 정규장 ORB·VWAP reclaim·HOD challenger
→ USD 30,000 위험 커널
→ Alpaca Paper 주문
→ Broker 체결 원장 + 보수적 Shadow 체결 원장
→ 장후 PF·승률·평균수익·누적수익·MDD 평가
→ 전략 자동 승격·강등
→ 주간 새 가설 생성
```

하나의 지속형 에이전트가 다음 네 모드를 순환한다.

- `Researcher`: 실패와 시장 변화에서 반증 가능한 가설 생성
- `Developer`: 같은 Strategy API로 historical/live 코드를 구현하고 테스트
- `Reviewer`: 인과성·비용·OOS·bootstrap·다중검정·인접 파라미터 안정성 판정
- `Operator`: 장중 paper 실행, 대사, kill switch와 장후 보고

에이전트는 paper 환경 안에서 전략 생성·실험·승격·중단을 자동 수행한다. 과거 원장 삭제, 실거래 활성화와 안전선 우회는 허용하지 않는다.

### Single Writer, Multiple Readers

- 실행 원장과 향후 broker paper 상태를 변경하는 프로세스는 하나뿐이다.
- Writer는 canonical SQLite 경로의 `.writer.lock`을 비차단으로 획득한 뒤에만 스키마 생성과 계좌 결합을 수행한다.
- 두 번째 Writer는 자격증명 또는 공급자 호출 전에 즉시 실패한다.
- 연구·리뷰·보고 작업은 SQLite `mode=ro`와 `query_only` 연결만 사용한다.
- 같은 intent 또는 broker event key의 immutable 필드가 다르면 재시도로 간주하지 않고 차단한다.
- Alpaca client는 현재 GET 메서드만 공개한다. 세션·주문스트림·보호청산·EOD 평탄화 게이트 전에는 POST/DELETE 경로를 노출하지 않는다.

## 하나의 프로젝트, 세 연구 Lane

| Lane | 포함 연구 | 실행 권한 | 상태 |
|---|---|---|---|
| `intraday_momentum` | ORB, 첫 눌림 VWAP reclaim, HOD breakout, Gap-and-Go | 정규장 Alpaca paper 주문 예정 | 최우선 |
| `swing_momentum` | Regend, RVOL, 신고가·모멘텀 | shadow/recommendation | 기존 결과 통합 대상 |
| `market_regime` | VIX, VIX3M, SKEW, SCR | 주문 없음 | 독립 regime 연구 |

세 lane은 같은 실험 원장과 버전 체계를 사용하지만 성과를 사후 혼합하지 않는다. VIX 필터를 ORB에 추가하려면 `ORB baseline`과 `ORB + VIX regime`을 같은 기간·위험으로 비교하는 새 challenger를 등록해야 한다.

## 승인된 Paper 운용 계약

- 기준 자본: USD 30,000 로컬 가상 equity
- 레버리지: 1.0배
- 거래당 초기 위험: 최대 USD 75 또는 현재 conservative equity의 0.25% 중 작은 값
- 종목당 명목금액: 최대 USD 6,000
- 동시 포지션: 최대 3개
- 일일 kill switch: 실현손익과 보수적 미실현손익 합계 −USD 300
- 장전: 스캔만 수행하고 주문 금지
- 정규장: paper 주문만 허용
- 폐장 30분 전 신규 진입 중단, 폐장 5분 전 전량 청산
- 오버나이트 포지션: 0

Alpaca paper 체결은 실제 호가 잔량·시장충격·지연 슬리피지·주문 대기열을 완전히 재현하지 않는다. 따라서 broker paper fill과 보수적 shadow fill을 별도 원장으로 계산하고 둘 다 통과해야 Paper Champion으로 승격한다.

## 전략 자동 승격 기준

`IDEA → HISTORICAL → EXPERIMENTAL_PAPER → CHALLENGER → PAPER_CHAMPION ↔ SUSPENDED`

Paper Champion은 최소 60거래일·100건, broker/shadow 양쪽 PF 1.15 이상, 편도 20bp 비용 후 평균수익 양수, 거래일 block-bootstrap 95% CI 하한 0 이상, DSR/PBO와 인접 파라미터 plateau를 모두 통과해야 한다. IEX-only 결과는 Challenger까지만 허용하고 SIP 또는 동등 consolidated feed 검증 뒤 시장 전체 Champion으로 승격한다.

## 문서

- [승인된 전체 설계](docs/superpowers/specs/2026-07-14-autonomous-paper-trading-research-os-design.md)
- [Alpaca paper-only 안전 기반 구현 계획](docs/superpowers/plans/2026-07-14-alpaca-paper-foundation.md)
- [현재 코드 아키텍처](docs/architecture_ko.md)
- [KIS 실시간 연결 현황](docs/kis_live_integration_ko.md)
- [런타임 인과성·안전 감사](docs/runtime_audit.md)
- [새 작업 시작 안내](CODEX_START_HERE.md)

## 현재 가능한 일

- KIS 실전 시세 서버의 NASDAQ·NYSE·AMEX 상승률·거래량 랭킹 조회
- 매 cycle의 상승률·거래량 원시 랭킹 행과 실제 선택 여부를 CSV에 누적
- 선택 후보의 완료 정규장 1분봉을 최초 관찰 시각과 함께 SQLite에 중복 없이 보존
- 한 번 선택된 후보를 해당 뉴욕 거래일 동안 watchlist에 유지하고 랭킹 탈락 뒤에도 추적
- 최초 선택 후보의 다음 1분봉 시가부터 EOD·MFE·MAE와 임계값·비용 민감도 진단
- ORB 81개 인접 파라미터의 실제 관찰시각·다음 1분봉 조건부 진입·최대 10포지션 전진성과 진단
- 첫 HOD 뒤 2~8봉 base와 1.5배 거래량 재확대를 요구하는 독립 HOD breakout paper 신호
- 첫 5분 half-gap 유지·시가·VWAP 상회로 continuation과 gap failure를 분리하는 Gap-and-Go paper 신호
- NYSE 공식 현재 거래정지·호가 없음·역전 호가·100bp spread·왕복비용 140bp 위험 게이트
- 전체 위험판정 후보를 보존하고 spread·slippage·왕복비용 27개 인접값마다 최대 10개를 재선정하는 진단
- 전체 위험판정 후보의 누적 volume·ADV를 저장하고 등락률·가격·거래대금·volume/ADV 81개 조합마다 최대 10개를 재선정하는 후보 진단
- 정규장에만 위험통과 전체 후보의 종목별 현재가상세를 조회해 전일 종가·당일 시가·시가 갭을 append-only 저장
- 급등 후보의 1분봉과 최근 일봉 문맥 수집
- 뉴욕 정규장, 당일 데이터, 3분 이내 최신 완료 봉 검증
- 5분 ORB·상대거래량·스프레드·위험폭 필터
- 조건부 진입가·손절가·1R·2R 목표가 생성
- 추천·무효·손절·목표 상태를 SQLite에 보존
- 종목별 마지막 완료 봉 checkpoint와 재시작 중복 방지
- 날짜별 영속 감시, 부분 실패 cycle 감사, 정규장 종료 시 자동 중단
- NYSE 공식 2026~2028 휴장일·13:00 조기폐장 반영, 지원범위 밖 fail-closed
- SQLite immutable outbox와 JSONL·한국어 추천 카드 projection
- KIS 알림은 스캔 직전 5분 이내 생성된 추천만 queue해 과거 추천의 지연 발송 차단
- 정규장 종료 시 마지막 완료 봉 가격으로 열린 paper 추천을 당일 `time_exit`
- 완료된 paper 거래를 편도 5/10/20bp 비용으로 집계하고 연도별 결과·bootstrap CI·장 마감 fallback 비율을 출력
- CSV 분봉 replay
- Alpaca Paper 고정 도메인·별도 mode 600 자격증명·무리다이렉트 GET client
- 계좌 식별자를 저장하지 않는 SHA-256 fingerprint 기반 실행 원장 결합
- 단일 Writer 파일 잠금, WAL, 외래키, UPDATE/DELETE 금지 trigger가 있는 append-only 원장
- 계좌·주문·포지션과 로컬 intent를 전체 필드로 비교하는 fail-closed preflight
- Alpaca Paper 시장시계 GET과 고정 WSS endpoint의 `trade_updates` 인증·구독·Ping/Pong
- text/binary frame 원문 BLOB을 먼저 append-only 저장하고 검증 결과를 별도 disposition으로 남기는 raw-first 체결 원장
- 재시작 시 미분류 raw receipt를 원래 수신 순서·연결 세대·수신시각으로 다시 분류하고 불일치·미지 주문을 격리하는 복구 경로
- heartbeat 사이 계좌·open 주문·미해결 intent 주문·최근 7일 주문·포지션을 안전하게 페이지 조회해 원장과 대사하는 GET-only REST recovery
- REST 누적 체결 snapshot과 실제 개별 execution 증거를 분리해, 세부 체결이 누락되면 이후 신규 주문 승인을 계속 차단하는 projection
- 스트림의 두 Pong 사이 REST·원장 재대사와 공개 의존성 주입 없는 활성 세션 전용 정규장·최신 완료 1분봉·전체 포트폴리오 위험 승인 상태기계

## 실행

Alpaca Paper 안전 기반은 별도 `~/.config/trading-agent/alpaca-paper.env`의 paper 계정 키만 사용한다. 파일 권한은 정확히 `600`이어야 한다.

```text
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
```

최초 한 번 실행 원장과 현재 빈 Paper 계정을 결합한다. 이 명령의 외부 호출은 계좌·미체결 주문·포지션 GET뿐이며 broker 상태를 변경하지 않는다.

```bash
./run_alpaca_paper_bootstrap.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/bootstrap/latest
```

이후 시작 전 안전 대사를 수행한다. preflight는 로컬 실행 원장을 생성하거나 수정하지 않으며, 미결합·계좌 변경·알 수 없는 주문·주문 필드 불일치·미해결 intent·열린 포지션을 발견하면 준비 상태를 거부한다.

```bash
./run_alpaca_paper_preflight.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/preflight/latest
```

`bootstrap`과 `preflight` 모두 실제 주문을 제출하지 않는다.

주문 스트림과 REST를 한 연결 세대 안에서 실제 확인하려면 다음 명령을 실행한다. 스트림 Ping → 계좌·미체결·포지션·시장시계 GET → 단일 SQLite 원장 스냅샷과 대사·포트폴리오 집계 → 스트림 Ping 순서로 검사한다. 장이 닫혀 있어도 이 읽기 전용 probe가 정상이면 성공하지만, 결과는 세션 종료 뒤 주문 승인에 재사용할 수 없고 보고서도 현재 주문 승인을 주장하지 않는다.

```bash
./run_alpaca_paper_readiness.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/readiness/latest
```

이 명령도 POST/DELETE를 호출하지 않는다. 실제 주문 승인에는 열린 런타임 세션 안에서 5초 이내 두 Pong과 같은 `connection_epoch`의 REST·원장 대사를 먼저 통과해야 한다. 그 뒤 브로커와 로컬 정규장 일치, 폐장 30분 전 이전, 방금 완성된 정확한 현재 정규장 1분봉, 현재 스트림 상태, 부분체결 포함 전체 포트폴리오 위험을 순서대로 검사한다. 기존 노출은 원장 손절거리와 왕복 최소 20bp 비용으로 다시 계산해 종목별 위험·명목 한도를 적용하고, 신규 수량은 여기에 관측 spread까지 포함해 내부 산정한다.

재시작 직후에는 다음 명령으로 미분류 raw receipt를 먼저 처리하고, 같은 WSS 연결 세대의 두 heartbeat 사이에서 REST 주문 snapshot을 원장에 저장한다.

```bash
./run_alpaca_paper_recovery.py \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/recovery/latest
```

복구 명령은 WSS 인증·구독과 REST GET만 사용한다. 정상 종료는 복구 snapshot 저장과 현재 로컬 차단 사유 부재만 뜻하며, 종료된 세션의 결과로 새 주문 admission을 승인하지 않는다. REST 누적 체결량만 있고 개별 execution이 없으면 aggregate 상태는 복원하되 체결 상세를 완전하다고 만들지 않는다. immutable 충돌은 후속 snapshot으로 자동 해소하지 않고 감사 가능한 별도 체결 자료가 생길 때까지 차단한다. Alpaca WSS에는 replay cursor나 처리 high-water 보장이 없으므로 이 명령 하나가 이벤트 무손실을 증명하지도 않는다.

Alpaca 과거 SIP 1분봉 아카이브:

```bash
./run_alpaca_minute_archive.py \
  --start 2026-06-12 --end 2026-06-12 \
  --symbols-file examples/alpaca_probe_symbols.txt
```

`--symbols-file`을 생략하면 Alpaca Assets API의 현재 `active+inactive` 미국 상장 종목을 조회하고 유니버스 스냅샷을 저장한다. OTC는 제외한다. 데이터는 날짜·유니버스 지문·종목 묶음별 `CSV.gz`와 메타데이터로 즉시 확정되며, 재실행은 동일 날짜·동일 유니버스·동일 종목 묶음의 완료 체크포인트를 재사용한다. 장전 04:00부터 장후 20:00 ET까지 `feed=sip`, `1Min`, `adjustment=raw`, 날짜별 `asof`로 요청한다. 무료 한도에 맞춰 요청 시작 간격을 0.31초 이상으로 유지하고 RSS가 10GiB에 도달하면 중단한다.

자격증명은 `~/.config/trading-agent/alpaca.env`에 다음 이름으로 저장하고 권한을 `600`으로 설정해야 한다.

```text
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
```

Alpaca의 현재 Assets API는 비활성 종목을 포함하지만 완전한 과거 point-in-time 종목마스터는 아니다. 따라서 상장폐지·티커 변경·기업행위 편향은 별도 품질 진단 대상으로 유지한다.

KIS paper 스캔:

```bash
./run_kis_paper_scan.py --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy vwap_reclaim --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy hod_breakout --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy gap_and_go --top 3 --max-pages 10
./run_kis_daytime_scan.py --top 10
./run_session_continuity.py outputs/live_sessions/<거래일>
```

`orb`, `vwap_reclaim`, `hod_breakout`, `gap_and_go`는 별도 전략 이름과 출력 폴더로 실행한다. 성과를 합쳐 유리하게 만들지 않으며, 기본값은 `orb`다.

KIS 날짜별 paper 감시:

```bash
./run_kis_paper_watch.py --wait-until-open --max-wait-minutes 720 \
  --collect-premarket --premarket-interval-seconds 300 \
  --cycles 390 --interval-seconds 60 --top 10 --max-pages 1
```

장 전에 실행하면 최대 대기시간 안에서 뉴욕 정규장 개장을 30초 간격으로 확인한 뒤 시작한다. `--wait-until-open`을 생략하면 폐장 중에는 API를 호출하지 않고 즉시 종료한다.
`--collect-premarket`을 사용하면 04:00~09:29 ET에는 전용 읽기 전용 CLI가 원시 랭킹과 위험판정만 기본 5분 간격으로 저장하고, 09:30부터 기존 전략 감시로 전환한다. 장전에는 추천 DB·후보 watchlist·분봉 전략 평가를 만들지 않는다.
`--top`은 위험 통과 뒤 스냅샷별 포트폴리오 상한이며 기본값은 10이다. 조건을 충족하지 않는 종목을 강제로 채우지 않는다.
`--max-pages`는 반복 cycle의 종목별 분봉 페이지 상한이며 기본값은 1이다. 최근 120개 봉을 다시 읽고 앞선 완료 봉은 SQLite에 누적해 매분 과거 10페이지를 반복 요청하지 않는다.

신규 추천이 있으면 같은 출력 폴더에 다음 파일이 생성된다.

- `recommendation_alerts.jsonl`: 외부 알림 어댑터용 구조화 카드
- `recommendation_alerts_ko.md`: 사람이 확인하는 한국어 카드
- `paper_recommendations.sqlite3`: 중복 방지의 원본 immutable outbox

추천 유무와 관계없이 `kis_ranking_snapshots.csv`에는 관찰 시각, 랭킹 출처, 거래소, 원천 순위, 가격·등락률·호가·거래량·거래대금과 실제 선택 여부가 append-only로 저장된다. `kis_ranking_request_coverage.csv`에는 거래소×상승률/거래량 요청 6개의 성공 여부와 행 수·실패 사유를 cycle마다 남긴다. 한 요청이 실패해도 성공한 거래소 후보는 계속 shadow 평가하지만, 보고서는 `부분 모집단`으로 표시되고 child 종료코드는 1을 유지한다. `market_risk_screen.csv`에는 공식 현재 거래정지, 호가 결손·역전, spread, 편도 20bp 슬리피지 예비비를 합친 예상 왕복비용과 최종 선정 여부를 별도로 누적한다.

장전 수집을 사용하면 같은 구조의 `premarket_ranking_snapshots.csv`와 `premarket_risk_screen.csv`, child 종료 상태를 담은 `premarket_watch_cycles.csv`가 추가된다. KIS 누적 거래량은 실제 세션 reset을 검증하기 전에는 장전 전용 RVOL로 해석하지 않는다.

`run_kis_daytime_scan.py`는 KIS 미국 주간거래 시간에만 `BAQ/BAY/BAA` 랭킹을 읽는다. `daytime_ranking_snapshots.csv`, `daytime_risk_screen.csv`, `daytime_session_map.csv`를 별도로 저장하고 이 가격을 정규장 시가·opening gap으로 사용하지 않는다.

`run_session_continuity.py`는 세션별 전체 위험판정 후보를 비교해 주간거래→프리마켓→정규장 재등장률을 별도 CSV와 한국어 보고서로 만든다. 미래 세션이 아직 없으면 연속률을 공란으로 두며 수익성 결과로 해석하지 않는다.

`kis_opening_gap_cycles.csv`는 정규장 여부와 시가 조회 성공·실패 수를 남긴다. `kis_opening_gap_snapshots.csv`는 정규장 중에만 포트폴리오 한도 밖을 포함한 위험통과 후보의 전일 종가·당일 시가·갭·현재/전일 거래량을 저장한다. 폐장에는 이전 거래일 시가를 현재 시가로 소급하지 않는다. 이 값은 아직 추천 선정에 사용하지 않으며 forward 표본이 쌓인 뒤 인접값을 별도로 승격한다.

같은 SQLite의 `candidate_minute_bars`에는 선택 후보의 완료 정규장 OHLCV·거래대금과 최초 관찰 시각을 저장한다. 동일 거래소·종목·분봉을 다시 조회해도 최초 행을 유지해 당시 알려진 데이터 경로를 재현한다.

정규장에서 한 번 선택된 종목은 `tracked_candidates`에 거래일 단위로 저장한다. 이후 상위 랭킹에서 빠지면 새 추천은 만들지 않고 분봉 보존과 이미 열린 추천의 손절·목표 상태 갱신만 계속한다. 폐장·휴장에는 watchlist를 만들거나 이전 거래일 후보를 불러오지 않는다.

CSV replay:

```bash
./run_trading_agent_replay.py examples/example_intraday.csv \
  --output-dir outputs/replay/example
```

누적 paper 성과 보고서:

```bash
./run_paper_metrics.py outputs --output-dir outputs/paper_metrics/latest
```

`active` 뒤 손절·2R·당일 종료가 확인된 추천만 거래로 집계한다. 출력은 `paper_metrics.csv`, `paper_yearly_metrics.csv`, `paper_trades.csv`, `paper_metrics_ko.md`이며, 현재 저장된 2건은 기능 검증용 QA 표본이므로 수익성 증거가 아니다. 날짜별 watch가 공식 정규장 종료 뒤 끝나면 같은 CLI를 자동 실행해 세션의 `paper_metrics/`에 저장하고 `post_session_metrics_cycles.csv`에 종료코드를 남긴다. 장중 단발 watch나 DB가 없는 실행은 자동 일일 평가를 만들지 않는다.

장마감 Paper 연구 원장:

```bash
./run_daily_research_record.py outputs/live_sessions/<거래일> \
  --session-date YYYY-MM-DD --strategy orb
```

장마감 metrics가 성공하면 watch가 이 CLI도 순차 실행한다. 세션별 불변 JSON, 상위 `daily_research_ledger.jsonl`, 한국어 요약과 별도 종료코드를 남기며 코드·데이터·평가기·파라미터·비용·품질 계보를 함께 고정한다. 랭킹 6개 요청과 watch cycle이 완전하지 않거나 실패 cycle이 있으면 해당 날짜를 적격 forward day로 세지 않는다. 최소 60거래일·100건 외에도 broker ledger, block bootstrap, DSR/PBO, 인접 파라미터 평탄성, SIP 검증이 모두 없으므로 자동 승격과 자동 주문은 항상 금지한다.

스캐너 forward outcome 진단:

```bash
./run_scanner_forward_metrics.py outputs/live_sessions/<거래일> \
  --output-dir outputs/scanner_forward_metrics/<거래일>
```

종목·거래일 최초 실제 선택 1건만 사용하고, 다음 완전한 1분봉 시가부터 공식 close 직전 봉까지 연속 경로가 있을 때만 5/15/30분·EOD·MFE·MAE를 계산한다. 중도절단 경로는 별도 표시하고 성과 0이나 완료 거래로 바꾸지 않는다.

ORB forward paper 진단:

```bash
./run_orb_forward_metrics.py outputs/live_sessions/<거래일> \
  --output-dir outputs/orb_forward_metrics/<거래일>
```

OR 1/5/15분, 거래량 1.0/1.5/2.0배, 손절폭 0.75/1.0/1.25배, 목표 1R/2R/3R의 81개 조합을 비교한다. 각 조합은 편도 5/10/20bp, PF·승률·평균·누적·MDD·bootstrap CI와 연도별 결과를 출력한다. 랭킹 응답 뒤 실제 분봉 조회가 끝난 시각 이후만 신호로 인정하고, 다음 완전한 1분봉부터 조건부 진입하며 동시 보유는 상승률·거래대금 순 최대 10개다.

시장위험 인접값 진단:

```bash
./run_market_risk_sensitivity.py outputs/live_sessions/<스캔폴더>
```

spread 80/100/120bp, 편도 slippage 10/20/30bp, 최대 왕복비용 100/140/180bp의 27개 조합을 비교한다. 각 조합은 저장된 전체 위험판정 후보에서 다시 필터링하고 최대 10개를 재선정한다. 출력은 후보 보존율과 선택 목록이며 수익성 백테스트가 아니다.

스캐너 후보 인접값 진단:

```bash
./run_scanner_candidate_sensitivity.py outputs/live_sessions/<스캔폴더>
```

등락률 4/6/8%, 최대가격 20/50/200달러, 누적 거래대금 0.5/1/2백만 달러, 시점 누적 거래량/ADV 0.05/0.10/0.20의 81개 조합을 전체 위험판정 후보에서 다시 선정한다. 후행수익·PF 결과가 아니며 KIS 랭킹에 없는 전체 후보 opening gap은 계산하지 않는다.

테스트:

```bash
uv run pytest -q
```

## 폴더 구조

```text
trading-recommendation-agent/
├── trading_agent/        추천·전략·리스크·공급자·Paper 실행 기반
├── scr_backtest/         KIS 분봉 저수준 어댑터
├── tests/                인증·최신성·인과성·추천 회귀 테스트
├── docs/                 설계·실데이터 연결·런타임 감사
├── examples/             공급자 독립 분봉 예시
├── artifacts/            검증된 실행 결과 표본
├── outputs/              새 실행 결과, Git 제외
├── run_kis_paper_scan.py
├── run_kis_paper_watch.py
├── run_alpaca_minute_archive.py
├── run_alpaca_paper_bootstrap.py
├── run_alpaca_paper_preflight.py
├── run_alpaca_paper_readiness.py
├── run_alpaca_paper_recovery.py
├── run_kis_daytime_scan.py
├── run_session_continuity.py
├── run_paper_metrics.py
├── run_daily_research_record.py
├── run_orb_forward_metrics.py
├── run_market_risk_sensitivity.py
├── run_scanner_candidate_sensitivity.py
├── run_scanner_forward_metrics.py
└── run_trading_agent_replay.py
```

## 보안

자격증명은 프로젝트 안에 저장하지 않는다. KIS와 Alpaca market data는 각각 `~/.config/trading-agent/kis.env`, `~/.config/trading-agent/alpaca.env`를 사용한다. Paper 계좌 조회와 향후 실행은 별도 `~/.config/trading-agent/alpaca-paper.env`만 사용한다. Paper 파일은 일반 파일·현재 사용자 소유·정확한 mode `600`을 모두 요구한다.

Alpaca paper 코드는 trading base URL을 설정값으로 자유롭게 받지 않는다. REST는 정확한 `https://paper-api.alpaca.markets`, 주문 스트림은 정확한 `wss://paper-api.alpaca.markets/stream`만 허용하고 다른 URL은 자격증명 전송 전에 거절한다. REST 리다이렉트도 따르지 않는다. KIS는 계속 시세 조회 전용이다.

원본 Notion 페이지에 평문으로 남아 있는 기존 앱 키·시크릿은 운영 전 재발급하고 삭제해야 한다.

## 현재 한계

- KIS 랭킹 상위 후보를 감시하며 미국 전체 종목 원시 스트림을 완전히 열거하지 않는다.
- 영속 감시는 NYSE가 게시한 2026~2028 캘린더를 반영한다. 2029년 이후 일정과 임시 휴장 변경은 표를 갱신하기 전까지 안전하게 닫힌다.
- 로컬 추천 카드 outbox는 구현했지만 Telegram·Codex 외부 전송 어댑터는 아직 연결하지 않았다.
- 장 마감 `time_exit` 가격은 실제 MOC가 아니라 마지막 처리 완료 봉 fallback이므로 성과 집계에서 별도 구분해야 한다.
- 성과 대시보드는 구현됐지만 실제 정규장 paper 거래가 아직 누적되지 않아 수익성은 검증되지 않았다.
- 현재 paper 추천 전략은 ORB, 첫 눌림목 VWAP reclaim, 첫 HOD 거래량 돌파, Gap-and-Go 지속이다. 모두 구현·인과성 회귀만 완료됐고 실제 정규장 성과는 아직 0건이다.
- Alpaca Paper GET 대사, 주문 스트림 control plane·heartbeat, raw-first `trade_updates` 영속화·격리·재시작 복구, 활성 세션 기반 정규장/current-bar/portfolio 승인 게이트는 구현됐다. 다만 readiness와 ingestion이 아직 하나의 장수명 스트림 소유자·동일 ledger generation으로 직렬화되지 않았고 Alpaca WSS에는 replay cursor가 없다. 실제 주문 제출, account activity 기반 체결 정정·취소 복구, 부분체결 보호주문, kill switch와 마감 전 강제청산도 아직 구현되지 않았다.
- PIT free float가 없으므로 low-float를 추정하지 않는다. 거래대금 기준은 저유동성 대리필터이며 float 필터가 아니다.
- ORB 신호 시각은 돌파 1분봉의 시작이 아니라 봉 완료 뒤로 기록한다. 15:59 봉처럼 확인 가능 시각이 정규장 close인 신호는 신규 추천에서 제외한다.

새 Codex 작업은 [CODEX_START_HERE.md](CODEX_START_HERE.md)를 먼저 읽고 이어서 진행한다.
