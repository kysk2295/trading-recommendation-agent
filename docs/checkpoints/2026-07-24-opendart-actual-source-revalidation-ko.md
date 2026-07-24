# OpenDART actual source 재검증

- 구현 커밋: `6413af773d71638068f1e3476a86dc919056a80d`
- operation: 공식 `GET /api/list.json` read-only
- credential: 현재 사용자 소유 mode `0600` regular file
- 계좌·주문·포지션 mutation: `0`

## 실제로 닫은 응답 계약

별도 isolated source 원장에서 2026-07-24 공시검색을 실제 실행했다. 첫 raw-first
시도는 HTTP `200`, provider status `000`, page `1/3`, total `263`을 보존했지만
strict parser가 `invalid_response`로 닫았다.

비밀값과 공시 본문을 출력하지 않고 field type·길이·문자 클래스만 대사해 현재 공식
응답의 두 경계를 확인했다.

1. 첫 페이지 100건 중 `report_nm` 21건에 경계 공백이 있었다.
2. KR instrument v2에 해당하는 6자리 영숫자 `stock_code`가 있었다.

raw receipt는 수정하지 않는다. Pydantic boundary에서 `report_nm`의 경계 공백만
제거하고, OpenDART의 숫자 전용 사설 정규식 대신 기존 공통
`is_kr_instrument_symbol_v2`를 사용한다. lowercase·길이 오류, extra field,
잘못된 receipt/date와 provider/HTTP/content-type 실패는 계속 fail-closed한다.

## exact committed runtime actual evidence

clean exact runtime에서 새 isolated cycle을 다시 실행했다.

- source result/failure: `success/none`
- HTTP/provider operation: 공식 GET 3회
- raw receipt: `3`
- catalyst: `272`
- observation: `272`
- report/database/WAL/lock mode: 모두 `0600`
- database SHA-256:
  `6cd8aa1baefe8ea71813519168aebea148d8d743819808c77296dee1d00f65d4`
- actual report SHA-256:
  `bd678e5395026610606a014d7ec419e15125c4bd4d5953a498d2eefaae3e9a8e`
- credential value가 evidence 6개에 존재한 파일: `0`

같은 cycle을 존재하지 않는 credential path로 재실행했다.

- exit: `0`
- restarted no-op: `yes`
- receipt/catalyst/observation 신규: `0/0/0`
- provider/credential access: `0/0`
- replay report SHA-256:
  `c4dca5a3a4ab6079363c5073908e3732059e149cc38bea0490323e8e3a271018`

앞선 실패 두 cycle과 raw receipt는 삭제하거나 성공으로 바꾸지 않았다. 이 smoke는
오늘 09:05의 four-source 실패 cycle과 다른 isolated source run이며, 과거 cycle을
소급 완성하지 않는다.

## 검증

- RED 1: 경계 공백이 있는 official-shaped `report_nm`은 `invalid_response`
- RED 2: official-shaped 영숫자 6자리 `stock_code`는 `invalid_response`
- focused OpenDART client/collection/CLI: `31 passed`
- full pytest: `3646 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- CLI `--help`: exit `0`
- actual happy/replay: exit `0/0`
- external mutation: `0`

다음 실제 검증은 2026-07-27 08:30 credential readiness와 09:05 same-cycle
OpenDART → LS NWS → KIS ranking → volume surge terminal chain이다. source 하나라도
실패하면 Opportunity을 만들지 않고 기존처럼 fail-closed한다.
