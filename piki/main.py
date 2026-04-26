import typer
from rich.console import Console
from rich.panel import Panel

from piki import __version__
from piki.commands.config_cmd import app as config_app
from piki.commands import wiki_cmd
from piki.commands import init_cmd

app = typer.Typer(
    name="piki",
    help="piki — a CLI dev tool",
    add_completion=False,
)
console = Console()

app.add_typer(config_app, name="config")

for _cmd in [
    init_cmd.init,
    wiki_cmd.setup,
    wiki_cmd.sync,
    wiki_cmd.search,
    wiki_cmd.read,
    wiki_cmd.context,
    wiki_cmd.gotchas,
    wiki_cmd.adr,
]:
    app.command()(_cmd)


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
