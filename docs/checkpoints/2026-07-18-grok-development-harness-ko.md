# Grok Development Harness 체크포인트

- 날짜: 2026-07-18
- 범위: 개발 작업 격리·검증 도구 (in-place bounded worker)
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건

## 구현

- `GrokTaskContract`는 base commit, 허용 파일, 검증·수동 QA 명령과 worker summary 필드를 immutable JSON contract로 고정한다. `required_commands`는 pytest·Ruff·basedpyright를 모두 포함해야 하고, `manual_qa_commands`는 no-op(`python -c pass`)이 아닌 안전한 repository-relative Python CLI help를 요구한다.
- absolute/traversal path, `.git`·`.grok`·`.hermes`·`.omo`, 환경·비밀 관련 경로, 중복/빈 경로, path/command 개수·길이 상한 초과, shell metacharacter가 있는 명령, 잘못된 commit과 unknown field는 sanitized error로 거절한다.
- runner는 exact HEAD와 contract base를 대조하고, checkout이 clean이거나 사전 존재하는 user-owned untracked `.hermes/` 및/또는 `.omo/` 상태(중첩 untracked 포함)일 때만 진행한다. dirty checkout 검사는 `git status --porcelain=v1 -z --untracked-files=all`로 `status.showUntrackedFiles=no`여도 untracked를 강제 보고한다.
- preflight는 `main` branch 전용이며 linked worktree와 repository path의 symlink component(중간 경로 포함)를 거절한다. 실행 전·후 workspace snapshot은 user-owned/ignored 파일·디렉터리(빈 ignored 디렉터리 포함)·visible worktree entry metadata(allowed path·required parent·ignored·user-owned 제외)·unignored empty directory inventory와 **metadata-only full `.git` topology/state inventory**를 비교한다. binary `.git/index` metadata는 fingerprint에서 제외하고 logical index는 `git ls-files --stage -v -z`로 유지한다. inventory는 `index.lock`·`sharedindex.*`·Git operation state·every internal symlink·symlinked objects root를 fail-closed로 거절하며, `.git/index`는 current-user-owned regular file이고 `st_nlink=1`이어야 한다. walk/stat/enumeration 오류는 모두 fail-closed다. **사전 존재**하는 assume-unchanged/skip-worktree flag와 sparse-checkout masking도 preflight에서 거절하고, worker·independent verification 뒤에도 sparse-checkout fingerprint/reject를 다시 적용한다. unignored empty directory 생성/삭제는 fail-closed이며 allowed path에 필요한 missing parent directory만 예외로 허용한다. commit 후 reset으로 HEAD만 되돌리거나 local config/hook을 바꾸는 것도 fail-closed다. allowed path의 symlink component도 실행 전·후에 거절한다.
- dry-run은 Grok process를 호출하지 않고 Git 상태도 바꾸지 않는다.
- 실제 worker는 worktree/branch/clone 없이 현재 repository root에서 in-place non-interactive로 실행한다. allow-list 안 working-tree 파일은 수정할 수 있지만 main history에 commit/push 하면 안 된다.
- 생성 명령은 `--cwd`(repository root), `--always-approve`, `--permission-mode bypassPermissions`, `-p` single-turn, `--output-format json`, strict `--json-schema`(changed_files/verification/concerns), `--no-plan`, `--no-subagents`, `--disable-web-search`, `--no-memory`, `--max-turns`를 사용한다. `--sandbox strict`와 branch/worktree 생성은 사용하지 않는다.
- worker process는 새 process group에서 실행되고 stdout은 임시 regular file로 리다이렉트한 뒤 size/deadline를 poll한다. stderr는 DEVNULL이며 timeout·oversize 시 process group을 kill하고 parent 종료 후 survivor도 정리한다. stderr는 반환하지 않는다.
- harness Git·worker·verification subprocess는 ambient 환경에서 이름이 `GIT_`로 시작하는 모든 키를 fail-closed로 제거한다(allow-list가 아님).
- worker launch 직전에 clean snapshot/root를 다시 검증하고, post-worker Git inventory 전에 repository symlink/`.git` topology와 repo-owned current-user regular single-link `.git/index`(effective `git rev-parse --git-path index`)를 재검증한다. topology helpers는 `grok_worktree_topology.py`, visible worktree metadata는 `grok_worktree_metadata.py`, full `.git` inventory는 `grok_git_control.py`에 둔다.
- 실행 후 `git rev-parse HEAD`와 Git database/control fingerprint가 contract base snapshot과 같아야 한다. worker commit·ref/object·local config/hook·sparse-checkout 변경이 있으면 fail-closed다. Git changed path 전체를 allow-list와 대조하고 허용 밖 경로는 fail-closed다.
- independent verification success·nonzero·timeout·side effect 뒤에도 동일 post-workspace validation을 항상 다시 실행한다. timeout/OSError 경로에서도 snapshot·allow-list를 강제한다. 허용 밖 변경을 failure report로 숨기지 않는다.
- public report는 top-level `structuredOutput`에서만 bounded summary를 파싱한다. `text`의 연결 draft JSON에 의존하지 않으며 raw stdout/stderr, prompt, objective, absolute path, credential, provider payload는 노출하지 않는다.
- summary `changed_files`는 contract allow-list subset이어야 하고, Git `changed_paths`와 중복 없이 동일한 unique path 집합이어야 한다. `verification`은 required+manual 명령의 exact unique set과 일치해야 하며 subset/empty/duplicate는 거절한다. `concerns`는 고정 enum만 허용한다.
- worker process와 completed 전 offline 재검증이 동일한 `cache_disabled_environ` helper로 Python bytecode를 끄고 `PYTEST_ADDOPTS`를 정확히 `-p no:cacheprovider`로 고정한다(상속된 pytest 옵션은 fail-closed로 폐기). Ruff는 undocumented env가 아니라 documented `ruff check --no-cache`를 worker에 보이는 명령과 offline 재실행 명령 양쪽에 inject한다. offline 재실행은 `uv run --offline`이며 **process group**에서 돌고 stdout/stderr는 DEVNULL이다. success/failure/timeout 모두 ordinary background descendant를 reap한다. timeout/OSError는 worker_failed다. contract 명령에서 `compileall`은 허용되지 않으며 임의 Ruff flag도 거절한다.
- workspace snapshot fingerprint(metadata/index/Git DB)는 `development_harness/grok_workspace_fingerprint.py`로 분리하고, `grok_workspace_guard`는 path safety와 안정적 public re-export를 유지한다. process env sanitize는 `grok_process_env.py`, verification process group은 `grok_verification_process.py`다.
- `expected_summary_fields`는 정확히 `changed_files`, `verification`, `concerns`여야 한다.
- CLI는 `--worktree-root`를 요구하지 않는다. nonzero/timeout/missing binary는 안전한 `worker_failed`로 보존한다.

## Residual risk (prompt-only)

`--sandbox strict`를 쓰지 않으므로 credential 읽기, network 호출, `git push`, allow-list 밖 external write, 그리고 `setsid` 등으로 process group에서 분리된 descendant 재수거 실패는 OS sandbox가 아니라 prompt/contract/post-condition 검사에만 의존하는 residual risk다. ordinary background descendant는 process-group kill로 reap하지만 detached `setsid` 잔여는 남는다. 이 한계는 의도된 것이며, Codex independent review가 최종 통합 전 통제다.

## 현재 상태

현재 real in-place Grok worker 실행과 후속 review correction·hardening·final review blocker close·local postcheck bypass close·local harness invariant close가 반영됐다. harness는 `structuredOutput`만 소비하고, main-only preflight, process-group timeout kill, full `.git` topology/state inventory(binary index 제외, lock/sharedindex/operation/symlink reject), logical index flags, pre-existing index/sparse masking 거절, forced untracked status reporting, visible worktree metadata fingerprint, empty-directory inventory, ambient GIT_* sanitize, launch/post-worker/post-verification revalidation, user-owned/ignored metadata 보호, offline verification re-run, pytest/Ruff/basedpyright+meaningful manual QA contract rules, summary contract-safe enum을 강제한다.

## 검증

- task contract validation: targeted pytest
- Git preflight, dry-run, worker nonzero, structured summary, out-of-scope changed-path rejection: temporary Git repository pytest
- CLI `--help`, malformed contract, and dry-run happy path: subprocess pytest
- focused harness pytest, Ruff, basedpyright, and CLI help/invalid/dry-run QA
- focused harness pytest, Ruff, basedpyright, compileall, no-excuse: 0 violations
- full repository pytest final baseline: **2129 passed**

## Release checklist (harness)

1. Contract paths/commands bounds and protected roots still reject closed.
2. Preflight rejects dirty checkout (except untracked `.hermes/` / `.omo/`), non-main, linked worktree, symlink roots, pre-existing assume-unchanged/skip-worktree, sparse masking, index.lock/sharedindex/operation state/internal git symlinks/symlinked objects root, and non-regular/non-owned/multi-link `.git/index`; untracked reporting is forced despite `status.showUntrackedFiles=no`.
3. Every ambient `GIT_*` env key is stripped for harness Git, worker, and verification subprocesses.
4. Launch revalidates clean snapshot/root; post-worker and post-verification revalidate symlink/`.git` topology, index ownership, sparse-checkout, empty dirs, visible worktree metadata, and allow-list before accepting the result.
5. Worker and independent verification run in process groups; ordinary descendants are reaped; no OS sandbox / detached-setsid residual remains documented.
6. Snapshot fingerprints full `.git` topology/state (binary index excluded), refs/reflog, logical index flags, visible worktree metadata, and unignored empty directories; every walk/stat/enumeration error fails closed.
7. Contracts require pytest + Ruff + basedpyright and meaningful manual QA; production modules stay ≤250 pure LOC (no-excuse); focused harness tests + Ruff + basedpyright + CLI help pass.
8. Local Codex checkpoint commits are for exact-SHA review only; workers never commit; remote push waits for all reviewers PASS.

## 운영 규칙

`run_grok_task.py`는 거래 런타임이 아니다. 작업마다 새 contract를 base commit에 맞춰 만들고, worker가 끝난 뒤 Codex가 contract compliance와 code quality를 독립 review한다. worker는 commit/push하지 않는다. local Codex checkpoint commit은 exact-SHA review용이며, remote push는 모든 reviewer PASS 뒤에만 수행한다.
