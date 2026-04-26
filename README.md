# piki

팀 위키를 코딩 에이전트에게 연결하는 CLI 도구입니다.

## 설치

```bash
pipx install piki-cli
```

## 시작하기

```bash
# 1) piki 설치
pipx install piki-cli

# 2) skill + llm-wiki.md 설치 (현재 프로젝트 폴더에 복사)
piki install

# 3) GitHub Org 초기화 (단일 wiki repo + source repo 액션 설정)
#    GITHUB_TOKEN은 쉘 환경변수로만 주입 (코드/깃에 절대 저장 금지)
GITHUB_TOKEN=<token> piki init --org <org> --wiki-repo wiki --source-repos repo1,repo2 --dry-run
GITHUB_TOKEN=<token> piki init --org <org> --wiki-repo wiki --source-repos repo1,repo2

# 4) ingest 1회 실행 (graph-wiki 생성)
piki ingest

# 5) 의사결정 이력 로컬 포트로 보기
piki serve --port 8787
```

## 명령어

### bootstrap

```bash
piki init --org <org> --wiki-repo wiki --source-repos repo1,repo2 --dry-run
GITHUB_TOKEN=<token> piki init --org <org> --wiki-repo wiki --source-repos repo1,repo2
```

### skill

```bash
piki install                    # SKILL.md + llm-wiki.md 설치
piki install --target-dir .     # 특정 디렉터리에 설치
```

### wiki

```bash
piki setup                      # 위키 초기 설정 (~/.wiki/ 에 클론)
piki sync                       # 최신 위키 pull + 인덱스 재생성
piki ingest                     # pull + index + graph-wiki.md 생성
piki ingest --retries 2         # pull 실패 시 재시도 후 fallback
piki search <query>             # 전체 문서 전문 검색
piki read <path>                # 특정 페이지 읽기 (예: repos/auth-service/gotchas)
piki context <files...>         # 편집할 파일과 관련된 위키 페이지 조회
piki gotchas <repo>             # 해당 레포의 알려진 함정/금지 패턴 조회
piki adr [--topic <topic>]      # 아키텍처 결정 기록(ADR) 목록/검색
piki serve --port 8787          # 로컬 포트로 wiki 탐색
```

### config

```bash
piki config list                # 설정 전체 조회
piki config get <key>           # 특정 설정 값 조회
piki config set <key> <value>   # 설정 값 저장
piki config delete <key>        # 설정 키 삭제
piki config reset               # 설정 초기화
```

## 에이전트와 함께 사용하기

`SKILL.md`를 프로젝트 루트에 두면 Claude Code 등 코딩 에이전트가 코드 작성 전 자동으로 위키를 참조합니다.

```bash
# 파일 편집 전
piki context src/handlers/refund.ts

# 아키텍처/도메인 질문 전
piki search "payment v2 migration"

# 결제·인증 코드 작성 전
piki gotchas auth-service
```

## 프로젝트 구조

```
piki/
├── piki/
│   ├── __init__.py          # 버전
│   ├── main.py              # CLI 진입점
│   ├── config.py            # 설정 파일 읽기/쓰기
│   ├── commands/
│   │   ├── config_cmd.py    # config 명령어
│   │   ├── wiki_cmd.py      # wiki 명령어
│   │   ├── init_cmd.py      # org 초기화 명령어
│   │   └── skill_cmd.py     # skill 설치 명령어
│   └── wiki/
│       ├── __init__.py      # WIKI_DIR, WIKI_REPO 상수
│       ├── db.py            # SQLite FTS 인덱스 관리
│       └── render.py        # 터미널 렌더링
├── SKILL.md                 # 에이전트용 위키 참조 규칙
└── pyproject.toml
```
