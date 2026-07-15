# Alpaca Paper CLI 오류 정보 최소화 체크포인트

확인일: 2026-07-15

상태: **첫 정규장 smoke 운영 CLI의 실행 예외 redaction 통일, 실제 Paper POST/DELETE 0건 유지**

## 목적

첫 정규장 smoke 런북에 포함되는 Paper CLI를 다시 점검한 결과, 일부 GET-only 명령이 잡힌 예외의 원문 메시지를 stderr와 Markdown 보고서에 기록하고 있었다. API·원장·파일 예외 메시지에 계좌나 broker 식별자 또는 내부 경로가 섞여도 출력 경계를 넘지 못하도록 모든 운영 CLI의 실행 예외 정책을 클래스명 전용 형식으로 통일했다.

## 적용 범위

- `run_alpaca_paper_bootstrap.py`
- `run_alpaca_paper_preflight.py`
- `run_alpaca_paper_readiness.py`
- `run_alpaca_paper_recovery.py`
- `run_alpaca_paper_mutation_recovery.py`
- `run_alpaca_paper_safety.py`
- 앞선 체크포인트에서 같은 정책을 적용한 entry·보호 OCO·안전조치 mutation smoke CLI

잡힌 실행 예외는 모두 `안전 오류 유형: <예외 클래스>`만 기록한다. bootstrap과 preflight는 안전한 stderr만 남기며, 보고서를 생성하는 나머지 명령은 stderr와 보고서 모두 같은 안전 사유를 사용한다. 정상적인 current-epoch·대사·위험 게이트의 fail-closed 판단 사유와 redacted 집계는 바꾸지 않았다.

## 검증

- 6개 GET-only·안전계획 CLI 표적 회귀: `29 passed`
- 전체 회귀: `785 passed`
- `uv run ruff check .`: 통과
- 변경 Python 12개 파일 `ruff format --check`: 통과
- `uv run basedpyright`: 오류 0, 경고 0
- 임의 민감 문자열을 넣은 `OSError`가 stderr와 생성 보고서에 남지 않는 회귀 테스트: 6개 통과
- 기존 주문 이력·Account Activities 페이지 불완전 오류도 원문 대신 예외 클래스만 기록

## 수동 CLI QA

- bootstrap·preflight·readiness·entry·보호 OCO·안전조치·두 recovery 명령의 직접 `--help`: 종료코드 0
- entry·보호 OCO·안전조치 mutation smoke의 잘못된 arm: 자격증명·DB·네트워크 진입 전 argparse 종료코드 2
- 고정 자격증명 파일은 현재 존재하지 않아 실제 provider probe나 broker mutation을 실행하지 않았다.

## 안전 상태

- live trading endpoint 지원: 없음
- Alpaca Paper POST/DELETE: 0건
- smoke 한도 변경: 없음
- 계좌 fingerprint·broker order ID·request ID·자격증명 값 출력: 없음

이 체크포인트는 오류 출력 경계만 강화한다. 정규장·현재 완료 1분봉·정확한 Paper account binding·current WSS epoch·빈 초기 broker 상태·명시적 arm이 모두 확인되기 전에는 첫 entry POST를 허용하지 않는다.
