# 추천 에이전트 런타임 감사

## H1: 미래 분봉을 사용해 ORB 신호를 미리 생성한다

- 판별 기준: 5분 범위가 끝나기 전에 추천이 생성되면 실패다.
- 관찰: 예시 데이터의 첫 5개 분봉은 범위만 형성하고, 09:35 돌파 분봉에서 추천 1건이 생성됐다.
- 결과: 기각. 추천 생성 시각까지의 분봉만 사용했다.

## H2: 같은 분봉에서 손절과 목표가 모두 닿으면 목표를 먼저 기록한다

- 판별 기준: 동일 봉의 고가가 2R 이상이고 저가가 손절 이하일 때 최종 상태를 확인한다.
- 관찰: 충돌 회귀 테스트의 최종 상태가 `stopped`였다.
- 결과: 기각. 일중 경로를 알 수 없는 경우 보수적으로 손절을 먼저 적용한다.

## H3: 추천 결과만 저장하고 실패·상태 변경 기록을 잃는다

- 판별 기준: SQLite 추천 레코드와 이벤트 이력을 독립적으로 읽는다.
- 관찰: 예시 실행에서 `setup → active → target_2r` 세 이벤트가 시각·가격·메모와 함께 저장됐다.
- 결과: 기각. 추천과 상태 변경은 별도 테이블에 보존된다.

## 실행 증거

- canonical 프로젝트 전체 회귀 97개 통과. 기존 안전장치에 더해 실제 선택 입력·응답 후 시각·최초 선택 dedup·완료 세션 outcome·비용·bootstrap 진단, 봉 완료 후 ORB 시각, 실제 분봉 조회 시각, 최대 10포지션, VWAP reclaim·HOD breakout·Gap-and-Go 상태기계, 장전 대기, 조건부 가격을 건너뛴 시가 체결, 공식 현재 halt·호가·슬리피지 위험 게이트를 포함한다.
- Ruff: 통과
- Basedpyright: 오류 0개
- replay, KIS, paper metrics CLI 도움말: 정상 출력
- 정상 재생: 분봉 7개, 추천 1개
- 잘못된 경로: 종료 코드 2와 한국어 오류 메시지

## KIS 실데이터 감사

### H4: 장전 분봉을 정규장 ORB 시초 범위로 오인한다

- 판별 기준: 09:20~09:21 분봉을 먼저 넣은 뒤 09:30~09:31과 09:32 돌파를 입력한다.
- 관찰: 장전 고가 110은 범위 계산에서 제외됐고 09:30~09:31 범위로만 신호가 생성됐다.
- 결과: 기각. 전략은 `America/New_York` 09:30~16:00만 사용한다.

### H5: KIS 비밀이나 토큰이 작업 폴더·출력에 노출된다

- 판별 기준: 자격증명 객체 표현, 비밀 파일 권한, 생성 리포트와 실행 로그를 확인한다.
- 관찰: 객체 표현은 `<redacted>`, 비밀과 토큰 캐시는 권한 `600`, 리포트에는 키·토큰이 없었다.
- 결과: 기각. 다만 원본 Notion 페이지에 평문 키가 남아 있으므로 운영 전 재발급이 필요하다.

### H6: 시장 폐장 중 마지막 가격만으로 허위 추천을 생성한다

- 판별 기준: 미국 일요일 밤에 실제 KIS 랭킹과 최근 정규장 분봉을 조회한다.
- 관찰: NAS 후보 `GMM`의 정규장 390분을 복원했지만 현재 뉴욕 시각과 마지막 분봉이 같은 정규장에 속하지 않아 `시장 폐장 또는 분봉 지연`, 추천 0개로 기록됐다.
- 결과: 기각. 뉴욕 정규장, 당일 분봉, 3분 이내 최신성, 유효 스프레드를 모두 확인하기 전에는 엔진을 실행하지 않는다.

## 2026-07-13 수동 QA

- 실제 KIS 인증: 성공
- AAPL 분봉 표본: 2026-07-10 미국 동부시간 19:55~19:59 5개 수신
- 세 거래소 랭킹→후보→분봉→일봉 문맥→추천 엔진: 성공
- 결과: GMM, 현재 거래일 완료 분봉 0개, 시장 폐장 또는 분봉 지연, 추천 0개
- `--top 0`: 종료 코드 2와 한국어 검증 오류
- 기존 SQLite 출력 재사용: 허용. 종목별 마지막 처리 봉 이후의 새 봉만 처리

## H7: 현재 랭킹 종목의 과거 분봉을 재생해 과거 시각 추천을 만든다

- 판별 기준: 시초 범위 뒤 09:32에 돌파했지만 현재 최신 완료 봉 09:33은 범위 아래인 스냅샷에서 추천이 생성되면 실패다.
- 최초 관찰: KIS 1회 스캐너가 조회된 정규장 봉 전체를 `engine.process()`로 재생하고 마지막 봉에서 즉시 `finalize_day()`를 호출했다. 현재 랭킹이 알려지기 전 과거 봉에서 추천·결과를 만들 수 있었다.
- 수정: 관찰 시각보다 최소 1분 전에 끝난 당일 정규장 봉만 남기고, 과거 봉은 `warmup()`으로 상태만 구성한다. 신규 추천은 최신 완료 봉 하나에서만 평가하며 1회 스캔에서는 과거 후속 봉이나 장 마감 결과를 만들지 않는다.
- 회귀 증거: 09:32 과거 돌파·09:33 비돌파 fixture는 추천 0건이다. 최신 09:32 봉 자체가 돌파인 fixture는 봉이 완성되는 09:33을 추천 생성 시각으로 기록하고, 15:59 돌파는 16:00에야 알 수 있어 추천 0건이다.
- 실키 재검증: 2026-07-13 10:14 KST 폐장 스캔에서 `GMM`은 후보였지만 현재 세션 완료 봉 0개, 추천 0개, 이벤트 0개였다.
- 결과: 기존 가설을 채택하고 결함을 수정했다. 이전 1회 live run은 연결 진단일 뿐 paper 성과로 사용할 수 없다.

## H8: KIS 부분 오류를 정상 cycle로 기록한다

- 판별 기준: 후보 중 하나의 분봉 조회가 HTTP 500 또는 연결 종료로 실패했을 때 단발 스캔과 영속 감시의 종료 상태를 확인한다.
- 최초 관찰: `KisPaperScanner`는 종목별 예외를 `오류:` 상태로 보고서에 남겼지만 단발 CLI는 종료코드 0을 반환했다. 그 결과 연구 허브의 초기 2-cycle QA는 HTTP 500이 포함된 cycle을 `ok`로 기록했다.
- 수정: 관찰 결과 중 `오류:`가 하나라도 있으면 단발 CLI가 종료코드 1을 반환하고, 영속 runner는 그 cycle을 `failed`로 즉시 기록한 뒤 다음 cycle을 계속 수행한다. 오류 문자열은 Markdown 표를 깨지 않도록 한 줄로 정규화한다.
- 실키 증거: 수정 후 실제 2회 실행은 두 cycle 모두 `failed`로 기록됐고 최종 종료코드는 1이었다. 두 번째 cycle까지 실행됐으므로 공급자 오류 뒤 진행도 유지됐다.
- 결과: 기존 가설을 채택하고 silent failure를 수정했다. 관찰된 KIS HTTP 500·연결 종료는 간헐적 공급자 오류로 보이지만 정확한 상류 원인은 확인하지 못했다.

## H9: 폐장 중 영속 감시가 불필요한 KIS 호출을 반복한다

- 판별 기준: 미국 일요일에 영속 runner를 실행하고 네트워크 호출·출력 폴더 생성을 확인한다. 장중 시작 뒤 16:00이 된 경우 다음 cycle 실행 여부도 확인한다.
- 수정·관찰: 시작 시각과 각 cycle 직전에 뉴욕 현지 시각을 다시 검사한다. 월~금 09:30~16:00이 아니면 즉시 종료하고, 회귀 테스트에서는 세션 predicate가 닫힌 세 번째 cycle 전에 operation을 중단했다. 실제 폐장 QA에서는 KIS 호출과 출력 폴더 생성이 모두 없었다.
- 추가 수정: NYSE와 Nasdaq이 게시한 2026~2028 휴장·조기폐장 표를 반영했다. 공휴일에는 열리지 않고 조기폐장일은 13:00에 닫히며, 게시 범위 밖은 fail-closed다.
- 결과: 기각. 임시 휴장 공지와 2029년 이후 일정은 명시적으로 표를 갱신해야 한다.

## H10: 동일 추천을 매 cycle마다 중복 알림한다

- 판별 기준: 같은 추천 DB에서 projection을 두 번 생성하고 SQLite outbox·JSONL 카드 수를 비교한다.
- RED: outbox 기능이 없어 카드 파일 자체가 생성되지 않았다.
- 수정: 추천 ID를 `alert_outbox` 기본키로 사용하고 최초 카드 JSON·한국어 Markdown을 immutable하게 저장한다. projection 파일은 DB 전체 outbox에서 재생성한다.
- 관찰: 최초 실행 신규 1건, 두 번째 실행 신규 0건, outbox·JSONL은 모두 1건이었다. projection 파일을 삭제한 뒤에도 1건이 복원됐다.
- 지연 발송 게이트: KIS는 스캔 직전 5분 이내 생성된 추천만 새로 queue하며, 30분 전 기존 추천은 outbox 0건으로 차단했다.
- 수동 replay: 분봉 7개, 추천 1개, 신규 카드 1개. 주당 위험의 부동소수점 노출도 6자리로 정규화했다.
- 결과: 기각. 외부 메시지 서비스 전송은 아직 연결하지 않았으므로 네트워크 전달 중복은 별도 어댑터에서 검증해야 한다.

## H11: watch가 정규장 종료 뒤 열린 추천을 overnight 상태로 남긴다

- 판별 기준: 공식 close가 지난 뒤 `setup`·`active`·`target_1r` 추천의 상태와 이벤트를 확인한다.
- RED: watch 종료 경로에 finalizer가 없어 열린 상태가 유지됐다.
- 수정: checkpoint에 마지막 완료 봉 close를 저장하고, 공식 close 뒤 `time_exit` 이벤트와 갱신된 보고서를 만든다. 기존 DB는 `last_close` 컬럼을 자동 migration한다.
- 관찰: 15:50 active 추천이 16:00에 `time_exit`, 가격 10.3000, 마지막 완료 봉 15:58 메모로 종료됐다.
- 결과: overnight 상태 가설은 기각했다. 다만 10.3000은 실제 closing fill이 아니라 마지막 완료 봉 fallback이므로 성과 집계에서 분리해야 한다.

## H12: 기능 검증용 추천을 실제 전략 성과로 오해한다

- 판별 기준: 미체결·미종료 추천 제외, DB 간 추천 ID 중복 제거, 5/10/20bp 비용, fallback 분리, 작은 표본 경고가 모두 있는지 확인한다.
- 관찰: QA DB 4개에서 중복을 제거한 종료 거래는 2개였다. 보고서는 편도 비용별 PF·승률·평균·누적·MDD와 bootstrap CI를 생성하고 1개의 마지막 완료 봉 fallback을 별도 표시했다.
- 결과: 집계 기능은 통과했지만 2건은 합성 replay·수동 종료 QA이므로 수익성 표본으로 사용할 수 없다. 실제 정규장 paper 거래가 누적될 때까지 전략 승격은 금지한다.

## H13: 최신 스캔 요약이 이전 후보군을 덮어써 스캐너 품질을 재현할 수 없다

- 판별 기준: 같은 출력 폴더에서 실제 KIS 스캔을 두 번 실행하고 관찰 시각·랭킹 출처·원시 입력·선택 여부가 모두 남는지 확인한다.
- 최초 관찰: `kis_scan_summary_ko.md`는 매 cycle 최신 표만 남겨 과거 후보군과 임계값 입력이 사라졌다.
- 수정: 세 거래소의 상승률·거래량 원시 랭킹 행을 `kis_ranking_snapshots.csv`에 append-only로 저장하고 실제 선택 종목을 표시했다.
- 결과: 실제 2회 실행에서 관찰 시각 2개와 총 1,200행이 남아 덮어쓰기 가설을 기각했다. 다만 KIS 랭킹 상위 표본이므로 전체 미국 종목 PIT 모집단을 대신하지 않는다.

## H14: 후보 분봉을 처리 후 버려 ORB·VWAP·HOD 경로를 재현할 수 없다

- 판별 기준: 장중 완료 봉 3개를 두 번 조회하고 SQLite 행 수와 최초 관찰 시각을 확인한다.
- 최초 관찰: 엔진 checkpoint는 마지막 봉 시각·종가만 남겨 전체 OHLCV 경로와 첫 관찰 시각을 복원할 수 없었다.
- 수정: `candidate_minute_bars`에 거래소·종목·분봉 시각 기본키로 OHLCV·거래대금·최초 관찰 시각을 저장하고 반복 조회는 무시한다.
- 결과: 회귀에서 3행과 최초 관찰 시각이 유지됐다. 실제 폐장 QA는 스키마 1개·데이터 0행으로 직전 거래일 봉의 오염도 차단했다. 정규장 실키 적재는 개장 후 추가 확인이 필요하다.
- 잔여 위험이었던 랭킹 탈락 뒤 우측 절단은 아래 H15의 거래일 watchlist로 보완했다.

## H15: 한 번 선택된 후보가 랭킹에서 빠지면 분봉·추천 상태 추적이 중단된다

- 판별 기준: 첫 cycle 후보를 저장한 뒤 다음 cycle에서 다른 후보만 선택하고 이전 후보의 추적 목록·분봉·추천 상태를 확인한다.
- 수정: 정규장 최초 선택 후보를 `tracked_candidates`에 거래일별로 저장한다. 현재 후보에서 빠진 종목은 `follow()`로 분봉을 보존하고 열린 추천만 갱신한다.
- 관찰: watchlist 회귀는 `FIRST`가 다음 랭킹에서 빠져도 `FIRST`, `NEXT`를 모두 반환하고 다음 거래일에는 0개를 반환했다. 추적 전용 ORB 봉은 추천 0건, 기존 active 추천은 같은 경로에서 `target_2r`로 갱신됐다.
- 실제 폐장 QA: 현재 후보 3개·추적 0개·추천 0개였고 `tracked_candidates` 테이블을 만들지 않았다.
- 결과: 랭킹 탈락 뒤 수집 중단과 추적 경로의 허위 신규 추천 가설을 회귀에서 기각했다. 실제 정규장 랭킹 교체와 재시작은 개장 후 추가 확인한다.

## H16: 중복 랭킹·반복 cycle·중도절단 경로가 forward 성과를 부풀린다

- 판별 기준: 동일 종목의 상승률·거래량 중복 행, 동일 거래일 반복 선택, 장 마감 전 경로를 각각 입력한다.
- 수정: 실제 선택 객체만 `selection_input=True`, 구형 행은 빈 값, 종목·거래일 최초 선택만 표본으로 사용한다. 다음 완전한 1분봉 시가부터 공식 close까지 1분 간격이 모두 존재해야 완료로 판정한다.
- 비용·평탄성: 등락률 4/6/8/10% × 거래대금 0.5/1/2/5백만 달러, 편도 5/10/20bp, 고정 seed bootstrap CI를 출력한다.
- 실제 폐장 QA: 새 600행의 실제 선택 입력은 3행이었지만 정규장 관찰이 아니므로 outcome과 모든 성과값은 0건·빈 값이었다.
- 2026-07-13 12:35 KST 실제 키 재개 QA도 새 세션에 랭킹 600행·실제 선택 입력 3행을 남겼다. 정규장 밖이므로 분봉·추천·outcome은 0건이고 16개 임계값 성과 셀은 모두 빈 값이었다.
- 결과: 중복·중도절단 성과 편입은 회귀와 실제 폐장 QA에서 기각했다. 실제 정규장 완료 세션 표본은 아직 0건이다.

## H17: 랭킹 응답 직후 분봉 조회 지연을 무시해 ORB 신호를 수십 초 앞당긴다

- 판별 기준: 랭킹 선택 09:36:30, 분봉 최초 관찰 09:36:45 표본의 신호·진입 시각을 확인한다.
- 최초 관찰: 전략 신호는 봉 완료 시각까지 보수화했지만 실제 분봉 조회 완료 시각은 엔진에 전달하지 않아 최대 수십 초 backdating이 가능했다.
- 수정: 추천 생성 시각을 `max(봉 완료, 실제 분봉 관찰)`로 고정했다. ORB 분석기도 랭킹 시각과 `first_observed_at`을 같은 cycle 창에서 결합하고 다음 완전한 1분봉인 09:37부터만 진입한다.
- 결과: 09:36:45 신호·09:37 진입 회귀가 통과해 기각했다.

## H18: ORB 파라미터별 거래를 만든 뒤 사후에 유리한 종목만 10개로 줄인다

- 판별 기준: 같은 시각 진입 후보 11개를 입력하고 포트폴리오 배정 순서와 탈락 종목을 확인한다.
- 수정: 진입 시각에 알려진 상승률·거래대금 내림차순으로 동시 최대 10개를 먼저 배정하고, 배정된 거래만 PF·승률·평균·누적·MDD에 포함한다.
- 회귀: 상승률이 가장 낮은 `S00`이 제외되고 10개만 `portfolio_selected=True`였다.
- 실제 폐장 QA: 후보 3개 × 81조합 243개 outcome은 전부 `censored`, 완료·거래 0건이며 수익·PF 칸을 비워 두었다.
- 결과: 사후 거래 필터 가설은 회귀에서 기각했지만 실제 정규장 포트폴리오 충돌 표본은 아직 없다.

## H19: 실제 관찰시각을 KST 객체로 저장해 미국 거래일 중복 방지가 흔들린다

- 판별 기준: 서울 22:33:30 관찰을 미국 동부 09:33:30 추천으로 저장하는지 확인한다.
- 최초 관찰: instant 비교는 맞았지만 `created_at` timezone이 서울로 남아 늦은 미국장에서는 `.date()`가 다음 한국 날짜가 될 수 있었다.
- 수정: 실제 관찰 instant를 항상 `America/New_York`으로 정규화하고, 중복 비교도 양쪽을 뉴욕 거래일로 변환한다.
- 결과: 추천시각과 timezone이 모두 미국 동부로 저장되는 회귀가 통과했다.

## H20: 실패한 첫 VWAP 눌림목 뒤 유리한 두 번째 reclaim을 선택한다

- 판별 기준: impulse와 첫 touch 뒤 종가가 VWAP 아래로 무효화된 다음 강한 재돌파를 입력한다.
- 수정: 첫 pullback이 실패하거나 reclaim 제한시간을 넘기면 상태를 `done`으로 종료한다.
- 결과: 이후 강한 봉에도 추천 0건인 회귀가 통과했다. 이 규칙의 수익성은 아직 검증되지 않았다.

## H21: 거래량이 부족한 첫 HOD 돌파 뒤 유리한 두 번째 돌파를 선택한다

- 판별 기준: 2봉 base 뒤 첫 5bp 돌파가 거래량 1.5배에 미달하고, 다음 봉이 더 큰 거래량으로 재돌파하는 경로를 입력한다.
- 관찰: 첫 돌파 시도에서 상태가 `done`으로 종료돼 나중 봉에도 추천은 0건이다.
- 결과: 사후 패턴 선택 가설을 회귀로 기각했다. 이 규칙의 수익성은 아직 검증되지 않았다.

## H22: HOD 전략 선택이 KIS 부분 오류를 정상 실행으로 숨긴다

- 판별 기준: 실제 키로 `--strategy hod_breakout`을 두 번 실행하고 종료코드·보고서·랭킹 저장을 확인한다.
- 관찰: 두 실행 모두 랭킹 600행을 저장했지만 KIS 분봉 HTTP 500을 포함해 종료코드 1을 반환했다. 정규장 밖이어서 추천은 0건이다.
- 결과: silent success는 기각했다. KIS 상류 HTTP 500의 원인은 현재 확정할 수 없다.

## H23: 09:35 이후 발견한 종목의 첫 5분 갭 지속을 과거 시각 추천으로 만든다

- 판별 기준: 09:30~09:34는 갭을 유지했지만 당시 candidate가 없고 09:35에 처음 candidate가 되는 경로를 입력한다.
- 관찰: 09:34 판정은 `neutral`로 종료하고 09:35 봉에서도 추천은 0건이다.
- 결과: 늦은 후보 도착을 이용한 backdating 가설을 회귀로 기각했다.

## H24: gap-up을 무조건 continuation으로 분류한다

- 판별 기준: 5% gap-up 뒤 첫 5분 저가가 전일 종가에 닿거나 09:34 종가가 half-gap 아래로 무너지는 경로를 입력한다.
- 관찰: 추천 0건이고 분류는 `gap_failure`다. 시가·VWAP 상회와 당시 candidate가 함께 있는 경로만 `continuation`이다.
- 결과: 단순 gap long 가설을 기각했다. continuation 수익성은 아직 검증되지 않았다.

## H25: 조건부 진입·목표를 갭으로 건너뛰어도 계획 가격으로 낙관 체결한다

- 판별 기준: 다음 1분봉 시가가 조건부 진입가보다 높거나 목표가보다 높은 경로에서 이벤트 체결가를 확인한다.
- 최초 관찰: 기존 엔진은 봉 시가가 진입가를 건너뛰어도 계획 진입가를, 목표가를 건너뛰어도 계획 목표가를 기록해 paper 성과를 낙관적으로 만들 수 있었다.
- 수정: long 진입과 목표 이벤트 가격을 각각 `max(계획 가격, 봉 시가)`로 기록한다. 같은 봉에서 손절과 목표가 모두 닿으면 기존처럼 손절을 먼저 적용한다.
- 결과: 시가 갭 진입·목표 회귀 2건이 통과했다. 1분봉 내부 경로가 없는 한 이 체결 모델은 paper alert 진단용 보수 근사이며 실제 호가 체결을 대신하지 않는다.

## H26: 위험 종목을 top N으로 먼저 고른 뒤 제외해 차순위 적격 후보를 잃는다

- 판별 기준: 상승률 상위에 active halt·호가 없음·역전·과대 spread 종목이 있고 그 아래 적격 종목이 있는 모집단에서 최종 선정을 확인한다.
- 관찰: 위험 종목 3개를 제외한 뒤 차순위 `SAFE`가 1개 포트폴리오에 선정됐다.
- 결과: 위험 게이트가 전체 KIS 랭킹 적격군을 먼저 순회하고 통과 종목 중 최대 N개를 채우므로 사후 거래 필터 가설을 기각했다.

## H27: 현재 halt feed 장애·스키마 변경을 정상 무정지로 간주한다

- 판별 기준: NYSE CSV 헤더가 변경된 응답을 입력한다.
- 관찰: typed `HaltFeedFormatError`가 발생해 cycle이 추천 단계로 진행하지 않았다. 실제 feed에서는 active halt 31개를 읽었다.
- 결과: fail-open 가설을 기각했다. 다만 KIS·NYSE 간 특수기호 심볼 매핑 완전성은 별도 데이터가 필요하다.

## H28: PIT float가 없는데 최신 float 또는 거래대금을 low-float로 오인한다

- 판별 기준: 보고서·모델에 float 값이나 추정 필드가 생성되는지 확인한다.
- 관찰: float 필드는 만들지 않았고 보고서에 `PIT float 미제공`, 거래대금은 저유동성 대리필터라고 명시했다.
- 결과: 현재값 backfill 가설을 기각했다. low-float 성과 연구는 `EXCLUDE_UNTIL_PIT_DATA`를 유지한다.

## H29: 이전 cycle 추적 종목이 새로 halt돼도 상태 갱신을 계속한다

- 판별 기준: tracked 후보 2개 중 1개만 공식 active halt 목록에 넣고 follow 대상 분할을 확인한다.
- 최초 관찰: 신규 후보 위험 게이트는 있었지만 기존 tracked 후보에는 halt 목록을 다시 적용하지 않았다.
- 수정·관찰: 매 cycle tracked 후보를 active halt 기준으로 분할해 정지 종목은 `follow()`에서 제외하고 `공식 현재 거래정지: 추적 중단` 관찰만 남긴다. 회귀에서 `HALTED`는 차단되고 `TRADABLE`만 follow 대상이었다.
- 결과: halt 중 추천 상태를 분봉으로 갱신하는 경로를 차단했다. halt feed 조회 뒤 실제 정지가 시작되는 수초 race와 특수기호 symbol mapping은 잔여 위험이다.

## H30: 포트폴리오 한도 뒤 후보가 사라져 비용 인접값 분석이 편향된다

- 판별 기준: 위험 통과 후보 12개와 최대 포지션 10개를 입력해 선정·한도 제외·위험 제외 집합과 CSV 행을 확인한다.
- 최초 관찰: 기존 게이트는 최대 포지션 수가 차는 즉시 순회를 중단해 뒤 후보를 CSV에 남기지 않았다. 다른 spread·slippage 조합을 이 파일에 적용하면 전체 후보 재선정이 불가능했다.
- 수정: 위험 통과 후보를 `selected`와 `not_selected`로 분리하되 전체 적격 랭킹을 끝까지 순회한다. 한도 밖 후보는 `포트폴리오 한도`로 저장하고, 민감도 분석은 각 조합마다 전체 CSV를 다시 필터링한 뒤 상승률·거래대금 순 최대 10개를 재선정한다.
- 인접값: 최대 spread 80/100/120bp × 편도 slippage 10/20/30bp × 최대 왕복비용 100/140/180bp의 27개 조합이다.
- 실제 키 관찰: 원시 랭킹 600행, 위험판정 모집단 163개, 고정 제외 162개, FBL 1개 통과였다. 27개 조합이 모두 동일했지만 폐장 호가 결손이 지배한 표본이라 평탄성 증거로 쓰지 않는다.
- 결과: 후보 손실 편향은 회귀로 기각했다. 정규장 중 한도 초과 적격 후보와 여러 거래일 표본이 누적되기 전에는 임계값 안정성·수익성을 판정할 수 없다.

## H31: baseline 선택 거래를 사후 필터한 4×4 표를 전체 스캐너 임계값 비교로 오해한다

- 판별 기준: 각 임계값이 전체 후보에서 포트폴리오를 새로 만드는지, 이미 `selection_input=True`인 종목만 거르는지 데이터 흐름을 추적한다.
- 관찰: 기존 `scanner_threshold_summary.csv`는 baseline에서 실제 선택된 종목의 완료 outcome만 입력받아 등락률·거래대금을 사후 필터했다. 전체 후보에서 조합별 최대 10개를 선정하지 않았다.
- 수정: 위험판정 전체 후보에 시점 누적 volume·ADV를 추가하고 등락률·최대가격·거래대금·volume/ADV 81개 조합마다 위험 통과 후보를 새로 정렬해 최대 10개를 선정한다. 기존 4×4 표에는 사후필터 한계를 명시했다.
- 실제 키 관찰: 163개 후보 모두 특징이 채워졌지만 폐장 호가 기준 위험 통과는 FBL 1개였다. 20달러 상한 27개 조합은 0개, 나머지 54개는 FBL 1개를 선정했다.
- 결과: 전체 후보 보존·재선정 경로는 통과했다. 후행 분봉은 baseline 외 후보에 없고 KIS 랭킹에 opening price도 없으므로 81개 표를 후보 수익성·gap 임계값 결과로 승격하지 않는다.

## H32: 구형 위험 CSV와 watch 기본 상한이 전체후보 연구를 훼손한다

- 판별 기준: 구형 12열 위험 CSV에 새 cycle을 append한 헤더·행 구조와 watch가 자식 스캔에 전달하는 `--top`을 확인한다.
- 최초 관찰: 새 writer는 15열 행을 쓰지만 기존 헤더 migration이 없었고, watch는 `--top`을 전달하지 않아 단발 스캔 기본 3개를 사용했다.
- 수정: 구형 파일은 기존 행을 새 15열 헤더로 원자 migration하고 volume·ADV는 빈 값으로 남긴다. watch는 `--top` 1~10을 노출하고 기본 10을 자식 스캔에 명시한다.
- 결과: migration 뒤 구형·신규 행이 동일 헤더로 파싱됐고, 명령 회귀와 CLI help에서 `--top 10`을 확인했다. 폐장 happy path는 네트워크 호출 없이 안전 종료했다.

## H33: 폐장 현재가상세의 과거 `open`을 새 거래일 시가로 저장한다

- 판별 기준: 미국 정규장 밖에서 실제 키 scanner를 실행해 현재가상세 요청·cycle 상태·시가 snapshot 파일을 확인한다.
- 관찰: 실제 키 CLI는 원시 랭킹 600행과 위험판정 후보 163개를 읽었지만 `kis_opening_gap_cycles.csv`에 `market_closed,1,0,0`만 기록했다. `kis_opening_gap_snapshots.csv`는 생성되지 않았다.
- 결과: 정규장 캘린더 게이트가 종목별 현재가상세 fan-out보다 먼저 작동해 stale 시가 저장 가설을 기각했다. 장전 시점에는 정규장 시가가 아직 없으므로 이 경로를 premarket gap으로 사용하지 않는다.

## H34: 최대 10포지션 뒤 위험통과 후보가 시가 갭 모집단에서 사라진다

- 판별 기준: 위험통과 후보 2개와 spread 위험 제외 후보 1개, 포트폴리오 상한 1개를 입력해 실제 현재가상세 요청 종목을 확인한다.
- 관찰: `selected`의 `UP`과 `포트폴리오 한도`의 `DOWN` 두 종목을 모두 요청했고 spread 제외 `WIDE`는 요청하지 않았다. 저장 gap은 각각 +10%, -10%였다.
- 결과: 시가 갭 수집은 baseline 최대 포지션이 아니라 위험통과 전체 후보를 입력으로 사용한다. 다만 KIS 랭킹 자체가 미국 전체 PIT 모집단은 아니다.

## H35: 종목별 시가 조회 실패를 성공 cycle로 숨긴다

- 판별 기준: 분봉 관찰은 정상이고 시가 갭 조회 실패가 1개인 cycle의 종료코드를 확인한다.
- RED: `scan_exit_code`가 시가 실패 수를 받지 않아 `opening_gap_failure_count` 인자를 전달하면 `TypeError`였고, 실제 연결도 gap 결과를 버렸다.
- 수정: `gap_cycle.failure_count`를 공통 cycle 종료코드에 전달했다. 시가 조회 성공·실패 행은 계속 append-only로 보존하되 실패가 1개 이상이면 단발 종료코드 1, 영속 watch `failed`가 된다.
- 결과: 회귀에서 정상 분봉 관찰 + 시가 실패 1개가 종료코드 1을 반환했다. 부분 공급자 오류 silent success 가설을 기각했다.

## H36: 영속 watch가 매분 후보당 과거 분봉 10페이지를 다시 요청한다

- 판별 기준: watch가 만드는 자식 scan 명령에 `--max-pages`가 있는지 확인한다.
- RED: `_scan_command(..., max_pages=1)` 호출이 인자 수 `TypeError`로 실패했고, 기존 명령은 단발 scan 기본 10페이지를 그대로 사용했다.
- 수정: watch에 1~10 범위 `--max-pages`를 추가하고 반복 수집 기본값을 1로 정했다. 자식 명령은 `--top 10 --max-pages 1`을 명시한다.
- 결과: 회귀·CLI help·오입력 종료코드 2를 확인했다. 최근 120개 봉을 매 cycle 다시 읽고 앞선 봉은 SQLite에 누적하므로 ORB 5분 범위와 진행 상태를 유지하면서 불필요한 반복 호출을 줄인다.

## H37: 경로 문자열 기반 RSS 집계가 실제 수집기 자식을 모두 포함한다

- 판별 기준: 첫 RSS 로그와 `ps`의 watch·`uv`·Python 프로세스 RSS를 대조한다.
- 최초 관찰: 경로 문자열 집계는 8,944KiB였지만 실제 프로세스 목록에는 `uv` 29,536KiB와 Python 42,848KiB가 별도로 있었다. 자식 명령이 상대경로라 marker가 없었다.
- 수정·관찰: watch의 프로세스 그룹 ID 전체를 합산하는 별도 30초 가드를 연결했고 첫 값은 76,048KiB였다. 9,961,472KiB 이상이면 그룹을 종료하고 중단 파일을 남긴다.
- 결과: 최초 `rss_watch.csv`는 비권위로 제한하고 `rss_process_group.csv`만 안전 판정에 사용한다.

## H38: 폐장 CLI와 개장 대기 CLI가 정규장 전 KIS 조회를 시작한다

- 판별 기준: `--wait-until-open` 유무 두 경로의 실제 CLI 출력과 생성 파일을 확인한다.
- 관찰: 대기 옵션이 없는 폐장 실행은 종료코드 0과 `미국 정규장 밖이므로 감시를 시작하지 않습니다.`를 출력했다. 실제 forward 세션은 `미국 정규장 개장을 기다립니다.`만 출력하고 랭킹·분봉 파일을 아직 만들지 않았다.
- 결과: 정규장 전 네트워크 호출 가설을 기각했다. 현재 tmux의 ORB 수집기 하나와 경량 RSS 가드만 실행 중이다.

## H39: 정규장-only watch가 장전 급등 후보 모집단을 잃는다

- 판별 기준: 04:00~09:29 ET에 원시 랭킹 snapshot 파일을 만들 수 있는 실행 경로가 있는지 확인한다.
- 최초 관찰: 기존 watch는 09:30까지 30초 캘린더 확인만 하고 KIS 랭킹을 조회하지 않았다.
- 수정: `--collect-premarket`을 추가해 장전에는 전용 snapshot child를 기본 5분 간격 실행하고 09:30부터 기존 ORB child로 전환한다.
- 결과: 04:00·09:29 허용, 09:30·주말 차단과 두 장전 cycle 뒤 정규장 전환을 회귀로 확인했다. 실제 장전 API 성공행은 04:00 이후 확인한다.

## H40: 장전에 전체 paper scan을 반복해 watchlist와 추천 상태를 오염한다

- 판별 기준: 장전 child 명령과 생성 표면에 전략·분봉·SQLite 경로가 포함되는지 확인한다.
- 관찰: 전용 `run_kis_premarket_scan.py`는 랭킹·현재 halt·위험 게이트만 실행하고 `premarket_ranking_snapshots.csv`와 `premarket_risk_screen.csv`만 쓴다. 명령에는 strategy·max-pages가 없다.
- 결과: 장전 후보가 정규장 watchlist를 누적 확대하거나 과거 정규장 봉으로 추천을 만드는 경로를 분리했다. 장전 랭킹은 후보 품질 진단용이며 수익성 표본이 아니다.

## H41: 새 장전 CLI가 코드 검증은 통과하지만 실행 표면에서 시작되지 않는다

- 판별 기준: shebang CLI를 직접 `./run_kis_premarket_scan.py --help`로 실행한다.
- 최초 관찰: 파일 실행권한이 없어 종료코드 126과 `permission denied`가 발생했다.
- 수정·관찰: 모드를 755로 변경한 뒤 동일 help가 종료코드 0, `--top 0`은 종료코드 2였다. 장전 밖 실행은 API·출력 폴더 없이 종료코드 0이었다.
- 결과: 수동 CLI 게이트에서 발견해 수정했다. 현재 실제 combined watch는 장전 개시 전 대기 메시지와 프로세스 그룹 RSS 75,152KiB를 기록 중이다.

## H42: 장 종료 분석 CLI가 shebang만 있고 실행권한이 없어 자동 후처리를 막는다

- 판별 기준: 모든 `run_*.py` 모드와 직접 `--help` 종료코드를 확인한다.
- 최초 관찰: `run_market_risk_sensitivity.py`와 `run_scanner_candidate_sensitivity.py`는 644여서 후자의 직접 help가 종료코드 126이었다.
- 수정·관찰: 두 파일을 755로 변경하고 두 help를 직접 실행해 종료코드 0을 확인했다. 나머지 7개 `run_*.py`도 모두 실행 가능하다.
- 결과: 정규장 종료 후 위험·스캐너 인접값 분석이 권한 때문에 건너뛰는 경로를 제거했다.

## H43: watch 종료와 동시에 여러 분석이 병렬 실행돼 메모리 제한을 어긴다

- 판별 기준: 후처리 프로세스 명령·대기 조건·실행 기록·RSS 가드를 확인한다.
- 관찰: 후처리 zsh는 watch PID가 사라질 때까지 60초 간격으로 기다리고, 이후 장전 스캐너→정규장 위험→정규장 스캐너→ORB→paper metrics 순서로 한 명령씩 실행한다. 현재 API child와 분석 child는 각각 0개다.
- 결과: 병렬 실행 가설을 기각했다. 각 단계 상태는 `postprocess_steps.csv`, `/usr/bin/time -l`은 `postprocess.log`, 프로세스 그룹 RSS는 `postprocess_rss.csv`에 남고 9.5GiB에서 중단한다.

## H44: 완료 거래가 없는 후처리도 0% 수익·PF 0처럼 가짜 성과를 만든다

- 판별 기준: 실제 키 폐장 QA의 랭킹 600행·위험후보 163행·추천 0건을 네 후처리 CLI에 순차 입력한다.
- 관찰: 모든 CLI는 종료코드 0이었다. ORB outcome은 81개지만 완료·거래가 0건이고, paper도 비용 3개 모두 거래 0건이었다. 두 성과 CSV의 승률·평균수익·PF·누적수익·MDD·CI는 공란이며 연도별 파일은 헤더만 있었다.
- 보고서: ORB는 중도절단을 수익 0으로 바꾸지 않고, scanner·risk는 후보 보존 진단이며, paper는 QA·paper 표본이라고 명시했다.
- 결과: 빈 표본을 0수익 성과로 승격하는 가설을 기각했다. 수동 QA 최대 RSS는 43,548,672~44,335,104바이트였다.

## H45: 공식 랭킹 문서에 없는 주간거래 코드를 미지원으로 단정한다

- 판별 기준: 실제 키로 `BAQ/BAY/BAA`의 상승률·거래량 랭킹을 각각 조회한다.
- 관찰: 세 코드가 모두 정상 응답을 반환했다. 단발 CLI는 원시 401행, 위험판정 18개, 최대 10개 선정을 별도 `daytime_*` 파일에 저장했고 종료코드 0, 최대 RSS 58,703,872바이트였다.
- 수정: 서울 10:00부터 뉴욕 04:00 ET까지의 주간거래 게이트와 목표 뉴욕 거래일 매핑을 추가했다. 주간거래 코드는 `NAS/NYS/AMS`와 분리하고 정규장 시가·gap 또는 premarket RVOL로 재해석하지 않는다.
- 결과: 미지원 가설은 기각했지만 전체시장 PIT 데이터 가설은 기각하지 못했다. 이 경로는 forward 후보 수집이며 3년 백테스트 대체물이 아니다.

## H46: 정규장 직후 09:29 장전봉을 최신 완료 정규장봉으로 승인한다

- 판별 기준: 09:30:05 ET에 broker clock이 open이고 09:29 봉이 09:30:02에 관찰된 후보를 주문 승인 게이트에 입력한다.
- RED: 단순히 현재 분을 1분 내린 값만 비교해 09:29 장전봉이 `APPROVED`됐다.
- 수정: 기대 봉의 시작·종료가 로컬 NYSE 정규장 경계 안에 모두 포함되는지 별도로 확인한다.
- 결과: 09:30대 장전봉은 `CURRENT_BAR_BLOCKED`, 09:31 이후 방금 완성된 09:30 정규장봉부터만 다음 게이트로 진행한다.

## H47: WebSocket 재연결 뒤 이전 REST 대사 결과를 재사용하거나 승인 provider를 주입한다

- 판별 기준: 호출자가 `ready=True`와 복사한 epoch를 넣거나, 공개 factory에 fake stream·REST·clock을 주입해 세션 밖에서 승인을 만든다.
- RED: 초기 모델은 공개 dataclass의 `ready` boolean과 epoch를 호출자가 직접 만들 수 있었고, 게이트 평가 시점에는 WSS가 이미 닫혀 있어도 승인할 수 있었다.
- 수정: 열린 WSS의 첫 Pong 뒤 계좌·주문·포지션·시계와 단일 SQLite 원장 snapshot을 읽고 대사·포트폴리오 집계를 끝낸 다음 두 번째 Pong을 확인한다. 독립 증명 객체와 공개 단독 gate evaluator를 삭제했다. 공개 `open_paper_runtime_session(credentials, ledger)`는 production REST·WSS·UTC clock으로 고정하고, 공개 생성자가 없는 활성 세션의 `evaluate_order`만 승인한다.
- 결과: 공개 factory에는 provider·clock 주입 인자가 없고, 오래된 epoch는 admission에서 차단되며 종료된 세션은 `InactivePaperRuntimeSessionError`로 재사용할 수 없다. 읽기 전용 probe 결과도 승인 artifact가 아니다.

## H48: 이벤트가 조용한 주문 스트림을 정상이라고 추정한다

- 판별 기준: 인증·`trade_updates` 구독 뒤 Pong 도착과 timeout을 각각 입력한다.
- 수정: 이벤트 수신 여부를 heartbeat로 사용하지 않고 RFC 6455 Ping/Pong을 5초 timeout으로 확인한다. 인증·구독 응답과 Pong 순서가 틀리거나 Pong이 5초보다 오래되면 차단한다.
- 실제 계정 관찰: 2026-07-14 장전 실제 Paper WSS에서 binary 인증 응답, `trade_updates` listening 승인과 Ping/Pong을 확인했다.
- 결과: 스트림 control plane은 통과했지만 Alpaca가 reconnect replay를 보장하지 않으므로 REST 대사를 별도로 유지한다.

## H49: 장외 연결 성공을 현재 주문 가능으로 보고한다

- 판별 기준: broker clock이 closed인 실제 계정에서 runtime readiness CLI를 실행한다.
- 관찰: WSS 인증·구독·Pong, 계좌·시계·미체결·포지션 GET과 원장 대사는 통과했고 미체결·포지션은 각각 0개였다.
- 수정: 보고서에서 연결·REST 대사, 시장 개장, 신규 주문 승인을 분리한다. candidate/current-bar가 없는 readiness CLI는 신규 주문을 항상 `미평가`로 기록한다.
- 결과: 2026-07-14 21:03 KST 최종 경로의 실제 실행은 종료코드 0, 최대 RSS 57,081,856바이트로 활성 스트림 내부 대사를 확인하면서도 `브로커 시장 개장: 아니오`, `신규 주문 승인: 미평가`, `POST/DELETE: 비활성`을 명시했다.

## H50: 조작되거나 불완전한 sizing·portfolio 합계를 그대로 신뢰한다

- 판별 기준: 비용을 뺀 사전 `SizedPaperOrder`, 호출자가 미리 합한 position/pending count와 부분체결 주문·포지션을 전달한다.
- RED: 첫 수정도 외부 sizing을 재검산할 뿐 최소 거래비용을 강제하지 않아 USD 75 위험 한도를 우회할 수 있었다. 포지션과 남은 주문이 같은 종목인 정상 부분체결은 두 슬롯으로 세거나 불일치로 차단했고, 호출자가 만든 합계 자체를 신뢰했다.
- 수정: 게이트 입력에서 `SizedPaperOrder`와 사전 합계를 제거했다. 브로커 주문·포지션과 원장 intent를 직접 결합하고, 부분체결은 현재 포지션 market value와 남은 주문 명목금액을 합친 단일 노출로 만든다. market value가 0·수량과 반대 부호면 불완전으로 차단하고, 유효해도 진입가 기준 명목금액보다 작게 세지 않는다. 미체결 주문이 없는 완전체결 포지션은 현재 세션의 유일한 intent와 로컬 `fill` 이벤트 증거가 모두 있어야 한다. 기존 노출 위험은 거래당 예약 한도와 원장 수량 전체의 손절거리·동일 config 최소비용 위험 중 큰 값으로 계산하고 각 노출의 위험·명목 한도도 별도 검사한다. join 누락·수량 불일치·동일 종목 중복·모호한 intent는 불완전 포트폴리오로 차단한다. 신규 주문은 conservative equity, 유동성 수량, spread, 손절거리와 왕복 최소 20bp 비용으로 내부 재산정한다.
- 결과: 진입 100·손절 99·spread 0 예시는 주당위험 1.398, 53주, 계획위험 약 USD 74.094로만 승인된다. 20bp spread에서는 46주로 줄며, 70주 기존 주문 위험은 USD 97.86으로 재계산되어 종목 한도를 초과한다. torn partial fill, 축소된 market value와 외부 합계 조작 경로는 `PORTFOLIO_BLOCKED`다.

## H51: SIP provider의 일시적 분봉 누락이 같은 세션에서 영구 gap block이 된다

- 판별 기준: 첫 SIP 응답이 session sequence 1·3만 반환한 뒤 같은 process의 다음 응답이 1·2·3 전체를 반환하도록 한다.
- RED: supervisor는 adapter에 마지막 sequence 3만 넘겼고 adapter는 두 번째 응답의 1·2·3을 모두 과거 offset으로 버렸다. 결과는 `no_new_data`였으며 기존 `gap_blocked` checkpoint를 해제할 방법이 없었다.
- 수정: read-only adapter 계약이 숫자 offset 대신 exact `MarketDataRuntimeCheckpoint`를 받는다. 정상 checkpoint는 기존 epoch와 last sequence를 그대로 이어가고, gap checkpoint에서는 full-session 응답이 sequence 1부터 현재 마지막까지 완전히 연속일 때만 prior epoch와 verified replay identity에 결합된 새 recovery epoch로 전체 backfill을 전달한다. 여전히 빠진 sequence가 있으면 기존 epoch와 gap block을 유지한다.
- 결과: raw·canonical gap evidence를 보존한 채 두 번째 full backfill이 `reconnect` incident와 clean checkpoint를 만들고 feature 계산을 다시 연다. 불완전 backfill은 신규 receipt가 0이어도 외부 상태를 `no_new_data`로 축소하지 않고 `blocked_sequence_gap`을 유지한다. fixture에서 receipt는 gap epoch 2개와 recovery epoch 3개로 분리됐다. 외부 network·credential·account·order 호출은 0건이다.

## H52: 단일종목 SIP adapter가 다른 종목의 desired subscription에 이전 checkpoint를 재사용한다

- 판별 기준: ACME용 adapter와 runtime checkpoint에 단일 desired subscription `OTHER`를 전달한다.
- RED: desired tuple 길이와 channel만 검사해 `OTHER` HTTP GET을 전송했고, 이후에는 ACME의 source-level epoch와 last sequence를 그대로 적용할 수 있었다.
- 수정: `AlpacaSipRuntimeContext`가 session date뿐 아니라 exact instrument ID와 symbol을 고정한다. adapter는 desired subscription이 두 binding과 모두 같지 않으면 credential header가 있는 HTTP request 전에 sanitized error로 닫는다.
- 결과: 한 adapter/supervisor가 한 종목만 소유한다는 M4 계약이 runtime 타입에 반영됐다. 다중 종목은 종목별 독립 adapter·runtime owner가 필요하며 현재 checkpoint를 종목 사이에 공유하지 않는다.

## H53: M4 broad scanner가 fixture 객체만 소비하고 실제 후보 생산자가 없다

- 판별 기준: KIS가 발행한 causal US Opportunity가 restart 가능한 M4.2 입력으로 도달하는 production 호출 경로와 durable artifact를 찾는다.
- 최초 관찰: `BroadScannerSnapshot`과 subscription policy는 순수 계약 테스트에서만 직접 생성됐다. KIS Opportunity outbox, instrument master, canonical dataset, M4.2 사이의 운영 연결은 없었다.
- 수정: KIS CLI에 all-or-none opt-in projection 설정을 추가했다. Opportunity raw bytes를 mode-600 append-only SQLite에 먼저 확정하고, 해당 시점에 유효한 US equity/ETF alias 하나만 허용해 candidate Parquet를 발행한 뒤 DuckDB replay identity와 scanner snapshot을 같은 immutable projection에 저장한다. 최신 snapshot reader는 SQLite 값만 읽지 않고 연결된 canonical dataset을 매번 다시 검증한다.
- 결과: exact retry는 raw·dataset·snapshot을 중복 생성하지 않고, alias 누락·미래 foundation·부분 CLI 설정·dataset mode 변조는 fail-closed다. 옵션이 없으면 기존 KIS 동작은 그대로다. 현재 fixture manifest는 `FIXT` 하나만 포함하므로 실제 장중 universe를 지원한다는 주장은 하지 않으며 current US security master adapter가 다음 경계다.

## H54: 기존 Alpaca universe가 provider 응답을 파싱한 뒤 CSV만 남긴다

- 판별 기준: `/v2/assets` 응답의 exact bytes, 최초 관측시각, stable instrument ID와 재시작 검증 가능한 snapshot이 있는지 확인한다.
- 최초 관찰: 기존 archive helper는 응답을 메모리에서 바로 Pydantic으로 파싱하고 symbol CSV를 교체했다. provider schema drift나 시점별 alias를 원문으로 재생할 수 없었다.
- 수정: GET response를 파싱 전에 mode-600 append-only SQLite에 확정하고, active listed/supported asset만 Alpaca UUID instrument와 provider-symbol alias로 투영한다. latest reader는 raw SHA와 receipt ID를 다시 계산한다.
- 결과: 실제 원문 33,351행에서 active instrument 13,011개를 확정했고 synthetic KIS candidate가 actual asset UUID와 canonical replay identity에 결합됐다. live trading origin, redirect, duplicate identity, 1일 초과 stale snapshot과 fixture foundation은 차단된다.

## H55: 실제 provider schema 확장을 인증 실패로 오인할 수 있다

- 판별 기준: GET-only CLI를 실제 Paper assets endpoint에 실행하고 raw receipt와 terminal snapshot을 각각 확인한다.
- 최초 관찰: 첫 raw 응답에는 기존 모델에 없던 `borrow_status`, `margin_requirement_long`, `margin_requirement_short`가 있었고, 21개 비식별 name에 provider 공백이 포함돼 strict parser가 snapshot 전에 닫혔다. 두 실패 모두 raw receipt는 먼저 보존됐다.
- 수정: 실제 응답의 필드 집합과 타입만 집계해 새 필드를 명시적으로 계약에 추가했다. 투영에 쓰지 않는 name은 최대 길이만 제한하고, asset UUID·symbol·exchange·class·status 검증은 유지했다.
- 결과: 세 번째 bounded GET은 ready로 종료됐다. 실제 외부 GET은 총 3건, account/order endpoint와 POST/DELETE mutation은 0건이다.

## H56: broad candidate 이전에 candidate별 SIP evidence를 요구해 구독 순환이 생긴다

- 판별 기준: KIS Opportunity만 존재하고 SIP candidate subscription이 아직 없을 때 non-fixture broad-scanner foundation과 durable M4.2 snapshot을 만들 수 있는지 확인한다.
- 최초 관찰: actual security master는 있었지만 production projection은 별도 ready foundation을 요구했고, 그 foundation의 다음 입력으로 SIP runtime을 지목했다. 그러나 SIP bounded subscription은 broad scanner candidate가 먼저 있어야 결정되므로 운영 순서가 순환했다.
- 수정: complete KIS 상승률·거래량 6개 coverage와 NYSE halt coverage, 1일 이내 Alpaca security snapshot을 검증해 세 source의 causal ready foundation을 결정적으로 만든다. exact foundation JSON과 security snapshot ID를 schema v2 scanner projection row에 저장하고 latest reader가 canonical dataset과 foundation을 함께 재검증한다. KIS watch는 projection store·canonical root·security store 세 경로를 all-or-none으로 하위 scan에 전달한다.
- 결과: 실제 13,011-instrument snapshot과 synthetic KIS Opportunity의 local E2E가 external I/O 없이 ready foundation, canonical candidate 1개, raw/projection row 각 1개를 만들었다. SIP는 broad selection 뒤 feature gate로 남고 계좌·주문·mutation 권한은 추가되지 않았다.

## H57: 다중 desired candidate가 단일 SIP checkpoint와 writer를 공유한다

- 판별 기준: 두 instrument를 같은 policy cycle에서 수집하고 한 종목의 gap·provider 실패 및 프로세스 재시작이 다른 종목의 checkpoint, receipt와 feature binding에 영향을 주는지 확인한다.
- 최초 관찰: Alpaca SIP adapter는 의도적으로 exact instrument/symbol 하나만 허용하고 supervisor checkpoint는 provider source ID로 조회한다. 여러 종목을 한 runtime DB나 adapter에 넣으면 validation 또는 source checkpoint 충돌이 발생한다.
- 수정: policy capacity 안의 desired subscription마다 instrument/symbol SHA-256 owner를 만들고 mode-700 전용 디렉터리 아래 runtime/evidence SQLite를 각각 mode 600으로 유지한다. global decision은 owner별 exact one-symbol decision으로 축소되고 READY feature만 symbol binding으로 반환한다. owner 생성·provider 오류는 typed failure로 격리하고 symlink root와 request coverage mismatch는 HTTP 전에 차단한다.
- 결과: 두 owner fixture가 각각 35개 완료 분봉과 canonical evidence를 만들고 M4.4 gate가 READY가 됐다. 한 owner gap·503에서는 다른 binding만 보존되어 gate가 missing evidence로 닫혔다. 재시작은 owner별 기존 20개 뒤 15개만 추가했다. 실제 운영은 historical intraday volume-profile denominator lineage가 생길 때까지 열지 않는다.

## H58: 현재 누적 거래량이나 임의 숫자를 RVOL denominator로 사용한다

- 판별 기준: runtime request가 historical point-in-time lineage 없이 숫자만 받을 수 있는지, 목표일 뒤 데이터나 누락된 최근 세션이 profile에 들어가는지, profile 변경이 최종 M4.4 evidence identity에 반영되는지 확인한다.
- 최초 관찰: `RuntimeFeatureRequest.expected_cumulative_volume`은 양수 Decimal만 검사했고 어떤 과거 세션에서 계산했는지 증명하지 않았다. 동일 숫자를 KIS 현재 누적 거래량이나 수동 값으로 넣어도 구별할 수 없었다.
- 수정: 목표 분까지 거래 가능한 직전 20개 완료 정규장을 exact calendar 날짜로 요구하고, 각 세션의 정규장 전체 연속 1분봉에서 해당 분 누적 거래량을 계산해 median evidence를 만든다. verified replay identity, source 날짜·누적값, 목표일·분, version과 SHA-256을 불변 객체에 결합했다. runtime policy 평가일과 목표일 불일치, 정규장 시작이 아닌 현재 bar 창, profile보다 많은 현재 bar도 차단한다.
- 결과: 20일 historical fixture replay가 두 종목별 denominator를 만들고 독립 SIP owner, canonical feature와 M4.4 READY gate까지 도달했다. profile identity만 바꿔도 derived Opportunity ID가 달라졌다. 현재·미래·오래된·공백·미완료·변조 profile은 모두 차단됐다. 실제 외부 GET, account/order endpoint와 POST/DELETE mutation은 0건이다.

## H59: 20개 세션을 매번 다시 GET하거나 여러 replay를 하나의 가짜 identity로 축약한다

- 판별 기준: 첫 수집 뒤 새 process가 provider를 열지 않고 같은 profile을 재생하는지, 20개 session replay가 개별 lineage로 보존되는지, 불완전 응답과 canonical 변조에서 network fallback을 하는지 확인한다.
- 최초 관찰: 순수 profile builder는 하나의 `ResearchInputIdentity`가 전체 20세션을 대표한다고 가정했고 실제 provider raw/canonical archive를 읽는 경로가 없었다. 기존 장중 runtime은 장 종료 시 마지막 분까지 완료 세션을 만들 수 없어 historical source로 부적합했다.
- 수정: profile evidence가 exact source 날짜와 정렬된 20개 세션별 verified replay identity를 직접 보존하도록 바꿨다. 별도 historical collector는 각 정규장 전체를 GET-only page client로 읽고 response bytes를 먼저 append한 뒤 exact sequence 1..close를 검증하고 canonical projection한다. 저장된 page index/token chain, receipt ID, payload hash와 terminal token을 재검증해 재시작 시 HTTP 없이 재생한다.
- 결과: 첫 fixture는 20 GET과 20 canonical dataset을 만들었고 두 번째 process는 GET 0건으로 동일 evidence를 반환했다. 마지막 1분 누락은 raw page를 보존하면서 profile을 차단했고 Parquet 변조도 HTTP fallback 없이 차단했다. account/order endpoint와 mutation은 0건이다.

## H60: 운영 profile JSON을 신뢰하거나 private state 없이 credential collector를 실행한다

- 판별 기준: 저장 artifact의 source dataset ID를 바꾸거나 filename·mode·symlink root를 조작했을 때 reader가 거부하는지, actual credential CLI가 data GET 이외 endpoint를 여는지, 재실행이 같은 20세션을 다시 요청하는지 확인한다.
- 수정: CLI가 먼저 mode-700 state root를 확정하고 그 아래 evidence SQLite, canonical root와 content-addressed mode-600 profile을 쓴다. JSON load는 각 `ResearchInputIdentity`의 canonical payload SHA-256을 재계산하고 전체 profile을 다시 생성해 median, source dates, semantic version, evidence SHA와 filename을 비교한다. data client는 `https://data.alpaca.markets`와 redirect off로 고정했다.
- 실제 관찰: AAPL canonical alias와 Paper data credential로 target 2026-07-20, through minute 35를 실행했다. historical GET 20건으로 raw page 20개, canonical session 20개와 profile 1개가 생성됐다. 즉시 재실행은 `new raw page: 0`이었다. 그 전에 잘못된 security-master SQL/table 및 symbol field 조회 두 번은 HTTP 전에 blocked됐고 profile을 만들지 않았다.
- 결과: artifact 변조·symlink·mode 불일치와 불완전 CLI 인자는 차단됐다. actual account/order endpoint, POST/DELETE mutation은 0건이다.

## H61: owner별 runtime 결과가 메모리에서만 결합되어 재시작 후 cycle을 감사할 수 없다

- 판별 기준: policy decision, profile request, 두 owner 결과와 M4.4 gate를 하나의 durable identity로 재생할 수 있는지, 한 owner gap이 gate 결과와 함께 보존되는지, SQLite payload 변조를 탐지하는지 확인한다.
- 수정: desired 순서별 instrument/symbol, profile evidence SHA, owner/runtime status, connection epoch, last sequence, ready feature replay identity와 gate status/reason/opportunity ID를 canonical JSON으로 직렬화하고 deterministic cycle ID를 계산한다. mode-600 SQLite는 update/delete trigger와 exact retry 비교를 사용하며 reader는 payload SHA·canonical bytes·cycle ID를 재검증한다.
- 결과: 두 owner READY cycle과 BBB gap의 `ready/blocked`, fleet `degraded`, gate `missing_evidence`가 각각 round-trip됐다. trigger를 제거한 뒤 payload를 `{}`로 바꾼 공격은 latest replay에서 차단됐다. account/order 데이터와 mutation 권한은 추가되지 않았다.

## H62: scanner, profile, runtime을 수동 인자로 조합해 다른 세대나 다른 분의 증거를 섞는다

- 판별 기준: scanner raw Opportunity과 broad snapshot이 같은 projection 세대인지, desired candidate마다 현재 완료 분의 exact profile이 있는지, 만료·폐장·stale·coverage mismatch가 credential/HTTP 전에 닫히는지 확인한다.
- 최초 관찰: 개별 scanner reader, profile artifact, fleet와 audit 계약은 있었지만 운영 호출자가 임의 Opportunity 또는 서로 다른 시점 profile을 넘겨도 하나의 상위 경계에서 막는 실행 경로가 없었다.
- 수정: projection row와 raw row를 같은 read-only query로 join하고 raw/canonical/foundation hash, 관측시각과 symbol 순서를 재검증하는 bundle reader를 추가했다. 운영 preflight는 full candidate policy, opportunity 유효기간, target session과 현재 완료 분, desired instrument별 artifact coverage를 확인한 뒤에만 credential loader와 Alpaca data client에 도달한다. 실행 뒤 fleet result, M4.4 gate와 audit append를 한 결과로 반환한다.
- 결과: 두-owner library E2E와 한-owner CLI E2E가 READY에 도달했고 CLI는 `/v2/stocks/bars` GET만 1건 열었다. malformed profile과 폐장/누락 scanner는 audit·credential·HTTP 전에 blocked됐다. account/order endpoint와 mutation은 0건이다.

## H63: 프로세스 재시작마다 active/cooldown을 비워 정책 회전 제한을 우회한다

- 판별 기준: 신규 종목의 최초 구독 뒤 재시작해도 minimum residency가 유지되는지, 퇴출 뒤 cooldown 중 재시작해도 고득점 재진입이 차단되는지 확인한다.
- 최초 관찰: 단발 운영 CLI가 매번 `active=()`, `cooldowns=()`로 policy를 평가해 순수 정책의 체류·냉각 계약이 프로세스 경계에서 사라졌다.
- 수정: exact policy decision SHA, evaluated time, desired별 최초 subscribed time과 unexpired cooldown을 content-addressed append-only state로 저장한다. READY preflight 뒤 policy intent를 먼저 확정하고 provider 결과는 별도 fleet audit에 둔다. 파일은 mode 600/current user/regular/no-symlink, payload hash와 state ID 재계산, `BEGIN IMMEDIATE` single writer를 요구한다.
- 결과: 30초 재시작에서는 100점 challenger가 incumbent를 밀어내지 못했고, 3분 후 정상 퇴출된 incumbent는 5분 cooldown 동안 재진입하지 못했다. state payload·mode·symlink 변조는 replay에서 차단됐다. provider 연결 성공이나 broker/account/order 상태는 이 state가 표현하지 않는다.

## H64: 현재 분 profile 파일을 사람이 매 cycle 선택한다

- 판별 기준: policy가 선택한 exact desired set을 중복 계산하지 않고 profile collector에 전달하는지, 같은 20일 history를 매분 다시 GET하지 않는지, profile 분과 runtime 완료 분이 일치하는지 확인한다.
- 최초 관찰: 기존 운영 CLI는 검증된 `--profile INSTRUMENT=PATH`를 요구했지만 다음 분이 되면 다른 artifact 경로를 사람이 다시 계산해야 했다. policy decision은 profile binding 내부에 있어 자동 collector가 desired set을 얻으려면 policy를 중복 실행해야 했다.
- 수정: scanner→policy 검증을 provider-free scope로 분리하고 exact completed minute와 desired set을 반환한다. 자동 materializer는 instrument/symbol hash별 private cache에서 기존 historical raw page/canonical replay를 재사용하고 그 분의 content-addressed profile을 만든 뒤 strict binding 단계로 넘긴다.
- 결과: 2종목 첫 실행은 historical GET 40건, 동일 scope 재실행은 0건이었다. CLI 1종목은 historical 20건과 current 1건 모두 Alpaca data GET만 사용해 READY가 됐다. 수동/자동 입력 동시 사용은 argparse에서 차단되고 account/order mutation은 0건이다.

## H65: 한 번의 stale scanner/provider 실패로 장중 runtime loop 전체가 종료된다

- 판별 기준: 첫 cycle이 blocked여도 다음 분 operation이 실행되는지, 모든 시도가 durable audit에 남는지, 정규장 종료 뒤 추가 호출이 없는지 확인한다.
- 최초 관찰: 단발 runtime CLI와 fleet audit은 성공/부분 실패 cycle을 보존했지만 profile 이전 preflight block은 fleet audit을 만들 수 없었고 반복 수명주기 계약도 없었다.
- 수정: provider-neutral bounded supervisor가 operation block을 구조화된 `runtime_cycle_blocked`로 변환하고 READY/blocked attempt 모두 별도 deterministic record로 append한다. 최대 390회와 interval 범위를 검증하고 매 operation 전에 New York 정규장 경계를 재확인한다.
- 결과: 첫 blocked 뒤 두 번째 READY 회복이 기록됐고 15:59 한 번 실행 뒤 16:00에는 호출하지 않았다. payload/mode/symlink 변조는 replay에서 차단됐다. 이 supervisor 계약에는 credential, account, order 필드가 없다.

## H66: supervisor가 이전 성공 fleet audit을 현재 blocked cycle의 성공으로 재사용한다

- 판별 기준: 매 attempt scanner를 다시 읽는지, child cycle 성공 뒤 audit evaluated time이 현재 attempt와 정확히 같은지, 두 번째 분이 historical cache를 재사용하는지 확인한다.
- 최초 관찰: supervisor 순수 계약은 있었지만 실제 자동 cycle과 연결되지 않아 stale scanner block 뒤 `latest()`의 과거 READY audit을 잘못 연결할 위험이 남았다.
- 수정: CLI operation은 현재 evaluated time을 단발 자동 cycle에 전달하고 exit 0 뒤에도 fleet audit의 evaluated time을 exact 비교한다. 불일치·누락·reader 변조는 `runtime_cycle_blocked`로 supervisor audit에만 남기고 다음 attempt를 계속한다.
- 결과: fresh scanner를 sleeper에서 추가한 2-cycle soak가 historical 20 + current 2, 총 GET 22건과 READY record 2개를 만들었다. 폐장 시작은 credential과 두 audit DB를 열지 않았다. account/order endpoint와 mutation은 0건이다.

## H67: canonical correction/tombstone 필드는 있지만 history 의미를 검증하지 않는다

- 판별 기준: 여러 immutable dataset에 걸친 원본, 정정과 삭제를 당시 `normalized_at` 기준으로 재생하고 branch·missing target·identity 변경을 차단하는지 확인한다.
- 최초 관찰: 개별 envelope는 correction target 문자열의 형태만 검사했고 Parquet writer와 DuckDB replay도 각 dataset의 schema/hash만 검증했다. 없는 event를 정정하거나 동일 원본에서 두 갈래로 분기하고 tombstone 뒤 다시 정정해도 dataset 간 history 단계에서 막는 경로가 없었다.
- 수정: verified dataset event reader와 별도 history engine을 추가했다. 전체 chain은 직전 active target 하나만 후속 event를 가질 수 있고 source, event type, provider event ID와 entity refs가 같아야 한다. received/normalized 시각 역행을 금지하고 as-of 뒤 normalized event는 당시 active projection에서 제외한다.
- 결과: correction 시 active event가 후속 버전으로 교체되고 tombstone 시 root chain이 active state에서 제거된다. 원본 bytes와 immutable event는 그대로 보존된다. local CLI는 집계만 mode 600 보고서로 출력하고 provider, credential, account/order endpoint를 열지 않는다.

## H68: source 관측시각을 entitlement 계약 발효시각으로 재사용한다

- 판별 기준: 같은 source 계약이 여러 scanner cycle에서 동일한지, runtime health만 시점별로 append되는지, as-of 조회가 미래 assessment와 겹치는 권한을 배제하는지 확인한다.
- 최초 관찰: broad-scanner foundation은 최신 source receipt time을 entitlement `effective_from`으로 넣었다. 같은 entitlement ID의 payload가 매 cycle 달라져 immutable registry에 등록할 수 없고 데이터 수신 성공을 이용권 발효처럼 표현했다.
- 수정: entitlement는 2026-07-17 등록 계약 버전의 고정 발효일을 사용하고 capability의 assessed/latest event 시각만 cycle마다 변경한다. 별도 mode-600 append-only registry는 entitlement와 capability assessment를 분리해 저장하고 UTC as-of snapshot을 제공한다.
- 결과: 서로 다른 두 scanner cycle의 entitlement tuple은 같고 capability assessment 시각만 다르다. exact retry는 추가 row 0건이며 overlapping entitlement, 동일 source/time conflict, payload/interval 변조와 symlink/mode 위반은 fail-closed다. local CLI 재평가 외 provider·credential·broker 접근은 0건이다.

## H69: 성공한 희소 source poll에 가짜 최신 event 시각을 부여한다

- 판별 기준: DART·뉴스 source run이 성공했지만 record count가 0일 때 실제 event 없이 current health를 표현할 수 있는지, 실패 run이나 다른 adapter/cycle을 같은 상태로 투영하지 않는지 확인한다.
- 최초 관찰: `DataCapability`의 complete/degraded 상태는 `latest_event_received_at`을 필수로 요구했다. 이를 그대로 KR source run에 연결하면 zero-record poll에 존재하지 않는 event 시각을 만들거나 정상 transport poll을 incomplete로 잘못 축약해야 했다.
- 수정: 실제 event 수신시각과 source poll heartbeat를 별도 필드로 분리하고 둘 중 최신 causal 시각으로 freshness를 평가한다. KR projection은 exact 네 source의 run ID, adapter version, cycle/date와 terminal status를 검증하고 성공 run에는 heartbeat만, 실패 run에는 failed health를 append한다. LS 뉴스는 tombstone 가능 정책, 나머지는 append-correction 정책으로 고정한다.
- 결과: zero-record 네 source가 fake event 없이 complete health로 등록되고 exact retry는 capability·entitlement 추가 0건이다. 미래 heartbeat, mixed run, failed source는 fail-closed 또는 incomplete로 보존된다. provider·credential·account/order endpoint와 broker mutation은 0건이다.

## H70: fleet audit 파일 경계와 owner coverage 없이 runtime source를 complete로 본다

- 판별 기준: mode·소유자·symlink가 잘못된 fleet audit을 registry가 거부하는지, 두 owner 중 하나의 sequence gap을 source complete로 축약하지 않는지 확인한다.
- 최초 관찰: fleet audit은 payload hash와 cycle ID를 재계산했지만 reader가 private file mode·소유자·symlink를 검사하지 않았고 writer도 explicit single-writer transaction을 시작하지 않았다. 전역 capability registry로 연결되는 owner coverage 계약도 없었다.
- 수정: audit store에 mode 600/current owner/regular file/no-symlink와 `BEGIN IMMEDIATE`를 추가했다. projection은 policy/fleet/gate 상호일관성, owner/runtime status, profile·feature digest와 unique symbol을 재검증하고 owner READY 비율을 source completeness bps로 집계한다.
- 결과: 2/2 READY는 complete 10000 bps, 1/2 sequence gap은 degraded 5000 bps로 append된다. cycle 완료시각은 실제 event 시각을 위조하지 않고 source heartbeat에만 기록된다. local CLI exact retry는 추가 row 0건이며 provider·credential·account/order endpoint와 mutation은 0건이다.

## H71: metadata-only canonical envelope에서 claim 내용을 추측한다

- 판별 기준: claim extraction이 active canonical event의 exact source/content/raw receipt/entity에 결합되는지, LLM model·prompt·output identity 없이 증거가 생성되는지, derived artifact가 원문 reference를 재배포하는지 확인한다.
- 최초 관찰: canonical Parquet는 event envelope와 content hash를 보존하지만 정규화된 뉴스·공시 내용은 포함하지 않는다. 이 envelope만으로 claim을 생성하면 hash에서 의미를 추측하거나 별도 extractor의 lineage를 잃게 된다.
- 수정: 별도 extraction 계약이 event ID, content hash, source, raw receipt와 entity set을 모두 고정한다. deterministic과 LLM 방식을 분리하고 LLM은 model/prompt version을 필수화했다. read model은 active event exact match 뒤에만 독립 source, stance conflict, current/baseline novelty와 burst를 계산한다.
- 결과: source/hash/receipt/entity mismatch, future extraction과 tombstone event는 fail-closed다. content-addressed mode-600 artifact에는 claim evidence ID와 집계만 남고 raw receipt reference와 원문은 없다. 이 커널은 실제 extraction adapter나 주문·승격 권한을 만들지 않는다.

## H72: typed intraday 지표를 canonical event lineage 없이 연구 claim으로 사용한다

- 판별 기준: breakout·RVOL 값이 READY snapshot의 exact verified dataset, 마지막 완료 1분봉, 20일 volume profile과 함께 재검증되는지, 서로 다른 RVOL 기준이나 종목이 같은 claim으로 섞이는지 확인한다.
- 최초 관찰: M4 runtime은 causal typed indicator와 replay identity를 함께 만들었지만 research evidence read model에 공급하는 adapter가 없었다. feature hash만 복사하면 어떤 dataset·event·threshold에서 나온 값인지 독립적으로 대사할 수 없었다.
- 수정: US SIP deterministic adapter가 snapshot identity를 canonical Parquet/DuckDB replay에서 다시 만들고 source, entity, event count·연속 시각, 정규장 개장부터 profile `through_minute`까지의 exact 구간, 마지막 완료 분봉, receipt·normalization causality와 volume-profile evidence를 검증한다. breakout bool과 RVOL threshold stance를 exact event에 결합하고 threshold·전체 typed indicator·identity를 output hash에 포함한다. fleet CLI는 opt-in으로 owner마다 별도 read model artifact를 만든다.
- 결과: wrong dataset, blocked snapshot, missing latest event, future normalization과 invalid threshold는 provider 재호출 없이 차단됐다. 두 owner는 서로 다른 artifact/read model로 유지되고 단일 `alpaca/sip` evidence는 `unconfirmed`다. mode-600 derived artifact에는 raw receipt reference가 없으며 account/order endpoint와 mutation은 0건이다.

## H73: 같은 classifier version 문자열만으로 저장된 KR 분류를 신뢰한다

- 판별 기준: 원장에 저장된 DART·LS keyword classification과 run manifest가 같은 classifier/prompt version을 가지지만 실제 keyword rules 내용이 바뀐 상태로 research evidence를 재생한다.
- 최초 관찰: version과 분류시각만 비교하면 keyword·theme·related symbol 규칙이 바뀌어도 과거 classification을 현재 manifest의 결과처럼 사용할 수 있었다. 이는 사전등록되지 않은 사후 테마·종목 결합을 숨길 수 있다.
- 수정: query-only projection이 run manifest의 exact `KrKeywordRuleSet`과 `classified_at`으로 모든 eligible catalyst를 다시 분류하고 저장된 `KrThemeClassification` 전체와 동등성을 검사한다. DART는 exact `OpenDartDisclosure` canonical JSON, LS는 허용된 flat canonical JSON을 strict wire frame으로 재구성해 기존 parser 결과와 대사하며 raw receipt link·terminal source run·adapter version·payload hash도 함께 고정한다.
- 결과: 같은 version을 유지한 rules 변조, source/run/receipt/payload 불일치와 비인과 분류는 artifact 전에 fail-closed다. exact DART·LS 두 source가 같은 사전등록 theme/entity를 지지할 때만 corroborated가 되고 derived artifact에는 원문·evidence quote·raw receipt reference가 없다. provider·credential·account/order endpoint와 mutation은 0건이다.

## H74: broad scanner의 source evidence reference를 독립 corroboration으로 과장한다

- 판별 기준: 하나의 KIS Opportunity에 포함된 KIS ranking·NYSE halt reference를 별도 canonical source event처럼 세어 scanner candidate claim을 corroborated로 만드는지 확인한다.
- 최초 관찰: scanner projection은 raw Opportunity와 candidate event를 보존하지만 event source는 `internal/us_opportunity` 하나다. Opportunity의 evidence reference는 selection 입력 계보다. 이를 별도 event로 제조하면 실제 provider normalized payload·receipt 검증 없이 독립 source 수를 부풀리게 된다.
- 수정: query-only loader가 scanner SQLite의 raw Opportunity·ready foundation·optional security-master ID와 verified Parquet event를 결합한다. raw receipt identity, dataset ID, candidate symbol/rank/score, instrument, canonical candidate payload hash와 event 시간을 exact 대사하고 factual `ranking_momentum` selection claim만 추출한다. KIS·NYSE reference는 output hash lineage에만 포함한다.
- 결과: security-master ID, raw receipt, candidate shape 또는 canonical event가 바뀌면 artifact 전에 fail-closed다. source는 `internal/us_opportunity` 하나여서 claim은 `unconfirmed`이고 derived artifact에는 raw receipt와 source evidence reference가 없다. provider·credential·account/order endpoint와 mutation은 0건이다.
