import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console

console = Console()


def _detect_current_repo_name() -> str | None:
    """Detect the local git repository name without hardcoded values."""
    try:
        top_level = (
            subprocess.check_output(["git", "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL, text=True)
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(top_level).name if top_level else None


def _load_pattern_doc() -> str:
    """Load piki.md (the pattern/constitution doc) bundled with the package."""
    return resources.files("piki.templates").joinpath("piki.md").read_text(encoding="utf-8")


def _github_request(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode("utf-8")
            return response.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError:
            parsed = {"message": text}
        return exc.code, parsed


def _get_file_sha(owner: str, repo: str, file_path: str, branch: str, token: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}?ref={branch}"
    status, response = _github_request("GET", url, token)
    if status == 200:
        return response.get("sha")
    if status == 404:
        return None
    raise RuntimeError(f"Failed to read {owner}/{repo}:{file_path} ({status}) -> {response}")


def _list_org_repos(org: str, token: str) -> list[str]:
    page = 1
    repos: list[str] = []
    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}&type=all"
        status, response = _github_request("GET", url, token)
        if status != 200:
            raise RuntimeError(f"Failed to list repos for org {org} ({status}) -> {response}")
        if not response:
            break
        for repo in response:
            if repo.get("archived") or repo.get("disabled"):
                continue
            name = repo.get("name", "").strip()
            if name:
                repos.append(name)
        if len(response) < 100:
            break
        page += 1
    return sorted(set(repos))


def _get_branch_sha(org: str, repo: str, branch: str, token: str) -> str | None:
    url = f"https://api.github.com/repos/{org}/{repo}/branches/{branch}"
    status, response = _github_request("GET", url, token)
    if status == 200:
        return response.get("commit", {}).get("sha")
    if status == 404:
        return None
    raise RuntimeError(f"Failed to read branch {org}/{repo}@{branch} ({status}) -> {response}")


def _trigger_ingest_workflow(
    org: str,
    wiki_repo: str,
    source_repo: str,
    head_sha: str,
    wiki_branch: str,
    token: str,
    workflow_file: str = "piki-ingest.yml",
) -> None:
    """Fire workflow_dispatch on the wiki repo's piki-ingest workflow.

    Requires the caller's token to have `actions:write` on the wiki repo.
    """
    url = f"https://api.github.com/repos/{org}/{wiki_repo}/actions/workflows/{workflow_file}/dispatches"
    payload = {
        "ref": wiki_branch,
        "inputs": {
            "org": org,
            "source_repo": source_repo,
            "head_sha": head_sha,
        },
    }
    status, response = _github_request("POST", url, token, payload)
    if status not in (200, 204):
        raise RuntimeError(
            f"Failed to dispatch ingest for {source_repo} ({status}) -> {response}. "
            "Token may need `actions:write` on the wiki repo."
        )


def _upsert_file(
    owner: str,
    repo: str,
    file_path: str,
    branch: str,
    content: str,
    commit_message: str,
    token: str,
    force_overwrite: bool,
    dry_run: bool,
) -> str:
    if dry_run:
        console.print(f"[dim][DRY-RUN][/dim] {owner}/{repo}:{file_path} (branch={branch})")
        return "planned"

    existing_sha = _get_file_sha(owner, repo, file_path, branch, token)
    if existing_sha and not force_overwrite:
        return "skipped"

    payload = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    status, response = _github_request("PUT", url, token, payload)
    if status not in (200, 201):
        raise RuntimeError(f"Failed to write {owner}/{repo}:{file_path} ({status}) -> {response}")
    return "updated" if existing_sha else "created"


def _wiki_readme(org: str, wiki_repo: str) -> str:
    return (
        f"# piki Wiki for {org}\n\n"
        f"이 저장소(`{wiki_repo}`)는 조직 단위 단일 위키입니다.\n"
        f"`{wiki_repo}` 외 source repo는 위키 파일을 직접 갖지 않습니다.\n\n"
        "## Sync\n"
        "- source repo의 PR(main 대상) merge 이벤트를 GitHub `repository_dispatch`로 수신\n"
        "- 외부 서버 없이 GitHub Actions만으로 동작\n"
    )


def _wiki_schema(org: str, wiki_repo: str) -> str:
    return (
        "# piki Schema\n\n"
        f"Organization: `{org}`\n"
        f"Wiki repository: `{wiki_repo}`\n\n"
        "## Rules\n"
        "- 이 위키는 조직 단위 단일 위키로 운영한다.\n"
        "- source repo는 수정하지 않고, 컨텍스트 결과만 이 위키에 반영한다.\n"
        "- 사실 진술은 출처를 남긴다.\n"
        "- 확신이 없으면 `[NEEDS HUMAN INPUT]`로 표기한다.\n"
    )


def _wiki_index(org: str, wiki_repo: str) -> str:
    return (
        "# Wiki Index\n\n"
        f"Org: `{org}`\n"
        f"Wiki repo: `{wiki_repo}`\n\n"
        "## Repositories\n- (to be populated)\n\n"
        "## Concepts\n- (to be populated)\n\n"
        "## Decisions\n- (to be populated)\n"
    )


def _wiki_log(org: str) -> str:
    return f"# Sync Log\n\n- init: wiki scaffold created for `{org}`\n"


def _wiki_gitattributes() -> str:
    """Tell git to union-merge log.md so parallel ingest workers don't
    conflict on append-only files during rebase. Without this, two
    simultaneous ingests both appending a sync line to log.md will conflict
    when the loser tries to rebase, breaking the whole pass."""
    return (
        "# piki: parallel ingest workers append to log.md from the same base.\n"
        "# Union merge keeps both lines instead of forcing a manual conflict.\n"
        "log.md merge=union\n"
    )


def _wiki_dispatch_workflow() -> str:
    return """name: piki-ingest

on:
  repository_dispatch:
    types:
      - piki_source_repo_merged
  workflow_dispatch:
    inputs:
      org:
        description: "Source repo's organization"
        required: true
      source_repo:
        description: "Source repository name (under the org)"
        required: true
      head_sha:
        description: "Head commit SHA to ingest"
        required: true
      base_sha:
        description: "Base commit SHA (optional; omit for snapshot mode)"
        required: false
        default: ""

permissions:
  contents: write

# NOTE: deliberately no `concurrency:` group.
# GitHub Actions only keeps 1 running + 1 pending per concurrency group, and any
# 3rd dispatch evicts the previous pending one (silently cancelled). Bootstrap
# fires N dispatches at once; with the queue, the middle (N-2) get dropped.
# Instead we let dispatches run in parallel and rely on `piki ingest-pr`'s
# pull-rebase-retry-on-push loop to serialize wiki commits.
jobs:
  ingest:
    runs-on: ubuntu-latest
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (github.event.client_payload.pr_merged == true || github.event.client_payload.pr_merged == 'true') &&
      (github.event.client_payload.base_ref == 'main' || github.event.client_payload.base_ref == 'master')
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install piki
        run: pip install --quiet "git+https://github.com/cmux-aim-netlog/piki.git@main"
      - name: Run ingest
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          # PIKI_BOT_TOKEN must allow read on source repo + write on this wiki repo.
          GITHUB_TOKEN: ${{ secrets.PIKI_BOT_TOKEN }}
          PIKI_WIKI_REPO: ${{ github.event.repository.name }}
          # repository_dispatch path: env vars below stay empty and ingest_pr will
          # parse GITHUB_EVENT_PATH. workflow_dispatch path: explicit inputs below.
          PIKI_ORG: ${{ github.event.inputs.org }}
          PIKI_SOURCE_REPO: ${{ github.event.inputs.source_repo }}
          PIKI_HEAD_SHA: ${{ github.event.inputs.head_sha }}
          PIKI_BASE_SHA: ${{ github.event.inputs.base_sha }}
        run: piki ingest-pr
"""


def _wiki_concepts_workflow() -> str:
    """Workflow that runs cross-repo concept extraction. Auto-triggered after
    every successful piki-ingest run via `workflow_run`, plus manual dispatch."""
    return """name: piki-concepts

on:
  workflow_dispatch: {}
  workflow_run:
    workflows: [piki-ingest]
    types: [completed]

permissions:
  contents: write

# One concepts pass at a time; if a new ingest finishes while we run,
# cancel + restart so we always converge on the latest wiki state.
concurrency:
  group: piki-concepts
  cancel-in-progress: true

jobs:
  concepts:
    runs-on: ubuntu-latest
    # Skip if the upstream ingest run failed or was cancelled.
    if: >-
      github.event_name == 'workflow_dispatch' ||
      github.event.workflow_run.conclusion == 'success'
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install piki
        run: pip install --quiet "git+https://github.com/cmux-aim-netlog/piki.git@main"
      - name: Run concept extraction
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.PIKI_BOT_TOKEN }}
          PIKI_ORG: ${{ github.repository_owner }}
          PIKI_WIKI_REPO: ${{ github.event.repository.name }}
        run: piki ingest-concepts
"""


def _source_workflow(org: str, repo: str, wiki_repo: str, wiki_branch: str) -> str:
    return f"""name: piki-sync-trigger

on:
  pull_request:
    branches:
      - main
    types:
      - closed

permissions:
  contents: read

jobs:
  notify-piki:
    if: github.event.pull_request.merged == true && github.event.pull_request.base.ref == 'main'
    runs-on: ubuntu-latest
    steps:
      - name: Dispatch merge event to wiki repository
        env:
          GH_TOKEN: "${{{{ secrets.PIKI_BOT_TOKEN }}}}"
          REPOSITORY: "${{{{ github.repository }}}}"
          REPO: "{repo}"
          ORG: "{org}"
          WIKI_REPO: "{wiki_repo}"
          WIKI_BRANCH: "{wiki_branch}"
          SHA: "${{{{ github.event.pull_request.merge_commit_sha }}}}"
          REF: "${{{{ github.event.pull_request.base.ref }}}}"
          EVENT_NAME: "${{{{ github.event_name }}}}"
          PR_NUMBER: "${{{{ github.event.pull_request.number }}}}"
          PR_MERGED: "${{{{ github.event.pull_request.merged }}}}"
          BASE_REF: "${{{{ github.event.pull_request.base.ref }}}}"
          HEAD_REF: "${{{{ github.event.pull_request.head.ref }}}}"
          PR_TITLE: "${{{{ github.event.pull_request.title }}}}"
          PR_URL: "${{{{ github.event.pull_request.html_url }}}}"
        run: |
          if [ -z "$GH_TOKEN" ]; then
            echo "PIKI_BOT_TOKEN secret is not configured."
            exit 1
          fi

          payload=$(cat <<EOF
          {{
            "org": "$ORG",
            "repo": "$REPO",
            "repository": "$REPOSITORY",
            "sha": "$SHA",
            "ref": "$REF",
            "event": "$EVENT_NAME",
            "pr_number": "$PR_NUMBER",
            "pr_merged": "$PR_MERGED",
            "base_ref": "$BASE_REF",
            "head_ref": "$HEAD_REF",
            "pr_title": "$PR_TITLE",
            "pr_url": "$PR_URL"
          }}
          EOF
          )

          curl -sS -X POST "https://api.github.com/repos/$ORG/$WIKI_REPO/dispatches" \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer $GH_TOKEN" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            -H "Content-Type: application/json" \
            -d "{{
              \\"event_type\\": \\"piki_source_repo_merged\\",
              \\"client_payload\\": $payload
            }}"
"""


def _action_guide_md(org: str, repo: str, wiki_repo: str) -> str:
    return (
        f"# GitHub Action Guide ({repo})\n\n"
        "이 파일은 `piki init`으로 자동 생성되었습니다.\n\n"
        "## 목적\n"
        f"- `{repo}` 에서 `main` 대상 PR merge 시 `{wiki_repo}` 저장소로 이벤트를 전달합니다.\n\n"
        "## 필수 시크릿 (Org-level Actions secret 권장)\n"
        "- `PIKI_BOT_TOKEN`: source repo `contents:read` + wiki repo `contents:write` 권한이 있는 GitHub 토큰.\n"
        f"- `GEMINI_API_KEY`: Gemini API key. `{wiki_repo}` 의 ingest workflow가 LLM 호출 시 사용.\n\n"
        "## `piki init` 실행 시 로컬 GITHUB_TOKEN 권한\n"
        f"- source repo / `{wiki_repo}` 양쪽에 `contents:write`\n"
        f"- `{wiki_repo}` 에 `actions:write` (init `--bootstrap` 단계가 workflow_dispatch 호출)\n\n"
        "## 워크플로우\n"
        f"- 트리거 (source side): `{repo}/.github/workflows/piki-sync.yml` — `pull_request.closed` + `merged == true` + `base.ref == main` → `repository_dispatch` to `{wiki_repo}`.\n"
        f"- ingest (wiki side): `{wiki_repo}/.github/workflows/piki-ingest.yml` — `repository_dispatch` 또는 `workflow_dispatch` 로 실행.\n\n"
        f"Organization: `{org}`\n"
    )


def _print_token_guide() -> None:
    from rich.panel import Panel
    from rich.text import Text

    guide = Text()
    guide.append("GITHUB_TOKEN 이 없습니다.\n\n", style="bold red")
    guide.append("GitHub Personal Access Token (PAT) 발급 방법:\n\n", style="bold")
    guide.append("1. ", style="bold cyan")
    guide.append("https://github.com/settings/tokens\n")
    guide.append("   → 우측 상단 프로필 → Settings → Developer settings → Personal access tokens\n\n")
    guide.append("2. ", style="bold cyan")
    guide.append("Fine-grained tokens → Generate new token\n\n")
    guide.append("3. ", style="bold cyan")
    guide.append("권한 설정:\n")
    guide.append("   Repository access: All repositories (또는 wiki + source repos 선택)\n")
    guide.append("   Permissions → Contents: Read and write\n\n")
    guide.append("4. ", style="bold cyan")
    guide.append("발급된 토큰을 환경변수로 주입:\n")
    guide.append("   GITHUB_TOKEN=<token> piki init --org <org> ...\n\n", style="green")
    guide.append("⚠️  토큰은 코드나 git에 절대 저장하지 마세요.\n", style="yellow")

    console.print(Panel(guide, title="[bold]GitHub Token 설정 가이드[/]", border_style="red"))


def init(
    org: str = typer.Option("cmux-aim-netlog", help="GitHub organization name."),
    wiki_repo: str = typer.Option("wiki", help="Single wiki repository name."),
    source_repos: str = typer.Option("", help="Comma-separated source repos. Empty means all org repos."),
    token: str = typer.Option("", envvar="GITHUB_TOKEN", help="GitHub token with contents write permissions."),
    wiki_branch: str = typer.Option("main", help="Wiki repository branch."),
    base_branch: str = typer.Option("main", help="Default source repository branch."),
    sync_source_files: bool = typer.Option(
        True,
        "--sync-source-files/--no-sync-source-files",
        help="Always update managed files in source repos (.github/workflows/piki-sync.yml, .github/GITHUB_ACTION.md).",
    ),
    bootstrap: bool = typer.Option(
        True,
        "--bootstrap/--no-bootstrap",
        help=(
            "After scaffold + workflow setup, fire workflow_dispatch on the wiki's "
            "piki-ingest workflow for each source repo at its current main HEAD so "
            "the wiki has real content immediately (no need to wait for a PR). "
            "Requires GEMINI_API_KEY and PIKI_BOT_TOKEN as Org Actions secrets, and "
            "the local GITHUB_TOKEN to have actions:write on the wiki repo."
        ),
    ),
    force_overwrite: bool = typer.Option(False, help="Overwrite existing files."),
    dry_run: bool = typer.Option(False, help="Print plan only without writing files."),
):
    """Initialize org single wiki and PR-merge triggers."""
    if not token or not token.strip():
        _print_token_guide()
        raise typer.Exit(1)

    if source_repos.strip():
        repos = [name.strip() for name in source_repos.split(",") if name.strip()]
    else:
        repos = [r for r in _list_org_repos(org, token) if r != wiki_repo]
        console.print(f"[cyan]Auto-detected source repos[/]: {', '.join(repos) if repos else '(none)'}")

    excluded_repos = {wiki_repo}
    current_repo = _detect_current_repo_name()
    if current_repo:
        excluded_repos.add(current_repo)
    filtered_repos = [repo for repo in repos if repo not in excluded_repos]
    skipped_repos = sorted(set(repos) - set(filtered_repos))
    if skipped_repos:
        console.print(
            "[yellow]Skipping managed repos[/]: "
            + ", ".join(skipped_repos)
            + " (workflow files are not created/updated there)"
        )
    repos = filtered_repos

    if not repos:
        console.print("[red]No valid source repositories.[/]")
        raise typer.Exit(1)

    has_error = False
    console.print(f"\n[bold]Initializing wiki repository[/] {org}/{wiki_repo}")

    wiki_files = [
        ("README.md", _wiki_readme(org, wiki_repo), "chore(piki): initialize wiki repository"),
        (".gitattributes", _wiki_gitattributes(), "chore(piki): union-merge log.md to survive parallel ingest"),
        ("piki.md", _load_pattern_doc(), "chore(piki): add piki pattern doc (constitution)"),
        ("CLAUDE.md", _wiki_schema(org, wiki_repo), "chore(piki): add wiki schema"),
        ("index.md", _wiki_index(org, wiki_repo), "chore(piki): add wiki index"),
        ("log.md", _wiki_log(org), "chore(piki): add wiki sync log"),
        ("meta/file-page-index.json", "{}\n", "chore(piki): add initial file-page index"),
        ("meta/stale.md", "# Stale Pages\n\n- (none)\n", "chore(piki): add stale tracker"),
        ("meta/orphans.md", "# Orphan Pages\n\n- (none)\n", "chore(piki): add orphan tracker"),
        (
            ".github/workflows/piki-ingest.yml",
            _wiki_dispatch_workflow(),
            "chore(piki): add LLM ingest workflow (repository_dispatch + workflow_dispatch)",
        ),
        (
            ".github/workflows/piki-concepts.yml",
            _wiki_concepts_workflow(),
            "chore(piki): add cross-repo concepts workflow (workflow_run on piki-ingest)",
        ),
    ]

    try:
        for file_path, content, message in wiki_files:
            result = _upsert_file(
                owner=org,
                repo=wiki_repo,
                file_path=file_path,
                branch=wiki_branch,
                content=content,
                commit_message=message,
                token=token,
                force_overwrite=force_overwrite,
                dry_run=dry_run,
            )
            console.print(f"[green]✓[/] {org}/{wiki_repo}:{file_path} => {result}")
    except Exception as exc:  # pylint: disable=broad-except
        has_error = True
        console.print(f"[red]Failed wiki init[/] {org}/{wiki_repo}: {exc}")

    for repo in repos:
        try:
            source_force_overwrite = force_overwrite or sync_source_files
            workflow_result = _upsert_file(
                owner=org,
                repo=repo,
                file_path=".github/workflows/piki-sync.yml",
                branch=base_branch,
                content=_source_workflow(org, repo, wiki_repo, wiki_branch),
                commit_message="chore(piki): add PR-to-main sync trigger workflow",
                token=token,
                force_overwrite=source_force_overwrite,
                dry_run=dry_run,
            )
            guide_result = _upsert_file(
                owner=org,
                repo=repo,
                file_path=".github/GITHUB_ACTION.md",
                branch=base_branch,
                content=_action_guide_md(org, repo, wiki_repo),
                commit_message="docs(piki): add GitHub Action usage guide",
                token=token,
                force_overwrite=source_force_overwrite,
                dry_run=dry_run,
            )
            console.print(
                f"[green]✓[/] {org}/{repo}:.github/workflows/piki-sync.yml => {workflow_result}, "
                f".github/GITHUB_ACTION.md => {guide_result}"
            )
        except Exception as exc:  # pylint: disable=broad-except
            has_error = True
            console.print(f"[red]Failed source setup[/] {org}/{repo}: {exc}")

    # Phase 3: bootstrap initial ingest so the wiki has real content
    # immediately, instead of staying empty until someone merges a PR.
    if bootstrap and repos and not has_error:
        console.print(f"\n[bold]Bootstrap: triggering initial ingest for {len(repos)} source repo(s)[/]")
        actions_url = f"https://github.com/{org}/{wiki_repo}/actions/workflows/piki-ingest.yml"
        for repo in repos:
            try:
                if dry_run:
                    console.print(f"[dim][DRY-RUN][/dim] would dispatch ingest for {org}/{repo}@{base_branch}")
                    continue
                sha = _get_branch_sha(org, repo, base_branch, token)
                if not sha:
                    console.print(f"[yellow]skip[/] {repo}: no `{base_branch}` branch")
                    continue
                _trigger_ingest_workflow(
                    org=org,
                    wiki_repo=wiki_repo,
                    source_repo=repo,
                    head_sha=sha,
                    wiki_branch=wiki_branch,
                    token=token,
                )
                console.print(f"[green]✓[/] dispatched ingest for {repo}@{sha[:8]}")
            except Exception as exc:  # pylint: disable=broad-except
                # Non-fatal: init succeeded; bootstrap is a best-effort convenience.
                console.print(f"[yellow]bootstrap skipped for {repo}[/]: {exc}")
        if not dry_run:
            console.print(f"  [dim]watch progress:[/] {actions_url}")
    elif bootstrap and has_error:
        console.print("[yellow]Skipping bootstrap because earlier setup steps failed.[/]")

    if has_error:
        raise typer.Exit(1)
    console.print("\n[bold green]Done.[/] piki init completed.")
    if bootstrap and not dry_run and repos:
        console.print(
            "[dim]The wiki will populate over the next 1–2 minutes as each ingest "
            "workflow finishes. Run `piki setup` (or `piki sync`) to pull the result.[/]"
        )
