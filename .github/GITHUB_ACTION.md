# GitHub Action Guide (piki)

이 파일은 `piki init`으로 자동 생성되었습니다.

## 목적
- `piki` 에서 `main` 대상 PR merge 시 `wiki` 저장소로 이벤트를 전달합니다.

## 필수 시크릿 (Org-level Actions secret 권장)
- `PIKI_BOT_TOKEN`: source repo `contents:read` + wiki repo `contents:write` 권한이 있는 GitHub 토큰.
- `GEMINI_API_KEY`: Gemini API key. `wiki` 의 ingest workflow가 LLM 호출 시 사용.

## `piki init` 실행 시 로컬 GITHUB_TOKEN 권한
- source repo / `wiki` 양쪽에 `contents:write`
- `wiki` 에 `actions:write` (init `--bootstrap` 단계가 workflow_dispatch 호출)

## 워크플로우
- 트리거 (source side): `piki/.github/workflows/piki-sync.yml` — `pull_request.closed` + `merged == true` + `base.ref == main` → `repository_dispatch` to `wiki`.
- ingest (wiki side): `wiki/.github/workflows/piki-ingest.yml` — `repository_dispatch` 또는 `workflow_dispatch` 로 실행.

Organization: `cmux-aim-netlog`
