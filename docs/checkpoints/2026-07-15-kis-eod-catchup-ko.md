# KIS 장마감 마지막 봉 catch-up 체크포인트

작성일: 2026-07-15 KST

## 발견한 결손

09:30부터 60초 간격으로 390번 scan하면 마지막 child가 보통 15:59대에 시작한다. 완료 봉 cutoff는 관찰시각보다 1분 전이므로 이 child가 저장하는 마지막 봉은 15:58일 수 있다. 기존 watcher는 바로 time-exit과 metrics로 넘어가 장마감 challenger가 요구하는 09:30~15:59 390봉 경로를 구조적으로 완성하지 못할 수 있었다.

## 수정 계약

- 하루 감시가 공식 close 3분 이내에 끝난 경우에만 close+65초까지 제한적으로 기다린다.
- 별도 EOD child가 당일 `tracked_candidates` 전체를 종목별 한 페이지, 순차적으로 조회한다.
- 정확한 15:59 봉을 확인해야 해당 종목을 complete로 기록한다.
- 읽은 분봉은 16:01대 실제 최초 관찰시각으로 저장해 장중 과거 신호에 사용되지 않게 한다.
- 이미 열린 추천은 마지막 봉으로 상태 갱신하지만 신규 신호·추천·후보 입력 snapshot은 만들지 않는다.
- EOD child가 끝난 뒤 time-exit과 metrics를 실행한다.
- 정규장 watch retry 감사와 EOD retry 감사를 서로 다른 파일로 유지한다.
- 짧은 QA watch가 폐장보다 3분 이상 일찍 끝나면 폐장까지 기다리지 않는다.

## 검증

- HTTP wire mock의 15:59 KIS 분봉을 16:01:05 ET에 읽어 `candidate_minute_bars.first_observed_at=16:01:05`, checkpoint=15:59로 저장했다.
- 같은 입력에서 기존 ACTIVE 추천 1건만 갱신되고 신규 추천은 생기지 않았다.
- 마지막 응답이 15:58이면 `오류: 장마감 마지막 완료 봉 없음`으로 종료하고 checkpoint를 15:58에서 전진시키지 않았다.
- 폐장 뒤에도 거래일 기준 tracked 후보를 다시 읽는 경로와 거래소 간 중복 ticker 거부를 구현했다.
- EOD 후보 0개 CLI happy path는 자격증명·네트워크 없이 요약과 retry 0건을 기록했다.
- 실제 진행 중 세션에서 정규장 전 CLI를 실행해 외부 API 호출 전에 exit 2로 차단됨을 확인했다.
- 전체 pytest 451개, Ruff lint, basedpyright, 변경 파일 Ruff format과 no-excuse 검사가 통과했다.

실제 KIS 15:59 응답 검증은 현재 정규장 종료 후 아직 수행하지 않았다. 현재 실행 중 watcher parent는 이전 코드를 이미 메모리에 로드했으므로 이 변경은 병합 뒤 다음 watcher부터 자동 적용된다. 오늘 세션은 기존 실패와 장중 도입 결손 때문에 catch-up 성공 여부와 무관하게 적격일로 승격하지 않는다.

이 기능은 분봉 경로 완전성 보강이며 수익성 증거가 아니다. halted·무거래 등으로 1분봉이 실제 누락된 종목은 합성하지 않고 challenger에서 censored로 남긴다.
