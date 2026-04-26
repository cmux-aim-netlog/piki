import base64
import json
import urllib.error
import urllib.request

import typer
from rich.console import Console

console = Console()


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


def _wiki_dispatch_workflow() -> str:
    return """name: piki-repo-dispatch

on:
  repository_dispatch:
    types:
      - piki_source_repo_merged

permissions:
  contents: read

jobs:
  receive-merge-event:
    if: github.event.client_payload.pr_merged == true && github.event.client_payload.base_ref == 'main'
    runs-on: ubuntu-latest
    steps:
      - name: Print received payload
        run: |
          echo "Received merge event from source repo."
          echo "org=${{ github.event.client_payload.org }}"
          echo "repo=${{ github.event.client_payload.repo }}"
          echo "sha=${{ github.event.client_payload.sha }}"
          echo "pr_number=${{ github.event.client_payload.pr_number }}"
          echo "pr_url=${{ github.event.client_payload.pr_url }}"
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


def init(
    org: str = typer.Option("cmux-aim-netlog", help="GitHub organization name."),
    wiki_repo: str = typer.Option("wiki", help="Single wiki repository name."),
    source_repos: str = typer.Option("Test_BE,Test_FE,piki", help="Comma-separated source repos."),
    token: str = typer.Option(..., envvar="GITHUB_TOKEN", help="GitHub token with contents write permissions."),
    wiki_branch: str = typer.Option("main", help="Wiki repository branch."),
    base_branch: str = typer.Option("main", help="Default source repository branch."),
    force_overwrite: bool = typer.Option(False, help="Overwrite existing files."),
    dry_run: bool = typer.Option(False, help="Print plan only without writing files."),
):
    """Initialize org single wiki and PR-merge triggers."""
    repos = [name.strip() for name in source_repos.split(",") if name.strip()]
    if not repos:
        console.print("[red]No valid source repositories.[/]")
        raise typer.Exit(1)

    has_error = False
    console.print(f"\n[bold]Initializing wiki repository[/] {org}/{wiki_repo}")

    wiki_files = [
        ("README.md", _wiki_readme(org, wiki_repo), "chore(piki): initialize wiki repository"),
        ("CLAUDE.md", _wiki_schema(org, wiki_repo), "chore(piki): add wiki schema"),
        ("index.md", _wiki_index(org, wiki_repo), "chore(piki): add wiki index"),
        ("log.md", _wiki_log(org), "chore(piki): add wiki sync log"),
        ("meta/file-page-index.json", "{}\n", "chore(piki): add initial file-page index"),
        ("meta/stale.md", "# Stale Pages\n\n- (none)\n", "chore(piki): add stale tracker"),
        ("meta/orphans.md", "# Orphan Pages\n\n- (none)\n", "chore(piki): add orphan tracker"),
        (
            ".github/workflows/piki-repo-dispatch.yml",
            _wiki_dispatch_workflow(),
            "chore(piki): add repository_dispatch ingest trigger",
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
            result = _upsert_file(
                owner=org,
                repo=repo,
                file_path=".github/workflows/piki-sync.yml",
                branch=base_branch,
                content=_source_workflow(org, repo, wiki_repo, wiki_branch),
                commit_message="chore(piki): add PR-to-main sync trigger workflow",
                token=token,
                force_overwrite=force_overwrite,
                dry_run=dry_run,
            )
            console.print(f"[green]✓[/] {org}/{repo}:.github/workflows/piki-sync.yml => {result}")
        except Exception as exc:  # pylint: disable=broad-except
            has_error = True
            console.print(f"[red]Failed source setup[/] {org}/{repo}: {exc}")

    if has_error:
        raise typer.Exit(1)
    console.print("\n[bold green]Done.[/] piki init completed.")
