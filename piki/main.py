import typer
from rich.console import Console
from rich.panel import Panel

from piki import __version__
from piki.commands.config_cmd import app as config_app
from piki.commands import wiki_cmd
from piki.commands import init_cmd
from piki.commands import skill_cmd

app = typer.Typer(
    name="piki",
    help="piki — a CLI dev tool",
    add_completion=False,
)
wiki_app = typer.Typer(help="Wiki related commands.")
console = Console()

app.add_typer(config_app, name="config")
wiki_app.command(name="init")(init_cmd.init)
app.add_typer(wiki_app, name="wiki")

for _cmd in [
    init_cmd.init,
    skill_cmd.install,
    wiki_cmd.setup,
    wiki_cmd.sync,
    wiki_cmd.ingest,
    wiki_cmd.search,
    wiki_cmd.read,
    wiki_cmd.context,
    wiki_cmd.gotchas,
    wiki_cmd.adr,
    wiki_cmd.serve,
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
