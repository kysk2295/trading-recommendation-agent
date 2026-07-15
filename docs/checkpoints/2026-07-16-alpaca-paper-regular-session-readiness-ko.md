# Alpaca Paper 정규장 readiness 체크포인트

뉴욕 세션: `2026-07-15`

## 결과

- 실제 Alpaca Paper `POST/PATCH/DELETE`: **0건**
- 빈 Paper 계좌와 execution ledger 최초 결합: 통과
- Alpaca Paper WSS 인증·구독·Pong: 통과
- current-epoch REST·원장·포트폴리오 대사: 통과
- 정규장 market clock: 개장 확인
- 최종 미체결 주문·포지션·FILL·보호 OCO: 모두 0건
- 현재시점 ORB setup: 15:30 ET 신규진입 cutoff까지 0건

신호가 없었으므로 entry·보호 OCO·cancel·flatten mutation을 만들지 않았다. 이 실행은 수익성 표본이나 `shadow_forward` 적격일이 아니라 첫 실제 자격증명·네트워크·계좌 readiness 증거다.

## 사전 검증

- clean `main`과 `origin/main` 일치 확인
- `1319 passed`
- Ruff 통과
- basedpyright `0 errors, 0 warnings, 0 notes`
- KIS live/paper, Alpaca market-data, Alpaca Paper loader 통과
- 자격증명 값·계좌 식별자·broker ID·원시 인증 응답 비출력

맥북 프로에 있던 세 credential 파일은 일회용 공개키로 tar stdout을 CMS AES-256 암호화해 iCloud Drive로 전달했다. 평문 archive는 만들지 않았고, 맥미니에서 인증서 fingerprint·archive 항목·파일 타입·소유자·mode·허용 변수 집합·nonempty 값과 실제 loader를 확인한 뒤에만 고정 경로에 배치했다. 전송 직후 CMS 암호문·공개 인증서·일회용 개인키를 삭제했다.

## 실행 순서

1. `run_alpaca_paper_bootstrap.py`
   - 계좌·미체결 주문·포지션 GET only
   - 빈 계좌와 새 execution ledger 결합 완료
2. lane registry와 global experiment ledger local-only bootstrap
3. `run_kis_paper_watch.py`로 현재 정규장 ORB source 수집
4. `run_alpaca_paper_preflight.py`
   - 미체결 주문 0, 열린 포지션 0
5. `run_alpaca_paper_readiness.py`
   - Paper endpoint 고정
   - WSS heartbeat와 같은 epoch의 REST·원장 대사 통과
   - 신규 주문 admission은 current source가 없어 미평가
6. exact current ORB setup one-shot monitor
   - 15:30 ET 이전, age 20초 이내 setup 한 건만 production loader에 전달하도록 제한
   - setup 0건으로 entry CLI 호출 및 mutation 없음
7. `run_alpaca_paper_recovery.py`와 최종 preflight
   - 정규화 주문 0, Account Activities FILL 0, 보호 OCO 0
   - 미체결 주문 0, 열린 포지션 0

## 늦은 시작 보정

watch는 14:18 ET에 시작됐다. 최초 `max-pages=1` cycle은 최근 약 120분만 읽어 09:30~09:34 opening range가 없으므로 ORB 상태를 만들 수 없었다. 전략·위험 임계값을 바꾸지 않고 read-only `max-pages=4`로 재시작해 현재 세션 opening history를 보강했다.

보강 뒤 DB에서 확인한 범위는 09:30~14:39 ET, 21종목, 5,638개 분봉이었다. 최신 후보 집계에서도 opening price·volume 조건과 momentum scanner 조건은 각각 충족 사례가 있었지만 교집합은 0이었다. 따라서 history 보강 이후 추천 0은 입력 결손이 아니라 사전등록된 조건 미충족으로 판정했다.

## 제한과 다음 실행

- 장중 늦게 시작해 pre-open `shadow_forward` trial을 등록하지 못했으므로 이날 자료를 정식 forward-validation 표본으로 승격하지 않는다.
- 실제 entry ACK, 부분·전체 체결, Account Activities FILL, 보호 OCO, staged cancel·replacement, EOD flatten 증거는 여전히 없다.
- 다음 정규장에는 장전부터 첫 정규장 smoke 런북을 실행해 opening history를 `max-pages=1` cycle로 자연스럽게 누적한다.
- exact current ORB setup이 생길 때만 1주, notional 100 USD, 계획위험 10 USD, 포지션 1개, 일손실 30 USD 고정 한도에서 armed entry를 한 번 실행한다.
- 체결되면 즉시 GET-only recovery, 보호 OCO, cutoff/EOD staged cancel·flatten, 최종 flat 대사를 순서대로 완료한다.
