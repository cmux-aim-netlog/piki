import subprocess
from pathlib import Path
from datetime import datetime

import typer
from rich.console import Console

from piki.wiki import WIKI_DIR, WIKI_REPO, db, render

console = Console()


def _require_wiki():
    if not WIKI_DIR.exists():
        console.print("[red]Wiki not set up.[/] Run [bold]piki setup[/] first.")
        raise typer.Exit(1)


def setup():
    """Clone the wiki repo to ~/.wiki/ and build the search index."""
    if WIKI_DIR.exists():
        console.print("[yellow]Wiki already set up.[/] Run [bold]piki sync[/] to update.")
        raise typer.Exit(0)
    console.print(f"[bold]Cloning wiki[/] → {WIKI_DIR}")
    result = subprocess.run(["git", "clone", WIKI_REPO, str(WIKI_DIR)], capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]Clone failed:[/]\n{result.stderr}")
        raise typer.Exit(1)
    console.print("[dim]Building search index...[/]")
    db.build_index()
    console.print("[green]✓[/] Wiki ready. Try [bold]piki search <query>[/]")


def sync():
    """Pull latest changes and rebuild search index."""
    _require_wiki()
    console.print("[dim]Pulling latest wiki...[/]")
    result = subprocess.run(["git", "-C", str(WIKI_DIR), "pull"], capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]Pull failed:[/]\n{result.stderr}")
        raise typer.Exit(1)
    console.print(result.stdout.strip())
    console.print("[dim]Rebuilding index...[/]")
    db.build_index()
    console.print("[green]✓[/] Wiki synced.")


def ingest(
    retries: int = typer.Option(2, "--retries", help="How many times to retry git pull on failure."),
):
    """Ingest latest wiki and generate graph-wiki snapshot."""
    if not WIKI_DIR.exists():
        console.print("[yellow]Wiki not set up. Running setup first...[/]")
        setup()
        return

    pull_ok = False
    for attempt in range(1, retries + 2):
        result = subprocess.run(["git", "-C", str(WIKI_DIR), "pull"], capture_output=True, text=True)
        if result.returncode == 0:
            pull_ok = True
            if result.stdout.strip():
                console.print(result.stdout.strip())
            break
        console.print(f"[yellow]Pull failed (attempt {attempt})[/]: {result.stderr.strip()}")

    if not pull_ok:
        console.print("[yellow]Proceeding with local wiki only (pull failed).[/]")

    db.build_index()
    graph_path = WIKI_DIR / "graph-wiki.md"
    pages = sorted([p for p in WIKI_DIR.rglob("*.md") if ".git" not in p.parts])
    decisions = sorted((WIKI_DIR / "decisions").glob("*.md")) if (WIKI_DIR / "decisions").exists() else []
    concepts = sorted((WIKI_DIR / "concepts").glob("*.md")) if (WIKI_DIR / "concepts").exists() else []

    graph_lines = [
        "# graph-wiki",
        "",
        f"- generated_at: {datetime.utcnow().isoformat()}Z",
        f"- total_pages: {len(pages)}",
        f"- decisions_count: {len(decisions)}",
        f"- concepts_count: {len(concepts)}",
        "",
        "## decisions",
    ]
    graph_lines.extend([f"- {p.relative_to(WIKI_DIR)}" for p in decisions] or ["- (none)"])
    graph_lines.append("")
    graph_lines.append("## concepts")
    graph_lines.extend([f"- {p.relative_to(WIKI_DIR)}" for p in concepts] or ["- (none)"])
    graph_lines.append("")

    graph_path.write_text("\n".join(graph_lines) + "\n", encoding="utf-8")
    console.print(f"[green]✓[/] Ingest complete. Graph created: {graph_path}")
    if not pull_ok:
        console.print("[yellow]Warning[/] ingest ran in fallback mode due to pull failure.")


def search(query: str):
    """Full-text search across all wiki pages."""
    _require_wiki()
    results = db.search(query)
    render.render_search_results(results)


def read(path: str):
    """Read a wiki page. Example: repos/auth-service/gotchas"""
    _require_wiki()
    render.render_page(path)


def context(files: list[str] = typer.Argument(..., help="Files you are about to edit.")):
    """Show wiki pages relevant to the files you're editing."""
    _require_wiki()
    results = db.context_for_files(files)
    if not results:
        for f in files:
            stem = Path(f).stem
            results += db.search(stem, limit=3)
    render.render_results(results, title=f"Context for {', '.join(files)}")
    if results:
        console.print("\n[dim]Run [bold]piki read <path>[/] to open a page.[/]")


def gotchas(repo: str):
    """Show known traps and deprecated patterns for a repo."""
    _require_wiki()
    render.render_page(f"repos/{repo}/gotchas")


def adr(topic: str = typer.Option("", "--topic", "-t", help="Topic to search for.")):
    """List or search Architecture Decision Records."""
    _require_wiki()
    decisions_dir = WIKI_DIR / "decisions"
    if not decisions_dir.exists():
        console.print("[dim]No ADRs found.[/]")
        return
    if topic:
        results = db.search(topic)
        results = [r for r in results if r["path"].startswith("decisions/")]
        render.render_results(results, title=f"ADRs matching '{topic}'")
    else:
        pages = sorted(decisions_dir.glob("*.md"))
        if not pages:
            console.print("[dim]No ADRs yet.[/]")
            return
        from rich.table import Table
        table = Table("File", show_header=True, header_style="bold cyan")
        for p in pages:
            table.add_row(str(p.relative_to(WIKI_DIR)))
        console.print(table)


def serve(
    port: int = typer.Option(8787, "--port", "-p", help="Local port to serve the wiki directory."),
):
    """Serve local wiki directory to inspect decision history in browser."""
    _require_wiki()
    console.print(f"[bold]Serving wiki[/] {WIKI_DIR} at [cyan]http://127.0.0.1:{port}[/]")
    console.print("[dim]Press Ctrl+C to stop.[/]")
    raise typer.Exit(
        subprocess.call(
            ["python3", "-m", "http.server", str(port), "--directory", str(WIKI_DIR)]
        )
    )
