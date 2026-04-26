import typer
from rich.console import Console
from rich.panel import Panel

from piki import __version__
from piki.commands.config_cmd import app as config_app
from piki.commands.wiki_cmd import app as wiki_app

app = typer.Typer(
    name="piki",
    help="piki — a CLI dev tool",
    add_completion=False,
)
console = Console()

app.add_typer(config_app, name="config")
app.add_typer(wiki_app, name="wiki")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(
            Panel(
                f"[bold green]piki[/] [dim]v{__version__}[/]\n\nRun [bold]piki --help[/] to see available commands.",
                expand=False,
            )
        )


@app.command()
def version():
    """Show version."""
    console.print(f"piki v{__version__}")
