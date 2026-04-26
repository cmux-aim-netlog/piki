# GitHub Action Guide (piki)

이 파일은 `piki init`으로 자동 생성되었습니다.

## 목적
- `piki` 에서 `main` 대상 PR merge 시 `wiki` 저장소로 이벤트를 전달합니다.

## 필수 시크릿
- `PIKI_BOT_TOKEN`: `repo` 권한이 있는 GitHub 토큰

## 워크플로우
- 파일 경로: `.github/workflows/piki-sync.yml`
- 트리거: `pull_request.closed` + `merged == true` + `base.ref == main`

Organization: `cmux-aim-netlog`
