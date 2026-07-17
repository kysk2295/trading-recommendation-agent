# Grok Development Harness 체크포인트

- 날짜: 2026-07-18
- 범위: 개발 작업 격리·검증 도구
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건

## 구현

- `GrokTaskContract`는 base commit, 허용 파일, 검증·수동 QA 명령과 worker summary 필드를 immutable JSON contract로 고정한다.
- absolute/traversal path, `.git`·`.grok`·`.hermes`, 환경·비밀 관련 경로, 중복/빈 경로, shell metacharacter가 있는 명령, 잘못된 commit과 unknown field는 sanitized error로 거절한다.
- runner는 exact HEAD와 contract base를 대조하고, checkout이 clean이거나 사전 존재하는 user-owned `?? .hermes/` 하나일 때만 진행한다.
- dry-run은 Git worktree와 Grok process를 만들지 않는다.
- 실제 worker는 task별 branch/worktree에서만 실행하며, strict sandbox, web/subagent 비활성화, network/push 금지, 허용 경로 대조를 받는다.
- worker stdout, prompt, objective, absolute path, credential과 provider payload는 public report에 포함하지 않는다.
- worker가 local commit을 해도 base-to-HEAD diff와 working-tree status를 모두 검사한다. 허용 경로 밖 변경은 fail-closed이며 worktree는 자동 삭제하지 않는다.

## Bootstrap 제한

Grok CLI는 설치·로그인과 `grok-4.5` model discovery까지 확인됐지만, 이 checkpoint의 실제 worker invocation은 account spending limit의 HTTP `402`로 모델 실행 전에 중단됐다. 코드·테스트·worker worktree는 생성되지 않았다. 따라서 하네스 자체는 Codex가 별도 ignored worktree에서 TDD로 구현했고, 이 사실은 Grok worker가 구현한 결과로 표현하지 않는다.

## 검증

- task contract validation: targeted pytest
- Git preflight, dry-run, worker nonzero, and out-of-scope changed-path rejection: temporary Git repository pytest
- CLI `--help`, malformed contract, and dry-run happy path: subprocess pytest
- full pytest, Ruff, basedpyright, and `git diff --check`: final integration gate

## 운영 규칙

`run_grok_task.py`는 거래 런타임이 아니다. 작업마다 새 contract를 base commit에 맞춰 만들고, worker가 끝난 뒤 Codex가 contract compliance와 code quality를 독립 review한다. 승인 전에는 cherry-pick, `main` commit, push 또는 worker worktree removal을 수행하지 않는다.
