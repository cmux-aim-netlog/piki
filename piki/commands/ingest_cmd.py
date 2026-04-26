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
import re
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
    _ensure_gitattributes(dest)


def _ensure_gitattributes(work: Path) -> None:
    """Idempotently declare `log.md merge=union` so parallel ingest workers
    don't conflict on append-only files during rebase.

    Two places, on purpose:

    1. `.git/info/attributes` (local, never committed) — picked up by the
       rebase that may happen below in this same ingest. Without this, a
       fresh clone from an older wiki repo (which doesn't yet have a
       committed `.gitattributes`) would still hit the conflict on the
       very first parallel run.
    2. `.gitattributes` (committed, persisted in wiki repo) — ensures the
       attribute survives across future fresh clones.
    """
    needed_line = "log.md merge=union"

    info_attr = work / ".git" / "info" / "attributes"
    info_attr.parent.mkdir(parents=True, exist_ok=True)
    existing_info = info_attr.read_text(encoding="utf-8") if info_attr.exists() else ""
    if needed_line not in existing_info:
        sep = "\n" if existing_info and not existing_info.endswith("\n") else ""
        info_attr.write_text(existing_info + sep + needed_line + "\n", encoding="utf-8")

    ga = work / ".gitattributes"
    if ga.exists():
        text = ga.read_text(encoding="utf-8")
        if needed_line in text:
            return
        ga.write_text(text.rstrip() + "\n" + needed_line + "\n", encoding="utf-8")
    else:
        ga.write_text(
            "# piki: union-merge append-only files to survive parallel ingest.\n"
            + needed_line + "\n",
            encoding="utf-8",
        )


def _read_wiki_state(wiki_dir: Path, source_repo: str) -> dict[str, str]:
    """Pages currently in the wiki for *this* source repo (full content)."""
    repo_dir = wiki_dir / "repos" / source_repo
    state: dict[str, str] = {}
    if repo_dir.exists():
        for p in repo_dir.rglob("*.md"):
            rel = str(p.relative_to(wiki_dir))
            state[rel] = p.read_text(encoding="utf-8", errors="ignore")
    return state


def _read_neighbor_summaries(wiki_dir: Path, source_repo: str) -> dict[str, str]:
    """Compact neighbor context: overview.md of every OTHER repo in the wiki.
    Lets the LLM cross-reference (e.g. Test_BE ingest can link to Test_FE)."""
    repos_dir = wiki_dir / "repos"
    if not repos_dir.exists():
        return {}
    summaries: dict[str, str] = {}
    for d in sorted(repos_dir.iterdir()):
        if not d.is_dir() or d.name == source_repo:
            continue
        ov = d / "overview.md"
        if ov.exists():
            body = _strip_backlink_block(ov.read_text(encoding="utf-8", errors="ignore"))
            summaries[f"repos/{d.name}/overview.md"] = body[:1500]
    return summaries


def _call_gemini(api_key: str, model: str, system: str, user: str) -> str:
    """Single generateContent call. responseMimeType=application/json forces valid JSON."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 65536,
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

# Other repos in this org (overview only — for cross-references)
{neighbors_block}

# Task
Generate or update wiki pages under `repos/{source_repo}/`.
Allowed page paths:
- `repos/{source_repo}/overview.md`
- `repos/{source_repo}/gotchas.md`     ← the most valuable type
- `repos/{source_repo}/conventions.md`
- `repos/{source_repo}/api.md`
- `repos/{source_repo}/files/<relative-path>.md`   ← one page per significant source file
- `repos/{source_repo}/symbols/<ClassName-or-function_name>.md`  ← one page per key class/function

Rules (also see CLAUDE.md schema):
- If existing pages remain accurate, OMIT them from output.
- Each factual claim must cite source: `> src: {source_repo}@{head_sha_short}:<path>#L<a>-L<b>`.
- If you cannot cite a claim, mark `[NEEDS HUMAN INPUT]` instead of inventing.
- Keep frontmatter: `---\\nrepo: {source_repo}\\nlast_synced_commit: {head_sha}\\n---`.
- Create a `files/<relative-path>.md` page for each **changed file** that has meaningful logic (skip config, lock, asset files). Link it from `overview.md` or `api.md`.
- Create a `symbols/<Name>.md` page for each **key class or function** introduced or significantly changed. Link it from the corresponding `files/` page.
- Every `files/` and `symbols/` page MUST contain a `관련` section with relative markdown links back to its parent pages (overview, api, or the file page that contains the symbol), so backlinks are auto-generated.

**Cross-repo links (important — this builds the wiki graph)**:
- When this repo's code clearly interacts with another repo (calls its API,
  shares its types, depends on its build artifacts, follows its conventions),
  add a relative markdown link to that repo's wiki page in your prose, e.g.
  `[Test_FE overview](../Test_FE/overview.md)` or
  `[Test_BE api](../Test_BE/api.md)`.
- These links power the auto-generated Backlinks section on the linked page.
  Without links, the wiki is a flat directory of strangers; with links, it's
  a graph an agent can traverse.
- Do NOT invent links to repos that don't appear in the "Other repos" block
  above. Only link to neighbors that actually exist.

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
        "When you reference another wiki page in your output, ALWAYS use a "
        "relative markdown link, e.g. `[Test_FE overview](../Test_FE/overview.md)` "
        "or `[Auth flow](../../concepts/authentication-flow.md)`. These links are "
        "what builds the wiki graph that downstream agents navigate.\n\n"
        "## Pattern (piki.md)\n" + pattern[:6000] +
        "\n\n## Schema (CLAUDE.md)\n" + schema[:3000]
    )


# ---------- backlinks (code, no LLM) ----------

WIKI_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md[^)]*)\)")
BL_START = "<!-- piki:backlinks-start -->"
BL_END = "<!-- piki:backlinks-end -->"


def _strip_backlink_block(text: str) -> str:
    if BL_START not in text:
        return text
    before = text.split(BL_START)[0].rstrip()
    after_parts = text.split(BL_END, 1)
    after = after_parts[1] if len(after_parts) > 1 else ""
    return before + after


def _normalize_link(source_md: Path, raw_target: str, wiki_dir: Path) -> str | None:
    target = raw_target.split("#")[0].split("?")[0].strip()
    if not target.endswith(".md"):
        return None
    if target.startswith(("http://", "https://", "mailto:")):
        return None
    try:
        resolved = (source_md.parent / target).resolve()
        rel = resolved.relative_to(wiki_dir.resolve())
    except (ValueError, OSError):
        return None
    return str(rel)


def _inject_backlinks(wiki_dir: Path) -> int:
    """Scan all *.md, build forward link map (page → set of pages it links to),
    invert to backward map, then rewrite a `## Backlinks` block on each page.
    Idempotent: previous block is stripped before computing.
    Returns number of pages rewritten."""
    pages = sorted(p for p in wiki_dir.rglob("*.md") if ".git" not in p.parts)
    titles: dict[str, str] = {}
    forward: dict[str, set[str]] = {}

    for p in pages:
        rel = str(p.relative_to(wiki_dir))
        text = _strip_backlink_block(p.read_text(encoding="utf-8", errors="ignore"))
        title = rel
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip() or rel
                break
        titles[rel] = title
        targets: set[str] = set()
        for m in WIKI_LINK_RE.finditer(text):
            norm = _normalize_link(p, m.group(2), wiki_dir)
            if norm and norm != rel:
                targets.add(norm)
        forward[rel] = targets

    backward: dict[str, set[str]] = {}
    for src, targets in forward.items():
        for tgt in targets:
            backward.setdefault(tgt, set()).add(src)

    updated = 0
    for p in pages:
        rel = str(p.relative_to(wiki_dir))
        text = p.read_text(encoding="utf-8", errors="ignore")
        stripped = _strip_backlink_block(text).rstrip()
        bls = sorted(backward.get(rel, set()))
        if bls:
            link_lines = []
            for src in bls:
                rel_path = os.path.relpath(wiki_dir / src, p.parent)
                link_lines.append(f"- [{titles[src]}]({rel_path})")
            block = (
                f"\n\n{BL_START}\n## Backlinks\n_Pages that link to this one (auto-generated)._\n\n"
                + "\n".join(link_lines)
                + f"\n{BL_END}\n"
            )
        else:
            block = "\n"
        new_text = stripped + block
        if new_text != text:
            p.write_text(new_text, encoding="utf-8")
            updated += 1
    return updated


# ---------- commit + push helper (race-rebase) ----------

def _commit_and_push(work: Path, message: str) -> bool:
    """Commit staged changes and push with rebase-on-reject retry.
    Returns True if pushed, False if nothing to commit."""
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
        return False
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", message],
        check=True, env=env,
    )
    push_cmd = ["git", "-C", str(work), "push", "origin", "HEAD"]
    for attempt in range(1, 6):
        result = subprocess.run(push_cmd, env=env, capture_output=True, text=True)
        if result.returncode == 0:
            if attempt > 1:
                console.print(f"  [dim](push succeeded on attempt {attempt})[/]")
            return True
        stderr = (result.stderr or "").strip()
        if "rejected" not in stderr.lower() and "non-fast-forward" not in stderr.lower():
            raise subprocess.CalledProcessError(result.returncode, push_cmd, stderr=stderr)
        if attempt == 5:
            raise subprocess.CalledProcessError(result.returncode, push_cmd, stderr=stderr)
        console.print(f"  [yellow]push rejected (attempt {attempt}/5) — pulling rebase[/]")
        subprocess.run(
            ["git", "-C", str(work), "pull", "--rebase", "--autostash", "origin", "main"],
            check=True, env=env, capture_output=True, text=True,
        )
    return True


# ---------- commands ----------

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
        neighbors = _read_neighbor_summaries(work, source_repo)
        console.print(
            f"  existing pages for {source_repo}: {len(state)} | "
            f"neighbor repo overviews: {len(neighbors)}"
        )

        # 5. LLM call
        system_prompt = _build_system_prompt(pattern, schema)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            source_repo=source_repo,
            head_sha=head_sha,
            head_sha_short=head_short,
            n_changes=len(changes),
            changes_block=_changes_block(changes),
            neighbors_block=_state_block(neighbors),
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

        # 8. Backlinks: deliberately NOT run here.
        # Per-repo ingests run in parallel; backlink blocks live in shared
        # pages (index.md, README.md, neighbor overviews). If two ingests
        # both rewrite a backlink block from the same base, rebase
        # conflicts and `_commit_and_push` cannot recover. The concepts
        # workflow (auto-triggered after each ingest via `workflow_run`,
        # cancel-in-progress=true so only one runs at a time) regenerates
        # backlinks idempotently from the latest wiki state. So the per-
        # repo commit only touches `repos/<self>/*` (no overlap) and
        # `log.md` (union-merge handles overlap).

        # 9. Commit + push
        if push:
            pushed = _commit_and_push(
                work, f"piki: ingest {source_repo}@{head_short} ({len(pages)} pages)"
            )
            if pushed:
                console.print("[bold green]✓ pushed to wiki[/]")
            else:
                console.print("  [dim]wiki already up to date — nothing to commit[/]")
        else:
            console.print(f"[yellow]--no-push: changes left in[/] {work}")
    finally:
        if push:
            shutil.rmtree(work, ignore_errors=True)


# ---------- cross-repo concepts pass ----------

CONCEPTS_USER_PROMPT = """\
You are extracting **cross-repo concepts** for the `{org}` organization wiki.
The wiki has per-repo pages under `repos/<repo-name>/`. Your job is to
identify knowledge that **spans multiple repos** and capture it under
`concepts/<topic>.md` so downstream agents can navigate the graph.

# All repo pages in the wiki
{repos_block}

# Existing cross-repo concept pages
{concepts_block}

# Your task

Generate or update `concepts/<topic>.md` files. Each concept page captures:
- A flow that touches multiple repos (e.g. authentication-flow, payment-flow,
  notification-flow).
- A shared domain model with implementations across repos (e.g. tenant-model,
  user-identity, billing-domain).
- A contract / API boundary between two or more repos.
- A naming or convention rule that should hold across repos.

Rules — non-negotiable:
- Each concept page MUST contain markdown links to the repo pages it touches,
  e.g. `[Test_BE overview](../repos/Test_BE/overview.md)` and
  `[Test_FE api](../repos/Test_FE/api.md)`. Use **relative paths** from
  `concepts/<file>.md`. These links build the wiki graph.
- Cite source code as `> src: <repo>@<sha-short>:<path>#L<a>-L<b>` OR link
  to a wiki page that does.
- If you cannot find evidence for a claim, mark `[NEEDS HUMAN INPUT]`.
- Frontmatter:
  ---
  type: concept
  last_synced_at: {ts}
  links_to: [<list of repo names this concept touches>]
  ---
- If an existing concept page is still accurate, OMIT it from output.
- If you cannot identify any meaningful cross-repo concept, return empty pages.

Output ONLY a JSON object. No prose. No code fences.
{{
  "pages": [{{"path": "concepts/<topic>.md", "content": "<full markdown>"}}],
  "log_entry": "one short summary line"
}}
"""


def _collect_repos_block(wiki_dir: Path) -> tuple[str, list[str]]:
    """Build a compact summary of all per-repo pages for the concepts prompt.
    Returns (block_text, list_of_repo_names_seen)."""
    repos_dir = wiki_dir / "repos"
    if not repos_dir.exists():
        return "(no repos/ pages found yet)", []
    parts = []
    repo_names = []
    for repo_dir in sorted(d for d in repos_dir.iterdir() if d.is_dir()):
        repo_names.append(repo_dir.name)
        section = [f"## repos/{repo_dir.name}/"]
        for page_name in ("overview.md", "api.md", "gotchas.md"):
            page = repo_dir / page_name
            if page.exists():
                body = _strip_backlink_block(page.read_text(encoding="utf-8", errors="ignore"))
                section.append(f"### {page_name}\n```\n{body[:2500]}\n```")
        parts.append("\n".join(section))
    return ("\n\n---\n\n".join(parts) if parts else "(no repo pages)"), repo_names


def _collect_concepts_block(wiki_dir: Path) -> str:
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return "(none yet)"
    parts = []
    for p in sorted(concepts_dir.glob("*.md")):
        body = _strip_backlink_block(p.read_text(encoding="utf-8", errors="ignore"))
        parts.append(f"## {p.name}\n```\n{body[:2000]}\n```")
    return "\n\n".join(parts) if parts else "(none yet)"


def ingest_concepts(
    org: str = typer.Option(..., envvar="PIKI_ORG"),
    wiki_repo: str = typer.Option("wiki", envvar="PIKI_WIKI_REPO"),
    gemini_key: str = typer.Option(..., envvar="GEMINI_API_KEY"),
    github_token: str = typer.Option(..., envvar="GITHUB_TOKEN"),
    model: str = typer.Option("gemini-2.5-pro", envvar="PIKI_MODEL"),
    push: bool = typer.Option(True, "--push/--no-push"),
):
    """Cross-repo concept extraction. Reads all repos/* pages and produces
    or updates concepts/*.md so the wiki has graph edges between repos.
    Run after per-repo ingest_pr passes (workflow_run trigger handles this
    automatically in the deployed setup)."""
    console.print(f"[bold cyan]piki ingest-concepts[/] {org}/{wiki_repo}")
    work = Path(tempfile.mkdtemp(prefix="piki-concepts-"))
    try:
        _clone_wiki(org, wiki_repo, github_token, work)

        repos_block, repo_names = _collect_repos_block(work)
        if not repo_names:
            console.print("[yellow]No repos/ pages yet — nothing to extract concepts from. Skipping.[/]")
            return
        concepts_block = _collect_concepts_block(work)
        console.print(f"  read {len(repo_names)} repos: {', '.join(repo_names)}")

        pattern_path = work / "piki.md"
        schema_path = work / "CLAUDE.md"
        pattern = pattern_path.read_text(encoding="utf-8") if pattern_path.exists() else _read_template("piki.md")
        schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""

        ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        user_prompt = CONCEPTS_USER_PROMPT.format(
            org=org, repos_block=repos_block, concepts_block=concepts_block, ts=ts_iso,
        )

        console.print(f"  calling Gemini ({model}) for cross-repo concept extraction...")
        raw = _call_gemini(gemini_key, model, _build_system_prompt(pattern, schema), user_prompt)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            console.print(f"[red]LLM returned invalid JSON:[/] {exc}")
            console.print("[dim]raw[:600]:[/] " + raw[:600])
            raise typer.Exit(3) from exc

        pages = result.get("pages", []) or []
        log_entry = result.get("log_entry", "") or f"{len(pages)} concept(s) updated"

        if not pages:
            console.print("  [dim]no new concepts identified[/]")
        else:
            for page in pages:
                rel = page.get("path", "").lstrip("/")
                content = page.get("content", "")
                if not rel or not content:
                    continue
                if not (rel.startswith("concepts/") and rel.endswith(".md")):
                    console.print(f"  [yellow]skip out-of-scope path:[/] {rel}")
                    continue
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
                console.print(f"  [green]wrote[/] {rel}")

            log_file = work / "log.md"
            log_text = log_file.read_text(encoding="utf-8") if log_file.exists() else "# Sync Log\n\n"
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            log_text += f"- [{ts}] concepts pass: {log_entry}\n"
            log_file.write_text(log_text, encoding="utf-8")

        # Always re-inject backlinks (concepts pass might have created new edges)
        n_bl = _inject_backlinks(work)
        console.print(f"  [dim]backlinks updated on {n_bl} page(s)[/]")

        if push:
            pushed = _commit_and_push(work, f"piki: concepts pass ({len(pages)} pages, backlinks refreshed)")
            console.print("[bold green]✓ pushed concepts[/]" if pushed else "  [dim]nothing to commit[/]")
        else:
            console.print(f"[yellow]--no-push:[/] {work}")
    finally:
        if push:
            shutil.rmtree(work, ignore_errors=True)
