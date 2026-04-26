import subprocess
from pathlib import Path

import typer
from rich.console import Console

from piki.wiki import WIKI_DIR, WIKI_REPO, db, render

app = typer.Typer(help="piki wiki — team context hub for coding agents.")
console = Console()


def _require_wiki():
    if not WIKI_DIR.exists():
        console.print("[red]Wiki not set up.[/] Run [bold]piki wiki setup[/] first.")
        raise typer.Exit(1)


@app.command()
def setup():
    """Clone the wiki repo to ~/.wiki/ and build the search index."""
    if WIKI_DIR.exists():
        console.print("[yellow]Wiki already set up.[/] Run [bold]piki wiki sync[/] to update.")
        raise typer.Exit(0)
    console.print(f"[bold]Cloning wiki[/] → {WIKI_DIR}")
    result = subprocess.run(["git", "clone", WIKI_REPO, str(WIKI_DIR)], capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]Clone failed:[/]\n{result.stderr}")
        raise typer.Exit(1)
    console.print("[dim]Building search index...[/]")
    db.build_index()
    console.print("[green]✓[/] Wiki ready. Try [bold]piki wiki search <query>[/]")


@app.command()
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


@app.command()
def search(query: str):
    """Full-text search across all wiki pages."""
    _require_wiki()
    results = db.search(query)
    render.render_search_results(results)


@app.command()
def read(path: str):
    """Read a wiki page. Example: repos/auth-service/gotchas"""
    _require_wiki()
    render.render_page(path)


@app.command()
def context(files: list[str] = typer.Argument(..., help="Files you are about to edit.")):
    """Show wiki pages relevant to the files you're editing."""
    _require_wiki()
    results = db.context_for_files(files)
    if not results:
        # fall back to filename-based FTS search
        for f in files:
            stem = Path(f).stem
            results += db.search(stem, limit=3)
    render.render_results(results, title=f"Context for {', '.join(files)}")
    if results:
        console.print("\n[dim]Run [bold]piki wiki read <path>[/] to open a page.[/]")


@app.command()
def gotchas(repo: str):
    """Show known traps and deprecated patterns for a repo."""
    _require_wiki()
    render.render_page(f"repos/{repo}/gotchas")


@app.command()
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
