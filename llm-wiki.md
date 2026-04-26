# llm-wiki bootstrap note

이 파일은 프로젝트 루트에 두고 에이전트 초기 컨텍스트로 사용합니다.

## 목적
- 에이전트가 코드 작성 전에 wiki 기반 팀 컨텍스트를 먼저 확인하게 강제

## 필수 규칙
1. 코드 수정 전에 `piki context <files...>` 실행
2. 아키텍처/도메인 질문 전에 `piki search "<query>"` 실행
3. 결제/인증/빌링 작업 전 `piki gotchas <repo>` 실행
4. 위키 정보와 코드가 다르면 사용자에게 충돌 사실 먼저 보고

## 초기화 순서
1. `piki skill-install` (또는 이 파일/`SKILL.md`를 수동 복사)
2. `piki init --org <org> --wiki-repo wiki --source-repos <repos>`
3. `piki ingest`
