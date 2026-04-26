# piki

팀 위키를 코딩 에이전트에게 연결하는 CLI 도구입니다.

## 설치

```bash
pip install -e .
```

## 시작하기

```bash
piki setup    # 위키 클론 및 검색 인덱스 생성
```

## 명령어

### bootstrap

```bash
piki init --org cmux-aim-netlog --wiki-repo wiki --source-repos Test_BE,Test_FE,piki --dry-run
# 실제 반영 시:
# GITHUB_TOKEN=<token> piki init --org cmux-aim-netlog --wiki-repo wiki --source-repos Test_BE,Test_FE,piki
```

### wiki

```bash
piki setup                   # 위키 초기 설정 (~/.wiki/ 에 클론)
piki sync                    # 최신 위키 pull + 인덱스 재생성
piki search <query>          # 전체 문서 전문 검색
piki read <path>             # 특정 페이지 읽기 (예: repos/auth-service/gotchas)
piki context <files...>      # 편집할 파일과 관련된 위키 페이지 조회
piki gotchas <repo>          # 해당 레포의 알려진 함정/금지 패턴 조회
piki adr [--topic <topic>]   # 아키텍처 결정 기록(ADR) 목록/검색
```

### config

```bash
piki config list           # 설정 전체 조회
piki config get <key>      # 특정 설정 값 조회
piki config set <key> <value>   # 설정 값 저장
piki config delete <key>   # 설정 키 삭제
piki config reset          # 설정 초기화
```

## 에이전트와 함께 사용하기

`SKILL.md`를 프로젝트 루트에 두면 Claude Code 등 코딩 에이전트가 코드 작성 전 자동으로 위키를 참조합니다.

```bash
# 파일 편집 전
piki wiki context src/handlers/refund.ts

# 아키텍처/도메인 질문 전
piki wiki search "payment v2 migration"

# 결제·인증 코드 작성 전
piki wiki gotchas auth-service
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
│   │   └── wiki_cmd.py      # wiki 명령어
│   └── wiki/
│       ├── __init__.py      # WIKI_DIR, WIKI_REPO 상수
│       ├── db.py            # SQLite FTS 인덱스 관리
│       └── render.py        # 터미널 렌더링
├── SKILL.md                 # 에이전트용 위키 참조 규칙
└── pyproject.toml
```
