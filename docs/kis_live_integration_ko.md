# KIS 실시간 Paper 추천 연결 현황

확인일: 2026-07-13
상태: **읽기 전용 실데이터 연결 성공, 날짜별 영속 forward runner 구현 완료, 정규장 장기 축적 대기**

## 완성된 기능

- 한국투자증권 실전 시세 서버 OAuth 인증과 23시간 로컬 토큰 캐시
- NASDAQ·NYSE·AMEX 상승률 상위와 거래량 상위 랭킹 결합
- 상승률·거래량 원시 랭킹 행과 실제 선택 여부를 cycle별 CSV에 append-only 저장
- 선택 후보의 완료 정규장 1분봉·거래대금·최초 관찰 시각을 SQLite에 중복 없이 저장
- 최초 선택 후보를 당일 watchlist로 유지하고 랭킹 탈락 뒤에는 신규 신호 없이 추적
- 실제 선택 입력 행·응답 후 관찰 시각·최초 선택 1건 기준 forward outcome 진단
- 랭킹 응답·분봉 조회 시각을 분리한 ORB 81개 인접값·최대 10포지션 전진성과 진단
- 동일 종목 중복 제거와 등락률·가격·거래대금 필터
- 후보별 최대 10페이지의 1분봉 역조회
- 미국 동부시간 기준 관찰 시각보다 최소 1분 전에 끝난 당일 정규장 봉만 추출
- 완료 일봉 최대 20개로 전일 종가와 평균 거래량 계산
- 과거 봉은 5분 ORB·상대거래량 워밍업에만 사용하고 최신 완료 봉 하나에서만 신규 추천 평가
- 스프레드·위험폭 필터와 추천 생성 이벤트를 SQLite에 기록
- NYSE 공식 현재 거래정지와 호가·spread·슬리피지 위험 결정을 `market_risk_screen.csv`에 누적
- 종목별 마지막 처리 봉을 SQLite에 저장해 재시작·중복 실행에서도 새 완료 봉만 처리
- 기존 추천은 놓친 새 봉으로 순차 갱신하고 같은 종목·전략은 거래일당 추천 1개로 제한
- 공급자 부분 오류를 cycle 실패로 감사하고 다음 cycle은 계속 실행
- NYSE 공식 2026~2028 휴장일·13:00 조기폐장을 반영하고 미게시 연도는 fail-closed
- 신규 추천은 추천 ID 기반 SQLite outbox와 JSONL·한국어 카드로 중복 없이 projection
- KIS 카드 후보는 스캔 직전 5분 이내 생성분으로 제한해 기존 DB의 과거 추천 지연 발송 차단
- 정규장 종료 시 마지막 완료 봉 close로 열린 추천을 당일 `time_exit`하고 보고서 갱신
- 종료 거래의 편도 5/10/20bp 비용·연도별 결과·bootstrap CI·fallback 비율을 CSV와 한국어 보고서로 생성
- 실제 주문, 잔고, 계좌 조회 기능 없음
- 정규장에만 위험통과 전체 후보의 현재가상세 `base`·`open`을 조회하고 시가 갭과 응답 후 관찰시각을 별도 CSV에 누적

## 사용 방법

```bash
./run_kis_paper_scan.py --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy vwap_reclaim --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy hod_breakout --top 3 --max-pages 10
./run_kis_paper_scan.py --strategy gap_and_go --top 3 --max-pages 10
./run_kis_daytime_scan.py --top 10
./run_kis_paper_watch.py --wait-until-open --max-wait-minutes 720 \
  --collect-premarket --premarket-interval-seconds 300 \
  --cycles 390 --interval-seconds 60 --top 10 --max-pages 1
./run_paper_metrics.py outputs --output-dir outputs/paper_metrics/latest
./run_orb_forward_metrics.py outputs/live_sessions/<거래일> \
  --output-dir outputs/orb_forward_metrics/<거래일>
```

장전 실행은 정규장 개장을 30초 간격으로 기다린다. 대기 제한을 넘기면 종료코드 2로 끝나며, 대기 옵션이 없을 때 폐장 중 실행은 기존처럼 네트워크 호출 없이 종료한다.
`--collect-premarket`은 04:00~09:29 ET에 원시 랭킹·위험판정만 기본 5분 간격 저장하고 09:30부터 정규장 전략으로 전환한다. 장전 단계는 추천 DB·watchlist·분봉 전략 평가를 만들지 않는다.
`run_kis_daytime_scan.py`는 KIS 미국 주간거래 시간에만 `BAQ/BAY/BAA` 랭킹을 읽고 `daytime_*` 파일로 분리한다. 서울 관측일과 목표 뉴욕 거래일을 별도 저장하며, 이 가격을 정규장 시가 또는 opening gap으로 사용하지 않는다.
반복 감시는 기본 `--max-pages 1`로 종목별 최근 120개 봉만 다시 조회한다. 앞선 완료 봉은 SQLite에 누적하므로 매 cycle 단발 기본 10페이지를 재요청하지 않는다.

단발 결과는 `outputs/live_runs/<실행시각>/`, 영속 감시는 `outputs/live_sessions/<뉴욕 거래일>/`에 생성된다. `--output-dir`로 같은 날짜 폴더를 다시 지정하면 기존 DB와 checkpoint를 이어 쓴다.

- `kis_scan_summary_ko.md`: 조회 후보, 등락률, 스프레드, 분봉 수, 분석 상태
- `recommendations_ko.md`: 진입가·손절가·목표가와 상태 이력
- `paper_recommendations.sqlite3`: 삭제하지 않는 감사 로그
- `watch_cycles.csv`: cycle 시각·종료코드·성공/실패 상태
- `premarket_watch_cycles.csv`: 장전 랭킹 child의 cycle 시각·종료코드·상태
- `premarket_ranking_snapshots.csv`: 장전 원시 상승률·거래량 랭킹과 최대 10개 선정 여부
- `premarket_risk_screen.csv`: 장전 전체 후보 위험판정·누적 거래량·ADV
- `daytime_ranking_snapshots.csv`: 주간거래 원시 상승률·거래량 랭킹과 최대 10개 선정 여부
- `daytime_risk_screen.csv`: 주간거래 전체 후보 위험판정·누적 거래량·ADV
- `daytime_session_map.csv`: 서울 관측시각과 목표 뉴욕 거래일
- `recommendation_alerts.jsonl`: 메시지 어댑터용 구조화 카드
- `recommendation_alerts_ko.md`: 진입·손절·목표·무효화가 있는 한국어 카드
- `kis_ranking_snapshots.csv`: 랭킹 출처·원천 순위·가격·등락률·호가·거래량·거래대금·선택 여부의 시점별 원본
- `kis_opening_gap_cycles.csv`: 정규장 여부·갭 조회 적격·성공·실패 수
- `kis_opening_gap_snapshots.csv`: 정규장 위험통과 후보의 전일 종가·당일 시가·시가 갭·현재/전일 거래량
- SQLite `candidate_minute_bars`: 선택 후보의 완료 정규장 OHLCV·거래대금·최초 관찰 시각
- SQLite `tracked_candidates`: 정규장 최초 선택 후보의 거래일별 추적 목록
- `scanner_forward_outcomes.csv`: 다음 1분봉 시가 기준 완료·중도절단 경로
- `scanner_threshold_summary.csv`: 갭·거래대금 인접값과 5/10/20bp 비용·bootstrap CI
- `paper_metrics.csv`, `paper_yearly_metrics.csv`, `paper_trades.csv`, `paper_metrics_ko.md`: 종료된 paper 거래의 비용 민감도와 해석 제한
- `orb_outcomes.csv`, `orb_parameter_results.csv`, `orb_yearly_results.csv`, `orb_trades.csv`, `orb_forward_report_ko.md`: ORB 인과적 전진성과·인접값·비용·연도별 결과

## 실제 확인 결과

2026-07-13 10:03 KST는 미국 일요일 21:03 EDT로 시장이 닫힌 시각이었다. 초기 구현은 KIS 랭킹에서 NAS `GMM`을 후보로 찾고 직전 거래일 정규장 390분을 복원했다. 폐장 게이트가 추천을 막았지만, 유효 호가가 있는 장중에는 현재 랭킹으로 과거 봉의 추천을 뒤늦게 생성할 수 있는 lookahead 위험이 발견됐다.

수정과 서비스 분리 후 2026-07-13 10:14 KST에 같은 실키로 다시 실행했다. `GMM`은 여전히 랭킹 후보였지만 현재 미국 세션의 완료 봉은 0개로 판정됐고 추천·이벤트 모두 0건이었다. 직전 거래일 390봉을 추천 시뮬레이션에 재생하지 않았다. 저장 결과는 `live_runs/20260713_101439/`에 있다.

날짜별 영속 runner의 실제 2-cycle QA에서는 두 cycle을 모두 끝까지 수행했다. KIS 분봉 endpoint의 간헐 HTTP 500과 연결 종료가 발생했고, 수정 전에는 보고서에만 오류를 적고 cycle을 `ok`로 기록하는 silent failure가 있었다. 수정 후 동일 종류의 부분 실패는 각 cycle을 `failed`로 기록하고 최종 종료코드 1을 반환하면서도 다음 cycle까지 실행했다. 실제 증거는 연구 허브의 `live_sessions/20260713_qa_fixed/watch_cycles.csv`에 있다.

미국 정규장 밖에서는 영속 runner가 네트워크 호출과 출력 생성을 시작하지 않고 즉시 종료한다. 장중에 시작한 runner도 매 cycle 직전에 세션을 다시 확인해 16:00 이후 중단한다.

2026-07-13 11:56 KST에 실제 키로 단발 스캔을 다시 실행했다. NAS 랭킹에서 `GMM`, `JZXN`, `NVVE` 3개 후보를 읽었지만 미국 정규장 전이라 당일 완료 정규장 분봉은 모두 0개였고 추천도 0개였다. 실행은 종료코드 0, 최대 RSS 79,298,560바이트였으며 결과는 연구 허브 `live_sessions/20260713_resume_1158/`에 저장했다.

원시 후보군 소실을 막은 뒤 같은 실제 키와 출력 폴더로 2026-07-13 12:05 KST에 2회 연속 실행했다. `kis_ranking_snapshots.csv`에는 관찰 시각 2개, 상승률 600행, 거래량 600행, NAS·NYS·AMS 각 400행으로 총 1,200행이 누적됐다. 두 실행 모두 선택 후보는 `GMM`, `JZXN`, `NVVE`였고 미국장 전이라 추천은 0건이었다. 최대 RSS는 60,096,512바이트이며 증거는 연구 허브 `live_sessions/20260713_ranking_journal_qa/`에 있다.

분봉 아카이브 연결 뒤 2026-07-13 12:12 KST 실제 키로 다시 실행했다. `candidate_minute_bars` 스키마는 생성됐지만 미국장 전이라 현재 거래일 완료 정규장 봉과 추천은 모두 0건이었다. 직전 거래일 봉을 현재 관찰 데이터처럼 저장하지 않았고 최대 RSS는 60,276,736바이트였다. 증거는 연구 허브 `live_sessions/20260713_bar_archive_qa/`에 있다.

watchlist 연결 뒤 2026-07-13 12:19 KST 실제 키로 폐장 스캔을 실행했다. 현재 후보 3개, 추적 0개, 추천 0개로 종료했고 `tracked_candidates` 테이블은 생성되지 않았다. 폐장 시 이전 거래일 후보를 등록하지 않는 안전 게이트가 작동했으며 최대 RSS는 60,571,648바이트였다. 증거는 연구 허브 `live_sessions/20260713_watchlist_qa/`에 있다.

forward 분석기 QA에서는 구형 랭킹 600행의 `selection_input`을 빈 값으로 보존하고 새 600행 중 실제 선택 입력을 정확히 3행으로 기록했다. 폐장 관찰은 정규장 표본에서 제외돼 선택 관찰·완료 경로·중도절단 모두 0건이었고 16개 임계값 격자도 성과값을 만들지 않았다. 증거는 연구 허브 `forward_metrics_qa_20260713/`에 있다.

이 결과는 수익성 검증이 아니라 데이터 연결·시점 인과성·안전 차단 검증이다. 실제 장중 신호 정확도는 정규장 paper 전진검증 기록이 누적된 뒤 계산해야 한다.

ORB 돌파는 1분봉 종가가 확정된 뒤에만 알 수 있으므로 추천 생성 시각을 해당 봉 시각보다 1분 뒤로 고정했다. 15:59 봉처럼 신호 확인 시각이 16:00인 경우에는 당일 조건부 진입이 불가능하므로 신규 추천을 만들지 않는다.

첫 눌림목 VWAP reclaim은 `impulse → 첫 VWAP touch → 거래량을 동반한 재돌파` 순서를 별도 상태기계로 처리한다. 첫 눌림목이 VWAP 아래로 실패하거나 제한시간을 넘기면 그 거래일에는 두 번째 눌림목을 고르지 않는다. 추천 시각·최신 완료 봉·실제 분봉 조회 시각·spread·당일 종료 게이트는 ORB와 동일하지만 전략 이름과 실행 폴더는 분리한다.

HOD breakout은 전일 종가 대비 3% 이상의 첫 장중 고가를 고정한 뒤 2~8개 완료 봉의 base를 요구한다. 가격이 base 저가 기준 3% 이상 무너지면 종료하고, 첫 5bp HOD 돌파 시도가 양봉·종가 확인·base 평균 대비 1.5배 거래량을 모두 충족할 때만 봉 완료 뒤 신호를 낸다. 첫 돌파가 거래량 부족으로 실패하면 나중의 유리한 돌파를 재선택하지 않는다.

Gap-and-Go는 09:30 시가가 전일 종가보다 4% 이상 높은 종목만 대상으로 하고 09:30~09:34 완료 5분을 한 번만 판정한다. 이 기간 저가가 전일 종가에 닿거나 09:34 종가가 half-gap 아래면 `gap_failure`, 종가가 시가·세션 VWAP 위이고 당시 스캐너 후보면 `continuation`이다. 09:35 이후에 처음 후보가 된 종목은 과거 5분 신호를 만들지 않는다. 진입은 09:34 종가 5bp 위의 조건부 가격이며 ORB 고가 돌파와 구분한다.

모든 KIS 전략 앞에는 공통 시장위험 게이트가 있다. KIS 원시 랭킹을 보존한 뒤 NYSE 공식 현재 거래정지 종목, bid/ask 결손·역전, 100bp 초과 spread, 현재 spread에 편도 20bp씩을 더한 왕복비용 140bp 초과를 차단하고 나서 최대 10개를 선정한다. halt feed가 실패하거나 CSV 스키마가 바뀌면 fail-closed한다. PIT float는 제공되지 않아 추정하지 않으며 누적 거래대금은 저유동성 대리필터로만 쓴다.

2026-07-13 14:20 KST 실제 키 QA는 KIS 원시 랭킹 600행과 공식 active halt 31개를 읽었다. 위험 판정 163행 중 162개를 제외하고 `FBL` 1개를 선정했지만 미국 정규장 밖이라 최신 완료 분봉·추천은 0건이었다. 종료코드 0, 오류 0행, 최대 RSS 60,915,712바이트였고 증거는 연구 허브 `live_sessions/20260713_risk_gate_release_retry_qa/`에 있다. 직전 실행의 KIS AMEX 거래량 랭킹 HTTP 500은 종료코드 1로 노출됐고 한 번의 순차 재시도에서 회복했다.

14:30 KST 재검증부터는 최대 포지션이 차더라도 나머지 위험 통과 후보를 `포트폴리오 한도`로 계속 저장한다. 별도 CLI가 spread 80/100/120bp, 편도 slippage 10/20/30bp, 최대 왕복비용 100/140/180bp의 27개 조합마다 전체 위험판정 모집단을 다시 선정한다. 실제 폐장 표본은 163개 중 유효 호가가 있는 후보가 FBL 1개뿐이라 모든 조합이 동일했다. 이는 파라미터 평탄성이 아니라 폐장 표본의 식별력 부족이며, 결과는 `live_sessions/20260713_full_candidate_risk_sensitivity_qa/market_risk_sensitivity/`에 있다.

후속 세션은 전체 위험판정 후보 163개 모두에 KIS 누적 거래량·평균 일거래량·volume/ADV를 저장했다. 등락률 3개, 최대가격 3개, 거래대금 3개, volume/ADV 3개의 81개 조합을 각기 전체 후보에서 재선정했다. 폐장 위험 통과는 FBL 1개뿐이어서 20달러 상한 27개는 0개, 50·200달러 상한 54개는 FBL 1개였다. 이는 후보 보존 QA일 뿐 후행수익 검증이 아니며, 전체 후보 시가가 없는 KIS 랭킹으로 opening gap을 추정하지 않는다.

실제 키 응답 필드 재검사에서 상승률·거래량 랭킹에는 시가와 장전 거래량이 없고, 종목별 현재가상세에는 전일 종가 `base`, 당일 시가 `open`, 당일/전일 거래량이 있음을 확인했다. 이에 정규장일 때만 위험통과 전체 후보를 종목별 순차 조회해 시가 갭을 저장하는 경로를 연결했다. 2026-07-13 15:04 KST 실키 CLI는 원시 랭킹 600행, 위험판정 163행, 적격 1개를 읽었지만 폐장이라 `market_closed` cycle만 기록했고 stale 시가행은 0개였다. 종료코드 0, 최대 RSS 61,030,400바이트였으며 증거는 연구 허브 `live_sessions/20260713_opening_gap_live_schema_qa/`에 있다. 정규장 성공행과 장전 갭은 아직 미검증이다.

## 보안 상태

- 자격증명은 작업 폴더 밖 `~/.config/trading-agent/kis.env`에 권한 `600`으로 저장했다.
- 접근 토큰은 `~/.cache/trading-agent/`에 권한 `600`으로 저장한다.
- 키·시크릿·토큰은 코드, SQLite, Markdown 리포트에 기록하지 않는다.
- 제공된 Notion 페이지에는 자격증명이 평문으로 남아 있다. 운영 전 앱 키와 시크릿을 재발급하고 Notion의 기존 값을 삭제해야 한다.

## 다음 구현 단계

1. 미국 정규장에서 날짜별 영속 runner를 실제 스케줄로 시작해 최소 3개월 표본 누적
2. 사용자 지정 Telegram 또는 Codex 채널로 outbox 카드 전달
3. KIS 랭킹에 없는 종목까지 포함하려면 별도 전체시장 실시간 공급자 연결
4. 누적된 paper 결과로 PF, 승률, MDD, 체결 가능률·신호 지연·장 마감 fallback 비율을 평가
5. 2029년 일정 게시 또는 임시 휴장 공지 시 캘린더 갱신

영속 runner는 추천 이후 새 완료 봉에서 진입·무효·손절·목표 상태를 이어서 갱신하고 정규장 close 뒤 열린 추천을 종료한다. 종료가는 실제 MOC가 아니라 마지막 완료 봉 fallback이다. 임시 휴장 변경 반영과 실제 closing execution 검증은 아직 별도 승격 게이트다.
