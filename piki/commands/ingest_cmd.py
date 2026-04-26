"""LLM-driven ingest. Designed to run inside the wiki repo's GitHub Actions
workflow on `repository_dispatch` from a source repo's PR-merge event.

Inputs (env / flags):
- GEMINI_API_KEY            (required)
- GITHUB_TOKEN              (required; needs cross-repo read + wiki write)
- GITHUB_EVENT_PATH         (when triggered by repository_dispatch)
- PIKI_ORG / PIKI_SOURCE_REPO / PIKI_HEAD_SHA  (overrides for manual runs)

Pipeline:
1. Resolve org / source_repo / head_sha (event payload or flags).
2. Fetch changed files (compare API; falls back to tree snapshot for cold start).
3. Clone wiki repo.
4. Read wiki/piki.md + wiki/CLAUDE.md as system prompt; existing repos/<name>/* as state.
5. Single Gemini call (responseMimeType=application/json) → {pages: [{path, content}], log_entry}.
6. Write pages, append log.md, commit, push.
"""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console

console = Console()


# ---------- helpers ----------

def _read_template(name: str) -> str:
    return resources.files("piki.templates").joinpath(name).read_text(encoding="utf-8")


def _gh_get(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_changed_files(org: str, repo: str, base: str, head: str, token: str) -> list[dict]:
    url = f"https://api.github.com/repos/{org}/{repo}/compare/{base}...{head}"
    data = _gh_get(url, token)
    skip_suffixes = (".lock", ".min.js", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2")
    files = []
    for f in data.get("files", [])[:30]:
        if f["filename"].endswith(skip_suffixes):
            continue
        files.append({
            "path": f["filename"],
            "status": f["status"],
            "patch": (f.get("patch") or "")[:1500],
        })
    return files


def _fetch_repo_tree(org: str, repo: str, ref: str, token: str) -> list[str]:
    url = f"https://api.github.com/repos/{org}/{repo}/git/trees/{ref}?recursive=1"
    data = _gh_get(url, token)
    excluded = ("/node_modules/", "/dist/", "/build/", "/.git/", "/__pycache__/")
    return [
        t["path"] for t in data.get("tree", [])
        if t["type"] == "blob"
        and not any(seg in f"/{t['path']}/" for seg in excluded)
    ]


def _clone_wiki(org: str, wiki_repo: str, token: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    auth_url = f"https://x-access-token:{token}@github.com/{org}/{wiki_repo}.git"
    subprocess.run(
        ["git", "clone", "--depth=10", auth_url, str(dest)],
        check=True, capture_output=True, text=True,
    )


def _read_wiki_state(wiki_dir: Path, source_repo: str) -> dict[str, str]:
    repo_dir = wiki_dir / "repos" / source_repo
    state: dict[str, str] = {}
    if repo_dir.exists():
        for p in repo_dir.rglob("*.md"):
            rel = str(p.relative_to(wiki_dir))
            state[rel] = p.read_text(encoding="utf-8", errors="ignore")
    return state


def _call_gemini(api_key: str, model: str, system: str, user: str) -> str:
    """Single generateContent call. responseMimeType=application/json forces valid JSON."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
            "temperature": 0.2,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API {exc.code}: {body}") from exc

    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        finish = candidates[0].get("finishReason", "?")
        raise RuntimeError(f"Gemini returned no text parts (finishReason={finish}): {data}")
    return parts[0].get("text", "")


# ---------- prompt ----------

USER_PROMPT_TEMPLATE = """\
# Source repo
`{source_repo}` @ `{head_sha}`

# Recent changes ({n_changes} entries)
{changes_block}

# Existing wiki pages for this repo
{state_block}

# Task
Generate or update wiki pages under `repos/{source_repo}/`.
Allowed page paths:
- `repos/{source_repo}/overview.md`
- `repos/{source_repo}/gotchas.md`     ← the most valuable type
- `repos/{source_repo}/conventions.md`
- `repos/{source_repo}/api.md`

Rules (also see CLAUDE.md schema):
- If existing pages remain accurate, OMIT them from output.
- Each factual claim must cite source: `> src: {source_repo}@{head_sha_short}:<path>#L<a>-L<b>`.
- If you cannot cite a claim, mark `[NEEDS HUMAN INPUT]` instead of inventing.
- Keep frontmatter: `---\\nrepo: {source_repo}\\nlast_synced_commit: {head_sha}\\n---`.

Output ONLY a JSON object. No prose. No code fences.
Schema:
{{
  "pages": [{{"path": "repos/{source_repo}/<file>.md", "content": "<full markdown>"}}],
  "log_entry": "one short line for log.md"
}}

If nothing should change, return `{{"pages": [], "log_entry": "no-op"}}`.
"""


def _changes_block(changes: list[dict]) -> str:
    if not changes:
        return "(no file changes)"
    parts = []
    for c in changes:
        head = f"### {c['path']} ({c['status']})"
        body = f"```diff\n{c['patch']}\n```" if c["patch"] else "(snapshot only — no diff)"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)


def _state_block(state: dict[str, str]) -> str:
    if not state:
        return "(none yet — this is a cold start. Bootstrap initial pages.)"
    parts = []
    for path, content in state.items():
        parts.append(f"## {path}\n```\n{content[:1800]}\n```")
    return "\n\n".join(parts)


def _build_system_prompt(pattern: str, schema: str) -> str:
    return (
        "You are the maintainer agent for a piki wiki. "
        "You write team knowledge pages from source-code changes.\n\n"
        "## Pattern (piki.md)\n" + pattern[:6000] +
        "\n\n## Schema (CLAUDE.md)\n" + schema[:3000]
    )


# ---------- command ----------

def ingest_pr(
    event_path: str = typer.Option("", envvar="GITHUB_EVENT_PATH"),
    org: str = typer.Option("", envvar="PIKI_ORG"),
    wiki_repo: str = typer.Option("wiki", envvar="PIKI_WIKI_REPO"),
    source_repo: str = typer.Option("", envvar="PIKI_SOURCE_REPO"),
    head_sha: str = typer.Option("", envvar="PIKI_HEAD_SHA"),
    base_sha: str = typer.Option("", envvar="PIKI_BASE_SHA"),
    gemini_key: str = typer.Option(..., envvar="GEMINI_API_KEY"),
    github_token: str = typer.Option(..., envvar="GITHUB_TOKEN"),
    model: str = typer.Option("gemini-2.5-pro", envvar="PIKI_MODEL"),
    push: bool = typer.Option(True, "--push/--no-push"),
):
    """Run one LLM ingest pass. Triggered by repository_dispatch in Actions."""
    # 1. Resolve event
    if event_path and Path(event_path).exists():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
            cp = event.get("client_payload", {}) or {}
            org = org or cp.get("org", "")
            source_repo = source_repo or cp.get("repo", "")
            head_sha = head_sha or cp.get("sha", "")
        except (json.JSONDecodeError, KeyError) as exc:
            console.print(f"[yellow]Could not parse event payload:[/] {exc}")

    if not (org and source_repo and head_sha):
        console.print("[red]Missing org / source_repo / head_sha (give via env or flags).[/]")
        raise typer.Exit(2)

    head_short = head_sha[:8]
    console.print(f"[bold cyan]piki ingest[/] {org}/{source_repo}@{head_short}")

    # 2. Fetch changes
    if base_sha:
        try:
            changes = _fetch_changed_files(org, source_repo, base_sha, head_sha, github_token)
            console.print(f"  fetched {len(changes)} changed files (base={base_sha[:8]})")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]compare API failed, falling back to tree snapshot:[/] {exc}")
            changes = []
    else:
        changes = []

    if not changes:
        tree = _fetch_repo_tree(org, source_repo, head_sha, github_token)
        changes = [{"path": p, "status": "snapshot", "patch": ""} for p in tree[:40]]
        console.print(f"  using tree snapshot ({len(changes)} files)")

    # 3. Clone wiki
    work = Path(tempfile.mkdtemp(prefix="piki-wiki-"))
    try:
        _clone_wiki(org, wiki_repo, github_token, work)
        console.print(f"  cloned wiki → {work}")

        # 4. Read pattern + schema + state
        pattern_path = work / "piki.md"
        schema_path = work / "CLAUDE.md"
        pattern = pattern_path.read_text(encoding="utf-8") if pattern_path.exists() else _read_template("piki.md")
        schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""
        state = _read_wiki_state(work, source_repo)
        console.print(f"  existing pages for {source_repo}: {len(state)}")

        # 5. LLM call
        system_prompt = _build_system_prompt(pattern, schema)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            source_repo=source_repo,
            head_sha=head_sha,
            head_sha_short=head_short,
            n_changes=len(changes),
            changes_block=_changes_block(changes),
            state_block=_state_block(state),
        )
        console.print(f"  calling Gemini ({model})...")
        raw = _call_gemini(gemini_key, model, system_prompt, user_prompt)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            console.print(f"[red]LLM returned invalid JSON:[/] {exc}")
            console.print("[dim]raw[:600]:[/] " + raw[:600])
            raise typer.Exit(3) from exc

        pages = result.get("pages", []) or []
        log_entry = result.get("log_entry", "") or f"{len(pages)} pages updated"

        if not pages:
            console.print("  [dim]no page updates[/]")
            return

        # 6. Apply
        for page in pages:
            rel = page.get("path", "").lstrip("/")
            content = page.get("content", "")
            if not rel or not content:
                continue
            if not rel.startswith(f"repos/{source_repo}/"):
                console.print(f"  [yellow]skip out-of-scope path:[/] {rel}")
                continue
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
            console.print(f"  [green]wrote[/] {rel}")

        # 7. Append log.md
        log_file = work / "log.md"
        log_text = log_file.read_text(encoding="utf-8") if log_file.exists() else "# Sync Log\n\n"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        log_text += f"- [{ts}] {source_repo}@{head_short}: {log_entry}\n"
        log_file.write_text(log_text, encoding="utf-8")

        # 8. Commit + push
        if push:
            env = os.environ | {
                "GIT_AUTHOR_NAME": "piki-bot",
                "GIT_AUTHOR_EMAIL": "piki-bot@users.noreply.github.com",
                "GIT_COMMITTER_NAME": "piki-bot",
                "GIT_COMMITTER_EMAIL": "piki-bot@users.noreply.github.com",
            }
            subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
            status = subprocess.run(
                ["git", "-C", str(work), "status", "--porcelain"],
                check=True, capture_output=True, text=True, env=env,
            ).stdout.strip()
            if not status:
                console.print("  [dim]wiki already up to date — nothing to commit[/]")
                return
            subprocess.run(
                ["git", "-C", str(work), "commit", "-m",
                 f"piki: ingest {source_repo}@{head_short} ({len(pages)} pages)"],
                check=True, env=env,
            )
            subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD"], check=True, env=env)
            console.print("[bold green]✓ pushed to wiki[/]")
        else:
            console.print(f"[yellow]--no-push: changes left in[/] {work}")
    finally:
        if push:
            shutil.rmtree(work, ignore_errors=True)
