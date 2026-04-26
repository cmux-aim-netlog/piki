from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from piki.wiki import WIKI_DIR

console = Console()


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip("\n")


def render_page(rel_path: str) -> bool:
    path = WIKI_DIR / rel_path
    if not path.exists():
        # try adding .md
        path = WIKI_DIR / (rel_path + ".md")
    if not path.exists():
        console.print(f"[red]Page not found:[/] {rel_path}")
        return False
    text = path.read_text()
    body = _strip_frontmatter(text)
    console.print(Panel(Markdown(body), title=f"[dim]{rel_path}[/]", border_style="blue"))
    return True


def render_results(results: list[dict], title: str = "Results") -> None:
    if not results:
        console.print("[dim]No results found.[/]")
        return
    from rich.table import Table
    table = Table("Page", "Repo", "Title", show_header=True, header_style="bold cyan")
    for r in results:
        table.add_row(r["path"], r.get("repo", ""), r.get("title", ""))
    console.print(Panel(table, title=f"[bold]{title}[/]", border_style="green"))


def render_search_results(results: list[dict]) -> None:
    if not results:
        console.print("[dim]No results found.[/]")
        return
    from rich.table import Table
    table = Table("Page", "Repo", "Snippet", show_header=True, header_style="bold cyan", show_lines=True)
    for r in results:
        table.add_row(r["path"], r.get("repo", ""), r.get("snippet", ""))
    console.print(table)
